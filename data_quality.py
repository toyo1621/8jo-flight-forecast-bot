import argparse
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from google.cloud import bigquery

from bigquery_storage import settings, table_path
from flight_metadata import FLIGHT_DISPLAY_NAMES, normalize_status


BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "flights.db"
OPERATED_STATUSES = {"運航", "通常", "遅延", "運航(条件付)"}
NON_OPERATED_STATUSES = {"欠航", "条件付き→引返欠航"}
KNOWN_STATUSES = OPERATED_STATUSES | NON_OPERATED_STATUSES
REQUIRED_WEATHER_FIELDS = ("wind_direction", "wind_speed", "wind_gusts", "cloud_cover_low")
REASON_REQUIRED_STATUSES = {"欠航", "条件付き→引返欠航"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")


@dataclass
class QualityFinding:
    severity: str
    code: str
    message: str
    count: int
    examples: list[str]


def _example_key(row):
    return f"{row.get('date')} {row.get('flight_number')}"


def _finding(severity, code, message, rows_or_count, examples=None):
    if isinstance(rows_or_count, int):
        count = rows_or_count
        example_values = examples or []
    else:
        rows = list(rows_or_count)
        count = len(rows)
        example_values = examples or [_example_key(row) for row in rows[:10]]
    return QualityFinding(severity, code, message, count, example_values)


def _parse_date(value):
    if isinstance(value, date):
        return value
    if isinstance(value, str) and DATE_RE.match(value):
        return datetime.strptime(value, "%Y-%m-%d").date()
    return None


def fetch_sqlite_records(db_file=DB_FILE):
    if not Path(db_file).exists():
        return []
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(flight_weather_logs)")}
        selected = [
            "date",
            "flight_number",
            "scheduled_time",
            "status",
            "wind_direction",
            "wind_speed",
            "wind_gusts",
            "cloud_cover_low",
            "visibility",
        ]
        optional = ["status_reason", "visibility_source"]
        selected.extend(column if column in columns else f"NULL AS {column}" for column in optional)
        return [dict(row) for row in conn.execute(f"SELECT {', '.join(selected)} FROM flight_weather_logs")]
    finally:
        conn.close()


def fetch_bigquery_records():
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    query = f"""
        SELECT CAST(date AS STRING) AS date, flight_number, scheduled_time, status,
               status_reason, wind_direction, wind_speed, wind_gusts, cloud_cover_low,
               visibility, visibility_source
        FROM `{table_path(config)}`
    """
    return [dict(row.items()) for row in client.query(query).result()]


def analyze_records(records, today=None):
    today = today or datetime.now().date()
    findings = []
    rows = [dict(row) for row in records]

    if not rows:
        return [_finding("error", "empty_dataset", "運航実績データが0件です。", 1)]

    keys = [(_parse_date(row.get("date")), row.get("flight_number")) for row in rows]
    duplicate_keys = [key for key, count in Counter(keys).items() if key[0] and key[1] and count > 1]
    if duplicate_keys:
        examples = [f"{key[0]} {key[1]}" for key in duplicate_keys[:10]]
        findings.append(_finding("error", "duplicate_date_flight", "date + flight_number が重複しています。", len(duplicate_keys), examples))

    invalid_date_rows = [row for row in rows if _parse_date(row.get("date")) is None]
    if invalid_date_rows:
        findings.append(_finding("error", "invalid_date", "date が YYYY-MM-DD として解釈できません。", invalid_date_rows))

    future_rows = [row for row in rows if (parsed := _parse_date(row.get("date"))) and parsed > today]
    if future_rows:
        findings.append(_finding("warning", "future_records", "未来日の運航実績が含まれています。", future_rows))

    unknown_flight_rows = [row for row in rows if row.get("flight_number") not in FLIGHT_DISPLAY_NAMES]
    if unknown_flight_rows:
        findings.append(_finding("error", "unknown_flight_number", "対象外の便名が含まれています。", unknown_flight_rows))

    missing_status_rows = [row for row in rows if not row.get("status")]
    if missing_status_rows:
        findings.append(_finding("error", "missing_status", "status が空です。", missing_status_rows))

    invalid_status_rows = [
        row for row in rows if row.get("status") and normalize_status(row["status"]) not in KNOWN_STATUSES
    ]
    if invalid_status_rows:
        findings.append(_finding("error", "unknown_status", "未対応の運航ステータスが含まれています。", invalid_status_rows))

    missing_reason_rows = [
        row
        for row in rows
        if normalize_status(row.get("status")) in REASON_REQUIRED_STATUSES and not row.get("status_reason")
    ]
    if missing_reason_rows:
        findings.append(_finding("warning", "missing_cancellation_reason", "欠航・引返欠航に欠航理由がありません。", missing_reason_rows))

    for field in REQUIRED_WEATHER_FIELDS:
        missing = [row for row in rows if row.get(field) is None]
        if missing:
            findings.append(_finding("warning", f"missing_{field}", f"{field} が欠測しています。", missing))

    missing_visibility = [row for row in rows if row.get("visibility") is None]
    if missing_visibility:
        findings.append(_finding("warning", "missing_visibility", "visibility が欠測しています。", missing_visibility))

    missing_visibility_source = [
        row for row in rows if row.get("visibility") is not None and not row.get("visibility_source")
    ]
    if missing_visibility_source:
        findings.append(_finding("info", "missing_visibility_source", "visibility_source が未設定です。", missing_visibility_source))

    invalid_time_rows = [
        row
        for row in rows
        if row.get("scheduled_time") and not TIME_RE.match(str(row["scheduled_time"]))
    ]
    if invalid_time_rows:
        findings.append(_finding("warning", "invalid_scheduled_time", "scheduled_time の形式が HH:MM[:SS] ではありません。", invalid_time_rows))

    by_date = defaultdict(set)
    for row in rows:
        parsed = _parse_date(row.get("date"))
        flight_number = row.get("flight_number")
        if parsed and flight_number in FLIGHT_DISPLAY_NAMES:
            by_date[parsed].add(flight_number)
    partial_dates = [
        f"{target_date} ({len(flights)}/3便)"
        for target_date, flights in sorted(by_date.items())
        if 0 < len(flights) < len(FLIGHT_DISPLAY_NAMES)
    ]
    if partial_dates:
        findings.append(_finding("info", "partial_daily_records", "同一日の3便が揃っていない日があります。", len(partial_dates), partial_dates[:10]))

    return findings


def summarize(findings, total_records):
    counts = Counter(finding.severity for finding in findings)
    return {
        "total_records": total_records,
        "errors": counts["error"],
        "warnings": counts["warning"],
        "infos": counts["info"],
        "passed": counts["error"] == 0,
    }


def format_markdown(findings, total_records):
    summary = summarize(findings, total_records)
    lines = [
        "# Data Quality Report",
        "",
        f"- Records checked: {summary['total_records']}",
        f"- Errors: {summary['errors']}",
        f"- Warnings: {summary['warnings']}",
        f"- Info: {summary['infos']}",
        "",
    ]
    if not findings:
        lines.append("No data quality findings.")
        return "\n".join(lines) + "\n"

    lines.extend(["| Severity | Code | Count | Examples |", "| --- | --- | ---: | --- |"])
    for finding in findings:
        examples = "<br>".join(finding.examples) if finding.examples else ""
        lines.append(f"| {finding.severity} | `{finding.code}` | {finding.count} | {examples} |")
    return "\n".join(lines) + "\n"


def format_text(findings, total_records):
    summary = summarize(findings, total_records)
    lines = [
        f"records={summary['total_records']} errors={summary['errors']} warnings={summary['warnings']} infos={summary['infos']}"
    ]
    for finding in findings:
        examples = ", ".join(finding.examples)
        lines.append(f"[{finding.severity}] {finding.code}: {finding.message} ({finding.count}) {examples}")
    return "\n".join(lines) + "\n"


def should_fail(findings, fail_on):
    if fail_on == "none":
        return False
    severities = {"error"} if fail_on == "error" else {"error", "warning"}
    return any(finding.severity in severities for finding in findings)


def main():
    parser = argparse.ArgumentParser(description="Check flight/weather data quality.")
    parser.add_argument("--backend", choices=("sqlite", "bigquery"), default=os.getenv("FORECAST_DATA_BACKEND", "sqlite").lower())
    parser.add_argument("--format", choices=("text", "markdown", "json"), default="text")
    parser.add_argument("--output")
    parser.add_argument("--fail-on", choices=("none", "error", "warning"), default="error")
    args = parser.parse_args()

    records = fetch_bigquery_records() if args.backend == "bigquery" else fetch_sqlite_records()
    findings = analyze_records(records)
    if args.format == "json":
        output = json.dumps(
            {"summary": summarize(findings, len(records)), "findings": [asdict(finding) for finding in findings]},
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    elif args.format == "markdown":
        output = format_markdown(findings, len(records))
    else:
        output = format_text(findings, len(records))

    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    raise SystemExit(1 if should_fail(findings, args.fail_on) else 0)


if __name__ == "__main__":
    main()
