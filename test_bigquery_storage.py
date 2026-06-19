from unittest.mock import patch

import bigquery_storage
from data_collector import save_collected_data


def test_normalize_item_formats_time():
    result = bigquery_storage._normalize_item(
        {
            "date": "2026-06-19",
            "flight_number": "ANA1891",
            "scheduled_time": "08:30",
            "status": "通常",
        },
        "2026-06-19T00:00:00+00:00",
    )

    assert result["scheduled_time"] == "08:30:00"
    assert result["flight_number"] == "ANA1891"


def test_collector_uses_bigquery_backend(monkeypatch):
    monkeypatch.setenv("FORECAST_DATA_BACKEND", "bigquery")
    items = [{"date": "2026-06-19", "flight_number": "ANA1891"}]

    with patch("data_collector.upsert_flight_weather_logs", return_value=1) as upsert:
        save_collected_data(None, items)

    upsert.assert_called_once_with(items)
