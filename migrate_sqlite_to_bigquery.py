import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery

from bigquery_schema import (
    DEFAULT_DATASET,
    DEFAULT_LOCATION,
    DEFAULT_PROJECT,
    DEFAULT_TABLE,
    SCHEMA,
    ensure_destination,
)
from bigquery_storage import build_upsert_sql
from flight_metadata import VALID_STORED_STATUSES, flight_display_name, normalize_database_status


DEFAULT_DB = Path(__file__).resolve().parent / "flights.db"


def read_sqlite_rows(db_file):
    if not db_file.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_file}")

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(flight_weather_logs)")}
        reason_column = "status_reason" if "status_reason" in columns else "NULL AS status_reason"
        return conn.execute(
            f"""
            SELECT date, flight_number, scheduled_time, status,
                   wind_direction, wind_speed, wind_gusts,
                   cloud_cover_low, visibility, NULL AS visibility_source, {reason_column}, created_at
            FROM flight_weather_logs
            ORDER BY date, flight_number
            """
        ).fetchall()
    finally:
        conn.close()


def normalize_row(row, migrated_at=None):
    migrated_at = migrated_at or datetime.now(timezone.utc)
    scheduled_time = row["scheduled_time"]
    if scheduled_time and scheduled_time.count(":") == 1:
        scheduled_time = f"{scheduled_time}:00"
    status = normalize_database_status(row["status"])
    if status not in VALID_STORED_STATUSES:
        raise ValueError(f"Unsupported flight status: {row['status']}")
    status_reason = "遅延" if row["status"] == "遅延" else row["status_reason"]
    return {
        "date": row["date"],
        "flight_number": row["flight_number"],
        "flight_display_name": flight_display_name(row["flight_number"]),
        "scheduled_time": scheduled_time,
        "status": status,
        "wind_direction": row["wind_direction"],
        "wind_speed": row["wind_speed"],
        "wind_gusts": row["wind_gusts"],
        "cloud_cover_low": row["cloud_cover_low"],
        "visibility": row["visibility"],
        "visibility_source": row["visibility_source"],
        "status_reason": status_reason,
        "created_at": row["created_at"],
        "migrated_at": migrated_at.isoformat(),
    }


def migrate(db_file, project, dataset_id, table_id, location):
    rows = read_sqlite_rows(db_file)
    if not rows:
        print("SQLiteに移行対象データがありません。")
        return 0

    client = bigquery.Client(project=project, location=location)
    ensure_destination(client, dataset_id, table_id, location)

    destination = f"{project}.{dataset_id}.{table_id}"
    staging = f"{project}.{dataset_id}._flight_weather_logs_migration"
    payload = [normalize_row(row) for row in rows]
    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    try:
        client.load_table_from_json(payload, staging, job_config=job_config).result()

        client.query(build_upsert_sql(destination, staging)).result()
    finally:
        client.delete_table(staging, not_found_ok=True)

    count = next(iter(client.query(f"SELECT COUNT(*) AS total FROM `{destination}`").result())).total
    print(f"移行完了: SQLite {len(rows)}件 / BigQuery {count}件")
    print(f"テーブル: {destination}")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="SQLiteの運航・気象履歴をBigQueryへ移行します。")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--location", default=DEFAULT_LOCATION)
    args = parser.parse_args()
    migrate(args.db, args.project, args.dataset, args.table, args.location)


if __name__ == "__main__":
    main()

