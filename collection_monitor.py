import argparse
from datetime import date, datetime, timedelta
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


def find_missing_collection_days(records, today=None, days=14, expected_flights=3):
    expected = expected_collection_dates(today, days)
    completed = set()
    for record in records:
        target = record.get("target_date")
        if isinstance(target, datetime):
            target = target.date()
        if isinstance(target, str):
            try:
                target = date.fromisoformat(target)
            except ValueError:
                continue
        rows_written = record.get("rows_written")
        try:
            enough_rows = rows_written is not None and int(rows_written) >= expected_flights
        except (TypeError, ValueError):
            enough_rows = False
        if target in expected and record.get("status") == "succeeded" and enough_rows:
            completed.add(target)
    return [target for target in expected if target not in completed]


def fetch_collection_runs(today=None, days=14):
    expected = expected_collection_dates(today, days)
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    start = expected[0].isoformat()
    end = expected[-1].isoformat()
    query = f"""
        SELECT CAST(target_date AS STRING) AS target_date,
               status, rows_written, attempt, run_id, error_code
        FROM `{_collection_table_path(RUNS_TABLE, config)}`
        WHERE target_date BETWEEN '{start}' AND '{end}'
    """
    return [dict(row.items()) for row in client.query(query).result()]


def format_report(missing_days, today=None, days=14):
    expected = expected_collection_dates(today, days)
    if not missing_days:
        return f"## 日次収集カバレッジ\n\n{expected[0]}〜{expected[-1]}の{days}日分は完了記録があります。"
    lines = [
        "## 日次収集カバレッジ",
        "",
        f"{expected[0]}〜{expected[-1]}のうち、完了記録がない日: {len(missing_days)}日",
        "",
    ]
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
    records = fetch_collection_runs(days=args.days)
    missing = find_missing_collection_days(records, days=args.days)
    report = format_report(missing, days=args.days)
    if args.output:
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
    if missing and args.fail_on_missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
