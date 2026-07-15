from google.cloud import bigquery


DEFAULT_PROJECT = "hachijo-flight-forecast"
DEFAULT_DATASET = "flight_forecast"
DEFAULT_TABLE = "flight_weather_logs"
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


def ensure_destination(client, dataset_id, table_id, location):
    dataset_ref = bigquery.Dataset(f"{client.project}.{dataset_id}")
    dataset_ref.location = location
    client.create_dataset(dataset_ref, exists_ok=True)

    table_ref = bigquery.Table(f"{client.project}.{dataset_id}.{table_id}", schema=SCHEMA)
    table_ref.time_partitioning = bigquery.TimePartitioning(field="date")
    table_ref.clustering_fields = ["flight_number", "status"]
    client.create_table(table_ref, exists_ok=True)
