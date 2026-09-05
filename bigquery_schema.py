from google.cloud import bigquery

DEFAULT_PROJECT = "hachijo-flight-forecast"
DEFAULT_DATASET = "flight_forecast"
DEFAULT_TABLE = "flight_weather_logs"
RAW_TABLE = "flight_collection_raw"
RUNS_TABLE = "collection_runs"
MAINTENANCE_AUDIT_TABLE = "maintenance_audit"
PREDICTION_SNAPSHOT_TABLE = "prediction_snapshots"
PREDICTION_PUBLICATION_TABLE = "prediction_publications"
DEFAULT_LOCATION = "asia-northeast1"

SCHEMA = (
    bigquery.SchemaField("date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("flight_number", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("flight_display_name", "STRING"),
    bigquery.SchemaField("scheduled_time", "TIME"),
    bigquery.SchemaField("status", "STRING"),
    bigquery.SchemaField("wind_direction", "FLOAT"),
    bigquery.SchemaField("wind_speed", "FLOAT"),
    bigquery.SchemaField("wind_gusts", "FLOAT"),
    bigquery.SchemaField("cloud_cover_low", "FLOAT"),
    bigquery.SchemaField("visibility", "FLOAT"),
    bigquery.SchemaField("visibility_source", "STRING"),
    bigquery.SchemaField("status_reason", "STRING"),
    bigquery.SchemaField("status_reason_category", "STRING"),
    bigquery.SchemaField("status_reason_source", "STRING"),
    bigquery.SchemaField("status_reason_observed_at", "TIMESTAMP"),
    bigquery.SchemaField("status_reason_confidence", "FLOAT"),
    bigquery.SchemaField("created_at", "TIMESTAMP"),
    bigquery.SchemaField("migrated_at", "TIMESTAMP", mode="REQUIRED"),
)

RAW_SCHEMA = (
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("attempt", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("source", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("target_date", "DATE"),
    bigquery.SchemaField("fetched_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("payload_json", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("redaction_applied", "BOOLEAN", mode="REQUIRED"),
)

COLLECTION_RUN_SCHEMA = (
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("attempt", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("target_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("started_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("completed_at", "TIMESTAMP"),
    bigquery.SchemaField("error_code", "STRING"),
    bigquery.SchemaField("error_message", "STRING"),
    bigquery.SchemaField("rows_written", "INTEGER"),
    bigquery.SchemaField("raw_rows", "INTEGER"),
    bigquery.SchemaField("source_status_json", "STRING"),
    bigquery.SchemaField("code_version", "STRING"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
)

MAINTENANCE_AUDIT_SCHEMA = (
    bigquery.SchemaField("audit_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("operation", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("dry_run", "BOOLEAN", mode="REQUIRED"),
    bigquery.SchemaField("target_scope", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("matched_count", "INTEGER"),
    bigquery.SchemaField("affected_count", "INTEGER"),
    bigquery.SchemaField("reason", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("actor", "STRING"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
)

PREDICTION_SNAPSHOT_SCHEMA = (
    bigquery.SchemaField("snapshot_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_attempt", "INTEGER"),
    bigquery.SchemaField("forecast_target_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("flight_number", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("model", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("calculation_status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("probability", "FLOAT"),
    bigquery.SchemaField("base_probability", "FLOAT"),
    bigquery.SchemaField("weather_factor", "FLOAT"),
    bigquery.SchemaField("typhoon_factor", "FLOAT"),
    bigquery.SchemaField("factor_breakdown_json", "STRING"),
    bigquery.SchemaField("prediction_generated_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("weather_retrieved_at", "TIMESTAMP"),
    bigquery.SchemaField("weather_valid_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("lead_hours", "INTEGER"),
    bigquery.SchemaField("provider", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("source_endpoint", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("fallback_used", "BOOLEAN", mode="REQUIRED"),
    bigquery.SchemaField("fallback_reason", "STRING"),
    bigquery.SchemaField("code_version", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("config_version", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("provenance_status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("weather_json", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("model_input_json", "STRING"),
    bigquery.SchemaField("input_fingerprint", "STRING"),
    bigquery.SchemaField("content_fingerprint", "STRING"),
    bigquery.SchemaField("publication_tracking_enabled", "BOOLEAN"),
    bigquery.SchemaField("weather_field_sources_json", "STRING"),
    bigquery.SchemaField("typhoon_risk_level", "STRING"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
)

PREDICTION_PUBLICATION_SCHEMA = (
    bigquery.SchemaField("artifact_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("snapshot_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("publication_status", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("run_id", "STRING"),
    bigquery.SchemaField("run_attempt", "INTEGER"),
    bigquery.SchemaField("code_version", "STRING"),
    bigquery.SchemaField("config_version", "STRING"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("published_at", "TIMESTAMP"),
    bigquery.SchemaField("publication_checked_at", "TIMESTAMP"),
    bigquery.SchemaField("published_url", "STRING"),
)


def ensure_destination(client, dataset_id, table_id, location):
    dataset_ref = bigquery.Dataset(f"{client.project}.{dataset_id}")
    dataset_ref.location = location
    client.create_dataset(dataset_ref, exists_ok=True)

    table_ref = bigquery.Table(f"{client.project}.{dataset_id}.{table_id}", schema=SCHEMA)
    table_ref.time_partitioning = bigquery.TimePartitioning(field="date")
    table_ref.clustering_fields = ["flight_number", "status"]
    client.create_table(table_ref, exists_ok=True)
    for column in (
        "status_reason_category STRING",
        "status_reason_source STRING",
        "status_reason_observed_at TIMESTAMP",
        "status_reason_confidence FLOAT64",
    ):
        client.query(
            f"ALTER TABLE `{client.project}.{dataset_id}.{table_id}` "
            f"ADD COLUMN IF NOT EXISTS {column}"
        ).result()


def ensure_collection_destinations(client, dataset_id, location):
    dataset_ref = bigquery.Dataset(f"{client.project}.{dataset_id}")
    dataset_ref.location = location
    client.create_dataset(dataset_ref, exists_ok=True)

    raw_ref = bigquery.Table(
        f"{client.project}.{dataset_id}.{RAW_TABLE}", schema=RAW_SCHEMA
    )
    raw_ref.time_partitioning = bigquery.TimePartitioning(field="fetched_at")
    raw_ref.clustering_fields = ["source", "target_date"]
    client.create_table(raw_ref, exists_ok=True)

    runs_ref = bigquery.Table(
        f"{client.project}.{dataset_id}.{RUNS_TABLE}", schema=COLLECTION_RUN_SCHEMA
    )
    runs_ref.time_partitioning = bigquery.TimePartitioning(field="started_at")
    runs_ref.clustering_fields = ["target_date", "status"]
    client.create_table(runs_ref, exists_ok=True)
    client.query(
        f"ALTER TABLE `{client.project}.{dataset_id}.{RUNS_TABLE}` "
        "ADD COLUMN IF NOT EXISTS source_status_json STRING"
    ).result()

    audit_ref = bigquery.Table(
        f"{client.project}.{dataset_id}.{MAINTENANCE_AUDIT_TABLE}",
        schema=MAINTENANCE_AUDIT_SCHEMA,
    )
    audit_ref.time_partitioning = bigquery.TimePartitioning(field="created_at")
    audit_ref.clustering_fields = ["operation", "status"]
    client.create_table(audit_ref, exists_ok=True)


def ensure_prediction_snapshot_destination(client, dataset_id, location):
    dataset_ref = bigquery.Dataset(f"{client.project}.{dataset_id}")
    dataset_ref.location = location
    client.create_dataset(dataset_ref, exists_ok=True)

    table_ref = bigquery.Table(
        f"{client.project}.{dataset_id}.{PREDICTION_SNAPSHOT_TABLE}",
        schema=PREDICTION_SNAPSHOT_SCHEMA,
    )
    table_ref.time_partitioning = bigquery.TimePartitioning(field="prediction_generated_at")
    table_ref.clustering_fields = ["forecast_target_date", "flight_number", "model"]
    client.create_table(table_ref, exists_ok=True)
    for column in (
        "base_probability FLOAT64",
        "weather_factor FLOAT64",
        "typhoon_factor FLOAT64",
        "factor_breakdown_json STRING",
        "weather_field_sources_json STRING",
        "run_attempt INT64",
        "model_input_json STRING",
        "input_fingerprint STRING",
        "content_fingerprint STRING",
        "publication_tracking_enabled BOOL",
    ):
        client.query(
            f"ALTER TABLE `{client.project}.{dataset_id}.{PREDICTION_SNAPSHOT_TABLE}` "
            f"ADD COLUMN IF NOT EXISTS {column}"
        ).result()

    publication_ref = bigquery.Table(
        f"{client.project}.{dataset_id}.{PREDICTION_PUBLICATION_TABLE}",
        schema=PREDICTION_PUBLICATION_SCHEMA,
    )
    publication_ref.time_partitioning = bigquery.TimePartitioning(field="created_at")
    publication_ref.clustering_fields = ["artifact_id", "publication_status"]
    client.create_table(publication_ref, exists_ok=True)
    for column in (
        "run_id STRING",
        "run_attempt INT64",
        "code_version STRING",
        "config_version STRING",
        "published_at TIMESTAMP",
        "publication_checked_at TIMESTAMP",
        "published_url STRING",
    ):
        client.query(
            f"ALTER TABLE `{client.project}.{dataset_id}.{PREDICTION_PUBLICATION_TABLE}` "
            f"ADD COLUMN IF NOT EXISTS {column}"
        ).result()
