"""Backfill hourly precipitation without overwriting existing BigQuery values."""

import argparse
import math
import uuid
from datetime import timedelta

import requests
from google.cloud import bigquery

from flight_forecast.app_config import (
    FLIGHTS,
    HACHIJO_AIRPORT_LATITUDE,
    HACHIJO_AIRPORT_LONGITUDE,
)
from flight_forecast.bigquery_schema import ensure_destination
from flight_forecast.bigquery_storage import settings, table_path

HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
SOURCE = "open_meteo_historical_forecast"
FORECAST_HOUR_BY_FLIGHT = {flight["number"]: flight["forecast_hour"] for flight in FLIGHTS}


def _date_chunks(start, end, days=60):
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def fetch_precipitation(start, end):
    values = {}
    for chunk_start, chunk_end in _date_chunks(start, end):
        response = requests.get(
            HISTORICAL_FORECAST_URL,
            params={
                "latitude": HACHIJO_AIRPORT_LATITUDE,
                "longitude": HACHIJO_AIRPORT_LONGITUDE,
                "start_date": chunk_start.isoformat(),
                "end_date": chunk_end.isoformat(),
                "hourly": "precipitation",
                "timezone": "Asia/Tokyo",
            },
            timeout=60,
        )
        response.raise_for_status()
        hourly = response.json().get("hourly", {})
        for timestamp, precipitation in zip(
            hourly.get("time", []), hourly.get("precipitation", [])
        ):
            if (
                isinstance(precipitation, (int, float))
                and not isinstance(precipitation, bool)
                and math.isfinite(precipitation)
                and precipitation >= 0
            ):
                values[timestamp] = round(float(precipitation), 2)
    return values


def build_updates(rows, precipitation):
    updates = []
    for row in rows:
        hour = FORECAST_HOUR_BY_FLIGHT.get(row.flight_number)
        if hour is None:
            continue
        timestamp = f"{row.date.isoformat()}T{hour:02d}:00"
        value = precipitation.get(timestamp)
        if value is not None:
            updates.append(
                {
                    "date": row.date.isoformat(),
                    "flight_number": row.flight_number,
                    "precipitation": value,
                    "precipitation_source": SOURCE,
                }
            )
    return updates


def _has_column(client, destination, column):
    return column in {field.name for field in client.get_table(destination).schema}


def backfill(apply=False):
    config = settings()
    client = bigquery.Client(project=config["project"], location=config["location"])
    destination = table_path(config)
    has_precipitation = _has_column(client, destination, "precipitation")

    if apply:
        ensure_destination(client, config["dataset"], config["table"], config["location"])
        has_precipitation = True

    missing_filter = "WHERE precipitation IS NULL" if has_precipitation else ""
    rows = list(
        client.query(
            f"""
            SELECT date, flight_number
            FROM `{destination}`
            {missing_filter}
            ORDER BY date, flight_number
            """
        ).result()
    )
    if not rows:
        print("No missing precipitation rows.")
        return 0, 0

    precipitation = fetch_precipitation(rows[0].date, rows[-1].date)
    updates = build_updates(rows, precipitation)
    missing = len(rows) - len(updates)
    mode = "apply" if apply else "dry-run"
    print(
        f"Mode: {mode}, candidates: {len(rows)}, available: {len(updates)}, "
        f"unavailable: {missing}"
    )
    if not apply or not updates:
        return len(updates), missing

    staging = f"{destination}_precipitation_{uuid.uuid4().hex}"
    schema = (
        bigquery.SchemaField("date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("flight_number", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("precipitation", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("precipitation_source", "STRING", mode="REQUIRED"),
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
            WHEN MATCHED AND T.precipitation IS NULL THEN UPDATE SET
              precipitation = S.precipitation,
              precipitation_source = S.precipitation_source
            """
        )
        job.result()
        print(f"Updated: {job.num_dml_affected_rows}")
    finally:
        client.delete_table(staging, not_found_ok=True)
    return len(updates), missing


def main():
    parser = argparse.ArgumentParser(
        description="Backfill missing BigQuery hourly precipitation values."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="NULLの降水量だけを更新する。未指定時はdry-run。",
    )
    args = parser.parse_args()
    backfill(apply=args.apply)


if __name__ == "__main__":
    main()
