from unittest.mock import patch

import bigquery_storage
from data_collector import STATUS_MAPPING, get_demo_flight_data, save_collected_data
from flight_metadata import normalize_database_status, normalize_status


def test_normalize_item_formats_time():
    result = bigquery_storage._normalize_item(
        {
            "date": "2026-06-19",
            "flight_number": "ANA1891",
            "scheduled_time": "08:30",
            "status": "運航",
        },
        "2026-06-19T00:00:00+00:00",
    )

    assert result["scheduled_time"] == "08:30:00"
    assert result["flight_number"] == "ANA1891"
    assert result["flight_display_name"] == "ANA1891(1便)"
    assert result["status_reason"] is None
    assert result["status"] == "運航"
    assert "id" not in result
    assert result["visibility_source"] is None


def test_normalize_item_uses_database_status_and_visibility_source():
    result = bigquery_storage._normalize_item(
        {
            "date": "2025-06-15",
            "flight_number": "ANA1891",
            "status": "条件付き→就航",
            "visibility": 12.0,
            "visibility_source": "open_meteo_archive",
        },
        "2026-06-22T00:00:00+00:00",
    )

    assert result["status"] == "条件付き運航"
    assert result["visibility_source"] == "open_meteo_archive"


def test_collector_uses_bigquery_backend(monkeypatch):
    monkeypatch.setenv("FORECAST_DATA_BACKEND", "bigquery")
    items = [{"date": "2026-06-19", "flight_number": "ANA1891"}]

    with patch("data_collector.upsert_flight_weather_logs", return_value=1) as upsert:
        save_collected_data(None, items)

    upsert.assert_called_once_with(items)


def test_demo_data_is_only_created_explicitly():
    flights = get_demo_flight_data()

    assert len(flights) == 3
    assert flights[1]["status"] == "条件付き→就航"


def test_odpt_arrival_statuses_count_as_operated():
    assert STATUS_MAPPING["odpt.FlightStatus:Arrived"] == "運航"
    assert STATUS_MAPPING["odpt.FlightStatus:EstimatedArrival"] == "運航"
    assert STATUS_MAPPING["odpt.FlightStatus:Delayed"] == "運航"
    assert STATUS_MAPPING["odpt.FlightStatus:Conditional"] == "条件付き→就航"
    assert STATUS_MAPPING["odpt.FlightStatus:Diverted"] == "条件付き→引返欠航"
    assert STATUS_MAPPING["odpt.FlightStatus:Returned"] == "条件付き→引返欠航"


def test_legacy_status_labels_are_normalized_for_display():
    assert normalize_status("通常") == "運航"
    assert normalize_status("条件付き運航") == "条件付き→就航"
    assert normalize_status("条件付→運航") == "条件付き→就航"
    assert normalize_status("引き返し(出発空港着)") == "条件付き→引返欠航"
    assert normalize_database_status("条件付き→就航") == "条件付き運航"
    assert normalize_database_status("通常") == "運航"

