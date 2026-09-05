import argparse
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from google.cloud import bigquery

from app_config import JST
from bigquery_schema import RUNS_TABLE
from bigquery_storage import _collection_table_path, settings


def expected_collection_dates(today=None, days=14):
    today = today or datetime.now(JST).date()
    if isinstance(today, datetime):
        today = today.date()
    if not isinstance(today, date) or not isinstance(days, int) or days <= 0:
        raise ValueError("欠損日検知の日付範囲が正しくありません。")
    return [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def _timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stable_record_key(record):
    return tuple(sorted((str(key), str(value)) for key, value in record.items()))


def _event_key(record):
    completed_at = record.get("completed_at")
    event_time = completed_at or record.get("created_at") or record.get("started_at")
    status_rank = {"succeeded": 2, "failed": 1, "started": 0}.get(
        record.get("status"), 0
    )
    try:
        attempt = int(record.get("attempt") or 0)
    except (TypeError, ValueError):
        attempt = 0
    return (_timestamp(event_time), attempt, status_rank, _stable_record_key(record))


def _observed_count(observed_flight_counts, target):
    if not observed_flight_counts:
        return None
    value = observed_flight_counts.get(target)
    if value is None:
        value = observed_flight_counts.get(target.isoformat())
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def aggregate_collection_runs(records, expected_flights=3, observed_flight_counts=None):
    """Collapse event rows without relying on BigQuery/input order."""
    by_run = {}
    for record in records:
        run_id = record.get("run_id")
        if not run_id:
            run_id = f"unidentified-{_stable_record_key(record)}"
        try:
            attempt = int(record.get("attempt") or 0)
        except (TypeError, ValueError):
            attempt = 0
        by_run.setdefault((run_id, attempt), []).append(record)

    final_runs = []
    for events in by_run.values():
        final_runs.append(max(events, key=_event_key))

    grouped = {}
    for record in final_runs:
        target = _as_date(record.get("target_date"))
        if target is not None:
            grouped.setdefault(target, []).append(record)

    states = {}
    for target, runs in grouped.items():
        final = max(runs, key=_event_key)
        observed_count = _observed_count(observed_flight_counts, target)
        try:
            rows_written = int(final.get("rows_written"))
        except (TypeError, ValueError):
            rows_written = 0
        if final.get("status") == "succeeded" and rows_written >= expected_flights:
            state = (
                "data_missing"
                if observed_count is not None and observed_count < expected_flights
                else "succeeded"
            )
        elif final.get("status") == "succeeded":
            state = (
                "data_missing"
                if observed_count is not None and observed_count < expected_flights
                else "data_incomplete"
            )
        elif observed_count is not None and observed_count >= expected_flights:
            state = "success_record_missing"
        elif final.get("status") == "failed":
            state = "run_failed"
        else:
            state = "started_without_completion"
        states[target] = {
            "state": state,
            "final_run": final,
            "runs": runs,
            "observed_flight_count": observed_count,
        }
    return states


def find_missing_collection_days(
    records,
    today=None,
    days=14,
    expected_flights=3,
    observed_flight_counts=None,
    current_time=None,
    monitoring_start_date=None,
):
    summary = coverage_summary(
        records,
        today=today,
        days=days,
        expected_flights=expected_flights,
        observed_flight_counts=observed_flight_counts,
        current_time=current_time,
        monitoring_start_date=monitoring_start_date,
    )
    ignored_states = {"not_due", "not_recorded_before_monitoring"}
    return [
        target
        for target in expected_collection_dates(today, days)
        if summary["collection_states"].get(target.isoformat()) not in ignored_states
        and summary["collection_states"].get(target.isoformat()) != "succeeded"
    ]


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def coverage_summary(
    records,
    today=None,
    days=14,
    expected_flights=3,
    observed_flight_counts=None,
    monitoring_start_date=None,
    current_time=None,
):
    expected = expected_collection_dates(today, days)
    states = aggregate_collection_runs(
        records,
        expected_flights=expected_flights,
        observed_flight_counts=observed_flight_counts,
    )
    completed = {target for target, state in states.items() if state["state"] == "succeeded"}

    latest = max(
        (state["final_run"] for state in states.values()),
        key=_event_key,
        default=None,
    )
    current = current_time or datetime.now(JST)
    monitoring_start = _as_date(monitoring_start_date or os.getenv("COLLECTION_MONITOR_START_DATE"))
    collection_states = {}
    for target in expected:
        state = states.get(target)
        if state:
            collection_states[target.isoformat()] = state["state"]
        elif target == current.date() and current.hour < 21:
            collection_states[target.isoformat()] = "not_due"
        elif monitoring_start and target < monitoring_start:
            collection_states[target.isoformat()] = "not_recorded_before_monitoring"
        elif _observed_count(observed_flight_counts, target) is not None and _observed_count(
            observed_flight_counts, target
        ) >= expected_flights:
            collection_states[target.isoformat()] = "success_record_missing"
        else:
            collection_states[target.isoformat()] = "not_recorded"
    consecutive_missing = 0
    ignored_states = {"not_due", "not_recorded_before_monitoring"}
    for target in reversed(expected):
        state = collection_states[target.isoformat()]
        if state == "succeeded":
            break
        if state in ignored_states:
            continue
        consecutive_missing += 1
    return {
        "last_success_date": max(completed).isoformat() if completed else None,
        "consecutive_missing_days": consecutive_missing,
        "collection_states": collection_states,
        "latest_run": {
            "run_id": latest.get("run_id"),
            "target_date": str(latest.get("target_date")) if latest else None,
            "status": latest.get("status") if latest else None,
            "attempt": latest.get("attempt") if latest else None,
        }
        if latest
        else None,
    }


def fetch_collection_runs(today=None, days=14):
    expected = expected_collection_dates(today, days)
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    start = expected[0].isoformat()
    end = expected[-1].isoformat()
    query = f"""
        SELECT CAST(target_date AS STRING) AS target_date,
               status, rows_written, attempt, run_id, error_code, started_at
               , completed_at, created_at
        FROM `{_collection_table_path(RUNS_TABLE, config)}`
        WHERE target_date BETWEEN '{start}' AND '{end}'
    """
    return [dict(row.items()) for row in client.query(query).result()]


def fetch_observed_flight_counts(today=None, days=14):
    expected = expected_collection_dates(today, days)
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    query = f"""
        SELECT CAST(date AS STRING) AS target_date,
               COUNT(DISTINCT flight_number) AS flight_count
        FROM `{_collection_table_path(config['table'], config)}`
        WHERE date BETWEEN '{expected[0]}' AND '{expected[-1]}'
        GROUP BY date
    """
    return {
        row["target_date"]: int(row["flight_count"])
        for row in client.query(query).result()
    }


def format_report(
    missing_days,
    today=None,
    days=14,
    records=None,
    observed_flight_counts=None,
    current_time=None,
    monitoring_start_date=None,
):
    expected = expected_collection_dates(today, days)
    summary = coverage_summary(
        records or [],
        today=today,
        days=days,
        observed_flight_counts=observed_flight_counts,
        current_time=current_time,
        monitoring_start_date=monitoring_start_date,
    )
    lines = [
        "## 日次収集カバレッジ",
        "",
        f"対象期間: {expected[0]}〜{expected[-1]} ({days}日)",
        f"最終成功日: {summary['last_success_date'] or 'なし'}",
        f"連続欠損日数: {summary['consecutive_missing_days']}日",
    ]
    state_counts = {}
    for state in summary.get("collection_states", {}).values():
        state_counts[state] = state_counts.get(state, 0) + 1
    if state_counts:
        lines.append(
            "状態内訳: "
            + " / ".join(f"{state} {count}日" for state, count in sorted(state_counts.items()))
        )
    latest = summary["latest_run"]
    if latest:
        lines.append(
            f"最新run: {latest['run_id'] or '不明'} / {latest['target_date'] or '日付不明'} / "
            f"{latest['status'] or '状態不明'} / attempt {latest['attempt'] or '不明'}"
        )
    if not missing_days:
        lines.append(f"{expected[0]}〜{expected[-1]}の完了記録があります。")
        return "\n".join(lines)
    lines.extend(
        [
            "",
            f"完了記録がない日: {len(missing_days)}日",
            "",
        ]
    )
    lines.extend(f"- {target}" for target in missing_days)
    lines.extend(
        [
            "",
            "raw保存のrun_idを確認し、`python data_collector.py --replay-run-id <run_id>`で再生してください。",
        ]
    )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="日次収集の欠損日を検知する")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-missing", action="store_true")
    args = parser.parse_args()
    current_time = datetime.now(JST)
    records = fetch_collection_runs(days=args.days)
    observed_flight_counts = fetch_observed_flight_counts(days=args.days)
    missing = find_missing_collection_days(
        records,
        days=args.days,
        observed_flight_counts=observed_flight_counts,
        current_time=current_time,
        monitoring_start_date=os.getenv("COLLECTION_MONITOR_START_DATE"),
    )
    report = format_report(
        missing,
        days=args.days,
        records=records,
        observed_flight_counts=observed_flight_counts,
        current_time=current_time,
        monitoring_start_date=os.getenv("COLLECTION_MONITOR_START_DATE"),
    )
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    if missing and args.fail_on_missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
