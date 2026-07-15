from datetime import date

from backfill_bigquery_visibility import FORECAST_HOUR_BY_FLIGHT, _date_chunks


def test_date_chunks_cover_range_without_overlap():
    chunks = list(_date_chunks(date(2025, 12, 1), date(2026, 3, 1), days=60))

    assert chunks == [
        (date(2025, 12, 1), date(2026, 1, 29)),
        (date(2026, 1, 30), date(2026, 3, 1)),
    ]


def test_backfill_uses_shared_flight_forecast_hours():
    assert FORECAST_HOUR_BY_FLIGHT == {"ANA1891": 8, "ANA1893": 13, "ANA1895": 17}

