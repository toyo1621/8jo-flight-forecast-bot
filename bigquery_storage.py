import json
import math
import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from google.cloud import bigquery

from bigquery_schema import (
    DEFAULT_DATASET,
    DEFAULT_LOCATION,
    DEFAULT_PROJECT,
    DEFAULT_TABLE,
    PREDICTION_SNAPSHOT_SCHEMA,
    PREDICTION_SNAPSHOT_TABLE,
    RAW_TABLE,
    RUNS_TABLE,
    SCHEMA,
    ensure_collection_destinations,
    ensure_destination,
    ensure_prediction_snapshot_destination,
)
from flight_metadata import (
    VALID_HISTORY_STATUSES,
    VALID_STORED_STATUSES,
    classify_status_reason_with_confidence,
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


def _collection_table_path(table, config=None):
    config = config or settings()
    return f"{config['project']}.{config['dataset']}.{table}"


_SECRET_KEY_PARTS = ("key", "token", "secret", "password", "authorization", "credential")


def _redact_raw_payload(value, key=None):
    if key and any(part in key.lower() for part in _SECRET_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {_key: _redact_raw_payload(item, _key) for _key, item in value.items()}
    if isinstance(value, list):
        return [_redact_raw_payload(item) for item in value]
    return value


def _raw_payload_json(payload):
    return json.dumps(
        _redact_raw_payload(payload), ensure_ascii=False, sort_keys=True, default=str
    )


def _insert_collection_rows(client, table, rows):
    errors = client.insert_rows_json(table, rows)
    if errors:
        raise RuntimeError(f"BigQueryへの{table}書き込みに失敗しました。")


def save_raw_collection_payload(
    run_id, source, payload, target_date=None, attempt=1, fetched_at=None
):
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    ensure_collection_destinations(client, config["dataset"], config["location"])
    row = {
        "run_id": run_id,
        "attempt": attempt,
        "source": source,
        "target_date": target_date,
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        "payload_json": _raw_payload_json(payload),
        "redaction_applied": True,
    }
    _insert_collection_rows(client, _collection_table_path(RAW_TABLE, config), [row])
    return row


def record_collection_run(
    run_id,
    target_date,
    status,
    attempt=1,
    started_at=None,
    completed_at=None,
    error_code=None,
    error_message=None,
    rows_written=None,
    raw_rows=None,
    source_status=None,
    code_version=None,
):
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    ensure_collection_destinations(client, config["dataset"], config["location"])
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "run_id": run_id,
        "attempt": attempt,
        "target_date": target_date,
        "status": status,
        "started_at": started_at or now,
        "completed_at": completed_at,
        "error_code": error_code,
        "error_message": error_message,
        "rows_written": rows_written,
        "raw_rows": raw_rows,
        "source_status_json": json.dumps(source_status or {}, ensure_ascii=False, sort_keys=True),
        "code_version": code_version or os.getenv("GITHUB_SHA"),
        "created_at": now,
    }
    _insert_collection_rows(client, _collection_table_path(RUNS_TABLE, config), [row])
    return row


def load_raw_collection_payloads(run_id):
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    query = f"""
        SELECT source, target_date, payload_json, attempt
        FROM `{_collection_table_path(RAW_TABLE, config)}`
        WHERE run_id = @run_id
        ORDER BY fetched_at, source
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("run_id", "STRING", run_id)]
    )
    return [dict(row.items()) for row in client.query(query, job_config=job_config).result()]


def save_prediction_snapshots(rows):
    if not rows:
        return 0

    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    ensure_prediction_snapshot_destination(client, config["dataset"], config["location"])
    destination = _collection_table_path(PREDICTION_SNAPSHOT_TABLE, config)
    staging = f"{config['project']}.{config['dataset']}._prediction_snapshots_{uuid.uuid4().hex}"
    job_config = bigquery.LoadJobConfig(
        schema=PREDICTION_SNAPSHOT_SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    columns = [field.name for field in PREDICTION_SNAPSHOT_SCHEMA]
    column_list = ", ".join(columns)
    values = ", ".join(f"S.{column}" for column in columns)
    try:
        client.load_table_from_json(rows, staging, job_config=job_config).result()
        client.query(
            f"""
            MERGE `{destination}` T
            USING `{staging}` S
            ON T.snapshot_id = S.snapshot_id
            WHEN NOT MATCHED THEN INSERT ({column_list})
            VALUES ({values})
            """
        ).result()
    finally:
        client.delete_table(staging, not_found_ok=True)
    return len(rows)


def fetch_published_forecast_archive():
    """Return the final pre-valid-time public snapshot and observed outcome."""
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    query = f"""
        WITH ranked_snapshots AS (
          SELECT
            snapshot_id, forecast_target_date, flight_number, model,
            calculation_status, probability, prediction_generated_at,
            weather_valid_at,
            ROW_NUMBER() OVER (
              PARTITION BY forecast_target_date, flight_number, model
              ORDER BY prediction_generated_at DESC, snapshot_id DESC
            ) AS row_number
          FROM `{_collection_table_path(PREDICTION_SNAPSHOT_TABLE, config)}`
          WHERE forecast_target_date < CURRENT_DATE('Asia/Tokyo')
            AND prediction_generated_at <= weather_valid_at
        )
        SELECT
          s.snapshot_id, s.forecast_target_date, s.flight_number, s.model,
          s.calculation_status, s.probability, s.prediction_generated_at,
          h.status AS outcome_status, h.status_reason, h.status_reason_category,
          h.status_reason_source, h.status_reason_observed_at
        FROM ranked_snapshots s
        LEFT JOIN `{table_path(config)}` h
          ON h.date = s.forecast_target_date
         AND h.flight_number = s.flight_number
        WHERE s.row_number = 1
        ORDER BY s.forecast_target_date DESC, s.flight_number, s.model
    """
    return [dict(row.items()) for row in client.query(query).result()]


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
               cloud_cover_low, visibility, status_reason_category,
               status_reason_source, status_reason_observed_at, status_reason_confidence
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
    reason_category, inferred_confidence = classify_status_reason_with_confidence(
        status,
        item.get("status_reason"),
        item.get("status_reason_category"),
    )
    confidence = item.get("status_reason_confidence", inferred_confidence)
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None and (not math.isfinite(confidence) or not 0 <= confidence <= 1):
        confidence = None
    reason_source = item.get("status_reason_source")
    if not reason_source and reason_category != "not_applicable":
        reason_source = (
            "unknown"
            if not item.get("status_reason") or item.get("status_reason") in {"未確認", "不明", "unknown"}
            else "unspecified"
        )
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
        "status_reason_category": reason_category,
        "status_reason_source": reason_source,
        "status_reason_observed_at": item.get("status_reason_observed_at"),
        "status_reason_confidence": confidence,
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
          status_reason_category = CASE
            WHEN S.status = T.status
              AND S.status_reason_category IN ('unknown', 'not_applicable')
              THEN COALESCE(T.status_reason_category, S.status_reason_category)
            ELSE S.status_reason_category
          END,
          status_reason_source = CASE
            WHEN S.status = T.status
              AND (S.status_reason IS NULL OR S.status_reason = '未確認')
              THEN COALESCE(T.status_reason_source, S.status_reason_source)
            ELSE S.status_reason_source
          END,
          status_reason_observed_at = CASE
            WHEN S.status = T.status
              AND (S.status_reason IS NULL OR S.status_reason = '未確認')
              THEN COALESCE(T.status_reason_observed_at, S.status_reason_observed_at)
            ELSE S.status_reason_observed_at
          END,
          status_reason_confidence = CASE
            WHEN S.status = T.status
              AND (S.status_reason IS NULL OR S.status_reason = '未確認')
              THEN COALESCE(T.status_reason_confidence, S.status_reason_confidence)
            ELSE S.status_reason_confidence
          END,
          created_at = COALESCE(T.created_at, S.created_at),
          migrated_at = COALESCE(T.migrated_at, S.migrated_at)
        WHEN NOT MATCHED THEN INSERT
          (date, flight_number, flight_display_name, scheduled_time, status, wind_direction,
           wind_speed, wind_gusts, cloud_cover_low, visibility, visibility_source, status_reason,
           status_reason_category, status_reason_source, status_reason_observed_at,
           status_reason_confidence, created_at, migrated_at)
        VALUES
          (S.date, S.flight_number, S.flight_display_name, S.scheduled_time, S.status,
           S.wind_direction, S.wind_speed, S.wind_gusts, S.cloud_cover_low, S.visibility,
           S.visibility_source, S.status_reason, S.status_reason_category,
           S.status_reason_source, S.status_reason_observed_at, S.status_reason_confidence,
           S.created_at, S.migrated_at)
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

