import json
import math
from collections import defaultdict
from datetime import date, datetime

from app_config import FLIGHTS, JST, probability_symbol
from flight_metadata import NON_OPERATED_STATUSES, OPERATED_STATUSES, normalize_status

MODEL_LABELS = {
    "jma_seamless": "JMA",
    "gfs_seamless": "GFS",
    "ecmwf_ifs025": "ECMWF",
}
WEEKDAYS = "月火水木金土日"

WEATHER_FIELDS = (
    ("wind_direction", "風向", "°"), ("wind_speed", "平均風速", "m/s"),
    ("wind_gusts", "最大瞬間風速", "m/s"), ("visibility", "視程", "km"),
    ("cloud_cover_low", "低層雲量", "%"), ("precipitation", "降水量", "mm/h"),
)


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _weather_details(primary, outcome):
    payload = _object(primary.get("weather_json"))
    weather = _object(payload.get("weather", payload))
    def cells(values):
        result = []
        for key, label, unit in WEATHER_FIELDS:
            value = values.get(key)
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            valid = valid and math.isfinite(value) and value >= 0
            result.append({"label": label, "value": f"{value:g} {unit}" if valid else "記録なし"})
        return result
    breakdown = _object(primary.get("factor_breakdown_json"))
    names = {"visibility": "視程", "southerly": "南風", "wind": "強風",
             "gust": "突風", "low_cloud": "低層雲", "precipitation": "降水"}
    factors = []
    for key, value in _object(breakdown.get("weather_factors")).items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            factors.append(f"{names.get(key, key)} ×{value:g}")
    typhoon = breakdown.get("typhoon_factor")
    if isinstance(typhoon, (int, float)) and not isinstance(typhoon, bool) and math.isfinite(typhoon):
        factors.append(f"台風 ×{typhoon:g}")
    return {
        "forecast": cells(weather),
        "collected": cells({key: outcome.get(f"collected_{key}") for key, _, _ in WEATHER_FIELDS}),
        "valid_at": _format_timestamp(primary.get("weather_valid_at")),
        "retrieved_at": _format_timestamp(primary.get("weather_retrieved_at")),
        "provider": primary.get("provider") or "出所記録なし",
        "config_version": primary.get("config_version") or "記録なし",
        "factors": " ／ ".join(factors) or "個別係数の記録なし",
    }


def _iso_date(value):
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _format_timestamp(value):
    if not value:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=JST)
    return value.astimezone(JST).strftime("%Y/%m/%d %H:%M")


def _score(value):
    if value is None:
        return None
    return round(float(value))


def _reflection(score, outcome):
    if score is None:
        return "公開時のJMA参考スコアを算出できなかったため、結果との比較は行いません。"
    if outcome not in OPERATED_STATUSES | NON_OPERATED_STATUSES:
        return "実際の運航結果が未取得のため、振り返りはまだ確定していません。"
    if outcome in OPERATED_STATUSES:
        if score >= 75:
            return "参考スコアは高めで、実際も運航しました。"
        if score < 60:
            return "参考スコアは慎重な値でしたが、実際は運航しました。スコアは運航可否を断定する値ではありません。"
        return "参考スコアは中間的な値で、実際は運航しました。"
    if score >= 75:
        return "参考スコアは高めでしたが、実際は欠航または引き返しでした。予測の限界が表れた事例です。"
    if score < 60:
        return "参考スコアは慎重な値で、実際も欠航または引き返しでした。"
    return "参考スコアは中間的な値で、実際は欠航または引き返しでした。"


def build_archive_days(rows):
    grouped = defaultdict(lambda: defaultdict(dict))
    outcomes = {}
    for row in rows:
        date_string = _iso_date(row["forecast_target_date"])
        flight_number = row["flight_number"]
        grouped[date_string][flight_number][row["model"]] = row
        if row.get("outcome_status") is not None:
            outcomes[(date_string, flight_number)] = row

    archive_days = []
    for date_string in sorted(grouped, reverse=True):
        parsed_date = date.fromisoformat(date_string)
        flights = []
        for spec in FLIGHTS:
            number = spec["number"]
            model_rows = grouped[date_string].get(number, {})
            primary = model_rows.get("jma_seamless")
            outcome_row = outcomes.get((date_string, number), {})
            outcome = normalize_status(outcome_row.get("outcome_status"))
            primary_score = _score(primary.get("probability")) if primary else None
            publication_status = (primary or {}).get("publication_status", "legacy")
            models = []
            for model_name, label in MODEL_LABELS.items():
                model_row = model_rows.get(model_name)
                value = _score(model_row.get("probability")) if model_row else None
                models.append(
                    {
                        "label": label,
                        "score": value,
                        "symbol": probability_symbol(value),
                        "status": model_row.get("calculation_status") if model_row else "missing",
                    }
                )
            flights.append(
                {
                    "number": number,
                    "time": spec["time"],
                    "score": primary_score,
                    "symbol": probability_symbol(primary_score),
                    "published_at": _format_timestamp(
                        primary.get("prediction_generated_at") if primary else None
                    ),
                    "publication_status": publication_status,
                    "publicly_confirmed": publication_status == "published",
                    "models": models,
                    "outcome": outcome,
                    "outcome_reason": outcome_row.get("status_reason"),
                    "outcome_confirmed": outcome in OPERATED_STATUSES | NON_OPERATED_STATUSES,
                    "outcome_class": "operated" if outcome in OPERATED_STATUSES else (
                        "disrupted" if outcome in NON_OPERATED_STATUSES else "pending"
                    ),
                    "outcome_label": "引き返し" if outcome == "条件付き→引返欠航" else outcome or "結果未取得",
                    "weather": _weather_details(primary or {}, outcome_row),
                    "reflection": (
                        "運営者の確認によると、南風の影響で欠航となりました。"
                        "南風が強い状況でしたが、参考スコアは高い値となっていました。"
                        "参考にしてくださった皆さまには、申し訳ありません。"
                        "今回の事例を踏まえ、南風の強さと風向に応じたリスク補正を追加しました。"
                        "当時の公開スコアは変更せず保存しています。"
                        if date_string == "2026-09-08" and number == "ANA1895"
                        and outcome in NON_OPERATED_STATUSES
                        else _reflection(primary_score, outcome)
                    ),
                }
            )
        confirmed = sum(flight["outcome_confirmed"] for flight in flights)
        operated = sum(flight["outcome"] in OPERATED_STATUSES for flight in flights)
        archive_days.append(
            {
                "date": date_string,
                "month": date_string[:7],
                "date_label": f"{parsed_date.month}/{parsed_date.day}",
                "long_date_label": f"{parsed_date.year}年{parsed_date.month}月{parsed_date.day}日",
                "weekday": WEEKDAYS[parsed_date.weekday()],
                "flights": flights,
                "confirmed_count": confirmed,
                "operated_count": operated,
                "cancelled_count": sum(f["outcome"] == "欠航" for f in flights),
                "returned_count": sum(f["outcome"] == "条件付き→引返欠航" for f in flights),
                "missing_count": len(flights) - confirmed,
                "result_class": "disrupted" if confirmed > operated else (
                    "operated" if confirmed == len(flights) else "pending"
                ),
                "publicly_confirmed": all(
                    flight["publicly_confirmed"] for flight in flights
                ),
                "last_modified": max(
                    (
                        row.get("prediction_generated_at")
                        for models in grouped[date_string].values()
                        for row in models.values()
                        if row.get("prediction_generated_at")
                    ),
                    default=None,
                ),
            }
        )
    return archive_days
