import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from google.cloud import bigquery

from bigquery_schema import DEFAULT_DATASET, DEFAULT_LOCATION, DEFAULT_PROJECT, DEFAULT_TABLE, SCHEMA, ensure_destination
from flight_metadata import (
    VALID_HISTORY_STATUSES,
    VALID_STORED_STATUSES,
    flight_display_name,
    normalize_database_status,
    normalize_status,
)


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
    return [
        (row["flight_number"], row["status"], row["wind_direction"], row["wind_speed"])
        for row in fetch_detailed_history()
    ]


@lru_cache(maxsize=1)
def fetch_detailed_history():
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    accepted_statuses = ", ".join(f"'{status}'" for status in sorted(VALID_HISTORY_STATUSES))
    query = f"""
        SELECT CAST(date AS STRING) AS date, flight_number, flight_display_name,
               status, status_reason, wind_direction, wind_speed, wind_gusts,
               cloud_cover_low, visibility
        FROM `{table_path(config)}`
        WHERE status IS NOT NULL
          AND wind_direction IS NOT NULL
          AND wind_speed IS NOT NULL
          AND status IN ({accepted_statuses})
    """
    rows = [dict(row.items()) for row in client.query(query).result()]
    for row in rows:
        row["status"] = normalize_status(row["status"])
    return rows


def _normalize_item(item, timestamp):
    scheduled_time = item.get("scheduled_time")
    if scheduled_time and scheduled_time.count(":") == 1:
        scheduled_time = f"{scheduled_time}:00"
    status = normalize_database_status(item.get("status"))
    if status not in VALID_STORED_STATUSES:
        raise ValueError(f"Unsupported flight status: {item.get('status')}")
    return {
        "date": item["date"],
        "flight_number": item["flight_number"],
        "flight_display_name": flight_display_name(item["flight_number"]),
        "scheduled_time": scheduled_time,
        "status": status,
        "wind_direction": item.get("wind_direction"),
        "wind_speed": item.get("wind_speed"),
        "wind_gusts": item.get("wind_gusts"),
        "cloud_cover_low": item.get("cloud_cover_low"),
        "visibility": item.get("visibility"),
        "visibility_source": item.get("visibility_source") or (
            "open_meteo_forecast" if item.get("visibility") is not None else None
        ),
        "status_reason": item.get("status_reason"),
        "created_at": timestamp,
        "migrated_at": timestamp,
    }


def build_upsert_sql(destination, staging):
    return f"""
        MERGE `{destination}` T
        USING `{staging}` S
        ON T.date = S.date AND T.flight_number = S.flight_number
        WHEN MATCHED THEN UPDATE SET
          flight_display_name = COALESCE(S.flight_display_name, T.flight_display_name),
          scheduled_time = COALESCE(S.scheduled_time, T.scheduled_time),
          status = S.status,
          wind_direction = COALESCE(S.wind_direction, T.wind_direction),
          wind_speed = COALESCE(S.wind_speed, T.wind_speed),
          wind_gusts = COALESCE(S.wind_gusts, T.wind_gusts),
          cloud_cover_low = COALESCE(S.cloud_cover_low, T.cloud_cover_low),
          visibility = COALESCE(S.visibility, T.visibility),
          visibility_source = CASE
            WHEN S.visibility IS NULL THEN T.visibility_source
            ELSE COALESCE(S.visibility_source, T.visibility_source)
          END,
          status_reason = CASE
            WHEN S.status = T.status
              AND (S.status_reason IS NULL OR S.status_reason = '未確認')
              THEN COALESCE(T.status_reason, S.status_reason)
            ELSE S.status_reason
          END,
          created_at = COALESCE(T.created_at, S.created_at),
          migrated_at = COALESCE(T.migrated_at, S.migrated_at)
        WHEN NOT MATCHED THEN INSERT
          (date, flight_number, flight_display_name, scheduled_time, status, wind_direction,
           wind_speed, wind_gusts, cloud_cover_low, visibility, visibility_source, status_reason,
           created_at, migrated_at)
        VALUES
          (S.date, S.flight_number, S.flight_display_name, S.scheduled_time, S.status,
           S.wind_direction, S.wind_speed, S.wind_gusts, S.cloud_cover_low, S.visibility,
           S.visibility_source, S.status_reason, S.created_at, S.migrated_at)
    """


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
    try:
        client.load_table_from_json(payload, staging, job_config=job_config).result()
        client.query(build_upsert_sql(destination, staging)).result()
    finally:
        client.delete_table(staging, not_found_ok=True)
    fetch_history.cache_clear()
    fetch_detailed_history.cache_clear()
    return len(payload)


def delete_unresolved_status_rows():
    """Delete rows that cannot be interpreted as an observed flight outcome."""
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    accepted_statuses = ", ".join(f"'{status}'" for status in sorted(VALID_HISTORY_STATUSES))
    job = client.query(
        f"""
        DELETE FROM `{table_path(config)}`
        WHERE status IS NULL OR status NOT IN ({accepted_statuses})
        """
    )
    job.result()
    fetch_history.cache_clear()
    fetch_detailed_history.cache_clear()
    return job.num_dml_affected_rows or 0

