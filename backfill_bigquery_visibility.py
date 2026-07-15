import argparse
import uuid
from datetime import timedelta

import requests
from google.cloud import bigquery

from app_config import FLIGHTS, HACHIJO_AIRPORT_LATITUDE, HACHIJO_AIRPORT_LONGITUDE
from bigquery_storage import settings, table_path


HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
SOURCE = "open_meteo_historical_forecast"
FORECAST_HOUR_BY_FLIGHT = {flight["number"]: flight["forecast_hour"] for flight in FLIGHTS}


def _date_chunks(start, end, days=60):
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def fetch_visibility(start, end):
    values = {}
    for chunk_start, chunk_end in _date_chunks(start, end):
        response = requests.get(
            HISTORICAL_FORECAST_URL,
            params={
                "latitude": HACHIJO_AIRPORT_LATITUDE,
                "longitude": HACHIJO_AIRPORT_LONGITUDE,
                "start_date": chunk_start.isoformat(),
                "end_date": chunk_end.isoformat(),
                "hourly": "visibility",
                "timezone": "Asia/Tokyo",
            },
            timeout=60,
        )
        response.raise_for_status()
        hourly = response.json().get("hourly", {})
        for timestamp, visibility_m in zip(hourly.get("time", []), hourly.get("visibility", [])):
            if visibility_m is not None:
                values[timestamp] = round(visibility_m / 1000, 2)
    return values


def backfill(dry_run=False):
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    destination = table_path(config)
    client.query(
        f"ALTER TABLE `{destination}` ADD COLUMN IF NOT EXISTS visibility_source STRING"
    ).result()
    rows = list(
        client.query(
            f"""
            SELECT date, flight_number
            FROM `{destination}`
            WHERE visibility IS NULL
            ORDER BY date, flight_number
            """
        ).result()
    )
    if not rows:
        print("No missing visibility rows.")
        return 0, 0

    visibility = fetch_visibility(rows[0].date, rows[-1].date)
    updates = []
    for row in rows:
        hour = FORECAST_HOUR_BY_FLIGHT.get(row.flight_number)
        if hour is None:
            continue
        timestamp = f"{row.date.isoformat()}T{hour:02d}:00"
        value = visibility.get(timestamp)
        if value is not None:
            updates.append(
                {
                    "date": row.date.isoformat(),
                    "flight_number": row.flight_number,
                    "visibility": value,
                    "visibility_source": SOURCE,
                }
            )

    missing = len(rows) - len(updates)
    print(f"Candidates: {len(rows)}, available: {len(updates)}, unavailable: {missing}")
    if dry_run or not updates:
        return len(updates), missing

    staging = f"{destination}_visibility_{uuid.uuid4().hex}"
    schema = (
        bigquery.SchemaField("date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("flight_number", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("visibility", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("visibility_source", "STRING", mode="REQUIRED"),
    )
    client.load_table_from_json(
        updates,
        staging,
        job_config=bigquery.LoadJobConfig(schema=schema),
    ).result()
    try:
        job = client.query(
            f"""
            MERGE `{destination}` T
            USING `{staging}` S
            ON T.date = S.date AND T.flight_number = S.flight_number
            WHEN MATCHED AND T.visibility IS NULL THEN UPDATE SET
              visibility = S.visibility,
              visibility_source = S.visibility_source
            """
        )
        job.result()
        print(f"Updated: {job.num_dml_affected_rows}")
    finally:
        client.delete_table(staging, not_found_ok=True)
    return len(updates), missing


def main():
    parser = argparse.ArgumentParser(description="Backfill missing BigQuery visibility values.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

