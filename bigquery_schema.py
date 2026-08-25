from google.cloud import bigquery

DEFAULT_PROJECT = "hachijo-flight-forecast"
DEFAULT_DATASET = "flight_forecast"
DEFAULT_TABLE = "flight_weather_logs"
RAW_TABLE = "flight_collection_raw"
RUNS_TABLE = "collection_runs"
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
    bigquery.SchemaField("code_version", "STRING"),
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
)


def ensure_destination(client, dataset_id, table_id, location):
    dataset_ref = bigquery.Dataset(f"{client.project}.{dataset_id}")
    dataset_ref.location = location
    client.create_dataset(dataset_ref, exists_ok=True)

    table_ref = bigquery.Table(f"{client.project}.{dataset_id}.{table_id}", schema=SCHEMA)
    table_ref.time_partitioning = bigquery.TimePartitioning(field="date")
    table_ref.clustering_fields = ["flight_number", "status"]
    client.create_table(table_ref, exists_ok=True)


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
