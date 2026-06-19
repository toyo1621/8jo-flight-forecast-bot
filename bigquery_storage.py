import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from google.cloud import bigquery

from flight_metadata import flight_display_name
from migrate_sqlite_to_bigquery import DEFAULT_DATASET, DEFAULT_LOCATION, DEFAULT_PROJECT, DEFAULT_TABLE, SCHEMA, ensure_destination


def settings():
    return {
        "project": os.getenv("GCP_PROJECT_ID", DEFAULT_PROJECT),
        "dataset": os.getenv("BIGQUERY_DATASET", DEFAULT_DATASET),
        "table": os.getenv("BIGQUERY_TABLE", DEFAULT_TABLE),
        "location": os.getenv("BIGQUERY_LOCATION", DEFAULT_LOCATION),
    }


def table_path(config=None):
    config = config or settings()
    return f"{config['project']}.{config['dataset']}.{config['table']}"


@lru_cache(maxsize=1)
def fetch_history():
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    query = f"""
        SELECT status, wind_direction, wind_speed
        FROM `{table_path(config)}`
        WHERE status IS NOT NULL
          AND wind_direction IS NOT NULL
          AND wind_speed IS NOT NULL
    """
    return [
        (row.status, row.wind_direction, row.wind_speed)
        for row in client.query(query).result()
    ]


def _normalize_item(item, timestamp):
    scheduled_time = item.get("scheduled_time")
    if scheduled_time and scheduled_time.count(":") == 1:
        scheduled_time = f"{scheduled_time}:00"
    return {
        "id": None,
        "date": item["date"],
        "flight_number": item["flight_number"],
        "flight_display_name": flight_display_name(item["flight_number"]),
        "scheduled_time": scheduled_time,
        "status": item.get("status"),
        "wind_direction": item.get("wind_direction"),
        "wind_speed": item.get("wind_speed"),
        "wind_gusts": item.get("wind_gusts"),
        "cloud_cover_low": item.get("cloud_cover_low"),
        "visibility": item.get("visibility"),
        "status_reason": item.get("status_reason"),
        "created_at": timestamp,
        "migrated_at": timestamp,
    }


def upsert_flight_weather_logs(items):
    if not items:
        return 0

    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    ensure_destination(client, config["dataset"], config["table"], config["location"])
    destination = table_path(config)
    staging = f"{config['project']}.{config['dataset']}._daily_{uuid.uuid4().hex}"
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = [_normalize_item(item, timestamp) for item in items]
    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    client.load_table_from_json(payload, staging, job_config=job_config).result()
    try:
        client.query(
            f"""
            MERGE `{destination}` T
            USING `{staging}` S
            ON T.date = S.date AND T.flight_number = S.flight_number
            WHEN MATCHED THEN UPDATE SET
              flight_display_name = S.flight_display_name,
              scheduled_time = S.scheduled_time,
              status = S.status,
              wind_direction = S.wind_direction,
              wind_speed = S.wind_speed,
              wind_gusts = S.wind_gusts,
              cloud_cover_low = S.cloud_cover_low,
              visibility = S.visibility,
              status_reason = S.status_reason,
              created_at = S.created_at,
              migrated_at = S.migrated_at
            WHEN NOT MATCHED THEN INSERT
              (id, date, flight_number, flight_display_name, scheduled_time, status, wind_direction,
               wind_speed, wind_gusts, cloud_cover_low, visibility, status_reason, created_at, migrated_at)
            VALUES
              (S.id, S.date, S.flight_number, S.flight_display_name, S.scheduled_time, S.status, S.wind_direction,
               S.wind_speed, S.wind_gusts, S.cloud_cover_low, S.visibility, S.status_reason, S.created_at, S.migrated_at)
            """
        ).result()
    finally:
        client.delete_table(staging, not_found_ok=True)
    fetch_history.cache_clear()
    return len(payload)
