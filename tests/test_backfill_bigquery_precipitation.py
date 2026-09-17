from datetime import date
from types import SimpleNamespace

from flight_forecast.backfill_bigquery_precipitation import (
    FORECAST_HOUR_BY_FLIGHT,
    _date_chunks,
    build_updates,
)


def test_date_chunks_cover_range_without_overlap():
    chunks = list(_date_chunks(date(2025, 12, 1), date(2026, 3, 1), days=60))

    assert chunks == [
        (date(2025, 12, 1), date(2026, 1, 29)),
        (date(2026, 1, 30), date(2026, 3, 1)),
    ]


def test_build_updates_uses_flight_forecast_hour_and_keeps_zero_rainfall():
    rows = [
        SimpleNamespace(date=date(2026, 9, 17), flight_number="ANA1891"),
        SimpleNamespace(date=date(2026, 9, 17), flight_number="ANA1893"),
        SimpleNamespace(date=date(2026, 9, 17), flight_number="OTHER"),
    ]
    values = {
        "2026-09-17T08:00": 0.0,
        "2026-09-17T13:00": 2.5,
    }

    updates = build_updates(rows, values)

    assert FORECAST_HOUR_BY_FLIGHT == {
        "ANA1891": 8,
        "ANA1893": 13,
        "ANA1895": 17,
    }
    assert [item["precipitation"] for item in updates] == [0.0, 2.5]
    assert all(
        item["precipitation_source"] == "open_meteo_historical_forecast"
        for item in updates
    )
