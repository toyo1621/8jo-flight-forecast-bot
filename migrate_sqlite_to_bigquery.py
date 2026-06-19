import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import bigquery


DEFAULT_PROJECT = "hachijo-flight-forecast"
DEFAULT_DATASET = "flight_forecast"
DEFAULT_TABLE = "flight_weather_logs"
DEFAULT_LOCATION = "asia-northeast1"
DEFAULT_DB = Path(__file__).resolve().parent / "flights.db"

SCHEMA = (
    bigquery.SchemaField("id", "INTEGER"),
    bigquery.SchemaField("date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("flight_number", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("scheduled_time", "TIME"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("wind_direction", "FLOAT"),
    bigquery.SchemaField("wind_speed", "FLOAT"),
    bigquery.SchemaField("wind_gusts", "FLOAT"),
    bigquery.SchemaField("cloud_cover_low", "FLOAT"),
    bigquery.SchemaField("visibility", "FLOAT"),
    bigquery.SchemaField("status_reason", "STRING"),
    bigquery.SchemaField("created_at", "TIMESTAMP"),
    bigquery.SchemaField("migrated_at", "TIMESTAMP", mode="REQUIRED"),
)

COLUMNS = tuple(field.name for field in SCHEMA)


def read_sqlite_rows(db_file):
    if not db_file.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_file}")

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """
            SELECT id, date, flight_number, scheduled_time, status,
                   wind_direction, wind_speed, wind_gusts,
                   cloud_cover_low, visibility, created_at
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
    return {
        "id": row["id"],
        "date": row["date"],
        "flight_number": row["flight_number"],
        "scheduled_time": scheduled_time,
        "status": row["status"],
        "wind_direction": row["wind_direction"],
        "wind_speed": row["wind_speed"],
        "wind_gusts": row["wind_gusts"],
        "cloud_cover_low": row["cloud_cover_low"],
        "visibility": row["visibility"],
        "status_reason": None,
        "created_at": row["created_at"],
        "migrated_at": migrated_at.isoformat(),
    }


def ensure_destination(client, dataset_id, table_id, location):
    dataset_ref = bigquery.Dataset(f"{client.project}.{dataset_id}")
    dataset_ref.location = location
    client.create_dataset(dataset_ref, exists_ok=True)

    table_ref = bigquery.Table(f"{client.project}.{dataset_id}.{table_id}", schema=SCHEMA)
    table_ref.time_partitioning = bigquery.TimePartitioning(field="date")
    table_ref.clustering_fields = ["flight_number", "status"]
    client.create_table(table_ref, exists_ok=True)


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
    client.load_table_from_json(payload, staging, job_config=job_config).result()

    update_columns = [column for column in COLUMNS if column not in {"date", "flight_number"}]
    update_clause = ",\n        ".join(f"T.{column} = S.{column}" for column in update_columns)
    column_list = ", ".join(COLUMNS)
    value_list = ", ".join(f"S.{column}" for column in COLUMNS)
    merge_sql = f"""
    MERGE `{destination}` T
    USING `{staging}` S
    ON T.date = S.date AND T.flight_number = S.flight_number
    WHEN MATCHED THEN UPDATE SET
        {update_clause}
    WHEN NOT MATCHED THEN
      INSERT ({column_list}) VALUES ({value_list})
    """
    client.query(merge_sql).result()
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
