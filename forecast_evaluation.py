import argparse
import json
import math
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean

from google.cloud import bigquery

from bigquery_schema import PREDICTION_SNAPSHOT_TABLE
from bigquery_storage import _collection_table_path, settings, table_path
from flight_metadata import (
    CANCELLATION_REASON_CATEGORIES,
    OPERATED_STATUSES,
    VALID_STORED_STATUSES,
    normalize_status,
)

UTC = timezone.utc
RELIABILITY_BIN_EDGES = tuple(range(0, 101, 10))


def _parse_timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _probability(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not 0 <= value <= 100:
        return None
    return value


def _outcome_label(row, conditional_operated=True):
    status = normalize_status(row.get("outcome_status") or row.get("status"))
    if status not in VALID_STORED_STATUSES:
        return None
    if status == "運航(条件付)" and not conditional_operated:
        return 0
    return 1 if status in OPERATED_STATUSES else 0


def partition_evaluable_predictions(rows, population="all", conditional_operated=True):
    if population not in {"all", "weather_only"}:
        raise ValueError("評価母集団が正しくありません。")
    eligible = []
    excluded = Counter()
    for row in rows:
        if row.get("provenance_status") != "known":
            excluded["unknown_provenance"] += 1
            continue
        if row.get("calculation_status") != "available":
            excluded["unavailable_calculation"] += 1
            continue
        probability = _probability(row.get("probability"))
        if probability is None:
            excluded["invalid_probability"] += 1
            continue
        generated_at = _parse_timestamp(row.get("prediction_generated_at"))
        valid_at = _parse_timestamp(row.get("weather_valid_at"))
        retrieved_at = _parse_timestamp(row.get("weather_retrieved_at"))
        if generated_at is None or valid_at is None:
            excluded["missing_prediction_time"] += 1
            continue
        if generated_at >= valid_at:
            excluded["prediction_after_valid_time"] += 1
            continue
        if retrieved_at and retrieved_at > generated_at:
            excluded["retrieval_after_prediction"] += 1
            continue
        outcome = _outcome_label(row, conditional_operated=conditional_operated)
        if outcome is None:
            excluded["unknown_outcome"] += 1
            continue
        if population == "weather_only":
            category = row.get("status_reason_category")
            if outcome == 0 and category != "weather":
                excluded["non_weather_or_unknown_cancellation"] += 1
                continue
        target_date = _parse_date(row.get("forecast_target_date"))
        if target_date is None:
            excluded["invalid_target_date"] += 1
            continue
        eligible.append(
            {
                **row,
                "probability": probability,
                "outcome": outcome,
                "target_date": target_date,
            }
        )
    return eligible, dict(excluded)


def brier_score(rows, predicted_key="probability", outcome_key="outcome"):
    if not rows:
        return None
    return round(
        mean(
            ((float(row[predicted_key]) / 100) - int(row[outcome_key])) ** 2
            for row in rows
        ),
        6,
    )


def _prior_rate(rows):
    return (mean(row["outcome"] for row in rows) * 100) if rows else None


def reliability_bins(rows):
    bins = []
    for lower in RELIABILITY_BIN_EDGES[:-1]:
        upper = lower + 10
        members = [
            row
            for row in rows
            if lower <= row["probability"] < upper
            or (upper == 100 and lower <= row["probability"] <= upper)
        ]
        bins.append(
            {
                "lower": lower,
                "upper": upper,
                "count": len(members),
                "mean_predicted_percent": round(mean(row["probability"] for row in members), 2)
                if members
                else None,
                "observed_rate_percent": round(_prior_rate(members), 2)
                if members
                else None,
            }
        )
    return bins


def expected_calibration_error(rows):
    if not rows:
        return None
    total = len(rows)
    error = 0.0
    for bucket in reliability_bins(rows):
        if bucket["count"]:
            error += bucket["count"] / total * abs(
                bucket["mean_predicted_percent"] - bucket["observed_rate_percent"]
            )
    return round(error, 4)


def metric_summary(rows):
    if not rows:
        return {
            "count": 0,
            "brier_score": None,
            "baseline_prior_percent": None,
            "baseline_prior_brier_score": None,
            "baseline_always_operated_brier_score": None,
            "expected_calibration_error_percent": None,
            "reliability": reliability_bins([]),
        }
    prior = _prior_rate(rows)
    prior_rows = [{**row, "probability": prior} for row in rows]
    always_rows = [{**row, "probability": 100.0} for row in rows]
    return {
        "count": len(rows),
        "brier_score": brier_score(rows),
        "baseline_prior_percent": round(prior, 2),
        "baseline_prior_brier_score": brier_score(prior_rows),
        "baseline_always_operated_brier_score": brier_score(always_rows),
        "expected_calibration_error_percent": expected_calibration_error(rows),
        "reliability": reliability_bins(rows),
    }


def _factor_breakdown(row):
    value = row.get("factor_breakdown_json")
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def factor_ablation_evaluation(rows):
    """Compare the recorded JMA factors on the same out-of-time population."""
    eligible, excluded = partition_evaluable_predictions(rows, population="all")
    weather_eligible, weather_excluded = partition_evaluable_predictions(
        rows, population="weather_only"
    )
    names = ("base", "weather_only", "typhoon_only", "combined")
    metrics = {}
    weather_metrics = {}
    missing = Counter(excluded)

    def build_population(population_rows):
        grouped = {name: [] for name in names}
        for row in population_rows:
            if row.get("model") != "jma_seamless":
                continue
            ablation = _factor_breakdown(row).get("ablation")
            if not isinstance(ablation, dict):
                missing["missing_factor_ablation"] += 1
                continue
            for name in names:
                probability = _probability(ablation.get(name))
                if probability is None:
                    missing[f"missing_factor_{name}"] += 1
                    continue
                grouped[name].append({**row, "probability": probability})
        return grouped

    for name, population_rows in build_population(eligible).items():
        metrics[name] = metric_summary(population_rows)
        metrics[name]["rolling_time"] = rolling_time_evaluation(population_rows)
    for name, population_rows in build_population(weather_eligible).items():
        weather_metrics[name] = metric_summary(population_rows)
        weather_metrics[name]["rolling_time"] = rolling_time_evaluation(population_rows)
    missing.update({f"weather_only_{key}": value for key, value in weather_excluded.items()})
    return {
        "status": "ok" if any(summary["count"] for summary in metrics.values()) else "insufficient_data",
        "model": "jma_seamless",
        "excluded_counts": dict(missing),
        "all": metrics,
        "weather_only": weather_metrics,
    }


def rolling_time_evaluation(rows, min_train_dates=3):
    grouped = {}
    for row in sorted(rows, key=lambda item: (item.get("target_date"), item.get("model", ""))):
        grouped.setdefault(row.get("target_date"), []).append(row)
    dates = [target for target in grouped if target is not None]
    folds = []
    for index, test_date in enumerate(dates):
        train_dates = dates[:index]
        if len(train_dates) < min_train_dates:
            continue
        train = [row for target in train_dates for row in grouped[target]]
        test = grouped[test_date]
        prior = _prior_rate(train)
        baseline = [{**row, "probability": prior} for row in test]
        folds.append(
            {
                "train_end": train_dates[-1].isoformat(),
                "test_date": test_date.isoformat(),
                "train_count": len(train),
                "test_count": len(test),
                "brier_score": brier_score(test),
                "baseline_prior_percent": round(prior, 2),
                "baseline_prior_brier_score": brier_score(baseline),
            }
        )
    return folds


def _grouped_metrics(rows, key_function):
    grouped = {}
    for row in rows:
        key = key_function(row)
        grouped.setdefault(key, []).append(row)
    return {
        str(key): metric_summary(grouped[key])
        for key in sorted(grouped, key=lambda value: str(value))
    }


def _lead_day(row):
    try:
        lead_hours = int(row.get("lead_hours"))
    except (TypeError, ValueError):
        return "unknown"
    return max(0, lead_hours) // 24


def _reason_coverage(rows, key_function):
    grouped = {}
    for row in rows:
        status = normalize_status(row.get("outcome_status") or row.get("status"))
        if status in OPERATED_STATUSES:
            continue
        key = key_function(row)
        grouped.setdefault(key, []).append(row)

    result = {}
    for key, members in sorted(grouped.items(), key=lambda item: str(item[0])):
        unknown_count = sum(
            1
            for row in members
            if row.get("status_reason_category") not in CANCELLATION_REASON_CATEGORIES
            or row.get("status_reason_category") == "unknown"
        )
        result[str(key)] = {
            "cancellation_count": len(members),
            "unknown_reason_count": unknown_count,
            "unknown_reason_rate_percent": round(unknown_count / len(members) * 100, 2),
            "categories": dict(
                Counter(
                    row.get("status_reason_category")
                    if row.get("status_reason_category") in CANCELLATION_REASON_CATEGORIES
                    else "unknown"
                    for row in members
                )
            ),
        }
    return result


def _model_metrics(rows):
    return {
        model: metric_summary([row for row in rows if row.get("model") == model])
        for model in sorted({row.get("model", "unknown") for row in rows})
    }


def evaluate_rows(rows, generated_at=None):
    eligible, excluded = partition_evaluable_predictions(rows, population="all")
    weather_eligible, weather_excluded = partition_evaluable_predictions(
        rows, population="weather_only"
    )
    models = {}
    for model in sorted({row.get("model", "unknown") for row in eligible}):
        models[model] = {
            "all": metric_summary([row for row in eligible if row.get("model") == model]),
            "weather_only": metric_summary(
                [row for row in weather_eligible if row.get("model") == model]
            ),
        }
    category_coverage = Counter(
        row.get("status_reason_category")
        if row.get("status_reason_category") in CANCELLATION_REASON_CATEGORIES
        else "unknown"
        for row in rows
        if normalize_status(row.get("outcome_status") or row.get("status"))
        not in OPERATED_STATUSES
    )
    conditional_as_non_operated, conditional_excluded = partition_evaluable_predictions(
        rows,
        population="all",
        conditional_operated=False,
    )
    return {
        "status": "ok" if eligible else "insufficient_data",
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "input_count": len(rows),
        "eligible_count": len(eligible),
        "excluded_counts": excluded,
        "weather_only_excluded_counts": weather_excluded,
        "category_coverage": dict(category_coverage),
        "weather_only_count": len(weather_eligible),
        "models": models,
        "by_flight": _grouped_metrics(eligible, lambda row: row.get("flight_number", "unknown")),
        "by_lead_day": _grouped_metrics(eligible, _lead_day),
        "reason_coverage_by_flight": _reason_coverage(
            rows, lambda row: row.get("flight_number", "unknown")
        ),
        "reason_coverage_by_period": _reason_coverage(
            rows, lambda row: row.get("forecast_target_date", row.get("target_date", "unknown"))
        ),
        "conditional_status_sensitivity": {
            "current_as_operated": {
                "eligible_count": len(eligible),
                "overall": metric_summary(eligible),
                "models": _model_metrics(eligible),
            },
            "as_non_operated": {
                "eligible_count": len(conditional_as_non_operated),
                "excluded_counts": conditional_excluded,
                "overall": metric_summary(conditional_as_non_operated),
                "models": _model_metrics(conditional_as_non_operated),
            },
        },
        "by_version": _grouped_metrics(
            eligible,
            lambda row: f"{row.get('code_version') or 'unknown'}@{row.get('config_version') or 'unknown'}",
        ),
        "factor_ablation": factor_ablation_evaluation(rows),
        "rolling_time": rolling_time_evaluation(eligible),
        "rolling_time_weather_only": rolling_time_evaluation(weather_eligible),
    }


def fetch_prediction_outcomes(lookback_days=365):
    if not isinstance(lookback_days, int) or lookback_days <= 0:
        raise ValueError("評価対象期間が正しくありません。")
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    query = f"""
        SELECT
          s.snapshot_id,
          s.forecast_target_date,
          s.flight_number,
          s.model,
          s.calculation_status,
          s.probability,
          s.lead_hours,
          s.factor_breakdown_json,
          s.code_version,
          s.config_version,
          s.prediction_generated_at,
          s.weather_retrieved_at,
          s.weather_valid_at,
          s.provenance_status,
          h.status AS outcome_status,
          h.status_reason_category,
          h.status_reason,
          h.status_reason_source,
          h.status_reason_observed_at,
          h.status_reason_confidence
        FROM `{_collection_table_path(PREDICTION_SNAPSHOT_TABLE, config)}` s
        JOIN `{table_path(config)}` h
          ON h.date = s.forecast_target_date
         AND h.flight_number = s.flight_number
        WHERE s.forecast_target_date >= DATE_SUB(CURRENT_DATE('Asia/Tokyo'), INTERVAL {lookback_days} DAY)
    """
    return [dict(row.items()) for row in client.query(query).result()]


def markdown_report(report):
    lines = [
        "# 予測値 外部検証レポート",
        "",
        f"- 状態: `{report['status']}`",
        f"- 入力行数: {report['input_count']}",
        f"- 評価対象行数: {report['eligible_count']}",
        "",
        "## 除外理由",
        "",
    ]
    if report["excluded_counts"]:
        lines.extend(
            f"- `{reason}`: {count}行"
            for reason, count in sorted(report["excluded_counts"].items())
        )
    else:
        lines.append("- なし")
    lines.extend(["", "## モデル別指標", ""])
    if not report["models"]:
        lines.append("評価可能な予測値がありません。来歴不明、算出不可、時系列条件違反を現在の精度とみなしていません。")
    else:
        for model, populations in report["models"].items():
            metrics = populations["all"]
            weather_metrics = populations["weather_only"]
            lines.extend(
                [
                    f"### {model}",
                    "",
                    f"- 件数: {metrics['count']}",
                    f"- Brier score: {metrics['brier_score']}",
                    f"- 学習期間内の運航率ベースライン: {metrics['baseline_prior_percent']}% / Brier {metrics['baseline_prior_brier_score']}",
                    f"- 常時運航ベースラインのBrier: {metrics['baseline_always_operated_brier_score']}",
                    f"- ECE: {metrics['expected_calibration_error_percent']}ポイント",
                    f"- 天候起因限定: {weather_metrics['count']}件 / Brier {weather_metrics['brier_score']} / ECE {weather_metrics['expected_calibration_error_percent']}ポイント",
                    "",
                ]
            )
    lines.extend(["## 便別・リード日別指標", ""])
    by_flight = report.get("by_flight", {})
    by_lead_day = report.get("by_lead_day", {})
    if by_flight:
        lines.append("### 便別")
        lines.extend(
            f"- `{flight}`: {metrics['count']}件 / Brier {metrics['brier_score']} / ECE {metrics['expected_calibration_error_percent']}"
            for flight, metrics in by_flight.items()
        )
    else:
        lines.append("- 便別に分解できる評価対象がありません。")
    if by_lead_day:
        lines.append("### リード日別")
        lines.extend(
            f"- `{lead_day}`日先: {metrics['count']}件 / Brier {metrics['brier_score']} / ECE {metrics['expected_calibration_error_percent']}"
            for lead_day, metrics in by_lead_day.items()
        )
    else:
        lines.append("- リード日別に分解できる評価対象がありません。")
    by_version = report.get("by_version", {})
    if by_version:
        lines.append("### コード・設定版別（変更前後比較用）")
        lines.extend(
            f"- `{version}`: {metrics['count']}件 / Brier {metrics['brier_score']} / ECE {metrics['expected_calibration_error_percent']}"
            for version, metrics in by_version.items()
        )
    sensitivity = report.get("conditional_status_sensitivity", {})
    if sensitivity:
        current = sensitivity.get("current_as_operated", {})
        alternative = sensitivity.get("as_non_operated", {})
        lines.extend(
            [
                "",
                "## 条件付運航の感度分析",
                "",
                f"- 現行定義（条件付運航を運航扱い）: {current.get('eligible_count', 0)}件",
                f"- 感度分析（条件付運航を非運航扱い）: {alternative.get('eligible_count', 0)}件",
            ]
        )
    lines.extend(["", "## 欠航理由の未知率", ""])
    reason_by_flight = report.get("reason_coverage_by_flight", {})
    if reason_by_flight:
        lines.extend(
            f"- `{flight}`: {summary['unknown_reason_count']}/{summary['cancellation_count']}件 "
            f"({summary['unknown_reason_rate_percent']}%)"
            for flight, summary in reason_by_flight.items()
        )
    else:
        lines.append("- 欠航実績がないため未集計")
    lines.extend(["## 時系列ローリング分割", ""])
    if report["rolling_time"]:
        lines.extend(
            f"- {fold['train_end']}までで学習、{fold['test_date']}を評価: {fold['test_count']}件 / Brier {fold['brier_score']} / baseline {fold['baseline_prior_brier_score']}"
            for fold in report["rolling_time"]
        )
    else:
        lines.append("- 学習期間を確保できるデータがないため未実施")
    lines.extend(["", "## 補正因子ablation（JMA）", ""])
    ablation = report["factor_ablation"]
    if ablation["status"] != "ok":
        lines.append("- 因子内訳を持つ評価可能なJMA予測がないため未実施")
    else:
        for name, metrics in ablation["all"].items():
            weather_metrics = ablation["weather_only"][name]
            lines.append(
                f"- `{name}`: {metrics['count']}件 / Brier {metrics['brier_score']} / "
                f"時系列fold {len(metrics['rolling_time'])} / "
                f"天候起因限定 {weather_metrics['count']}件・Brier {weather_metrics['brier_score']}"
            )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="公開予測値の時系列外部検証を行う")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--fail-on-insufficient-data", action="store_true")
    args = parser.parse_args()
    rows = fetch_prediction_outcomes(args.lookback_days)
    report = evaluate_rows(rows)
    markdown = markdown_report(report)
    if args.format == "json":
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    else:
        args.output.write_text(markdown, encoding="utf-8")
    if args.json_output:
        args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(markdown, end="")
    if args.fail_on_insufficient_data and report["status"] == "insufficient_data":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
