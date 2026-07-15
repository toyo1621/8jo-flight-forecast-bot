from unittest.mock import Mock, patch

import pytest

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

    assert result["status"] == "運航(条件付)"
    assert result["visibility_source"] == "open_meteo_archive"


def test_collector_uses_bigquery_backend():
    items = [
        {
            **flight,
            "wind_direction": 180.0,
            "wind_speed": 5.0,
            "wind_gusts": 8.0,
            "cloud_cover_low": 20.0,
            "visibility": 15.0,
        }
        for flight in get_demo_flight_data()
    ]

    with patch("data_collector.upsert_flight_weather_logs", return_value=3) as upsert:
        save_collected_data(items)

    upsert.assert_called_once_with(items)


def test_normalize_item_rejects_unresolved_status():
    with pytest.raises(ValueError, match="Unsupported flight status"):
        bigquery_storage._normalize_item(
            {
                "date": "2026-06-19",
                "flight_number": "ANA1891",
                "status": "未取得",
            },
            "2026-06-19T00:00:00+00:00",
        )


def test_upsert_merge_preserves_valid_values_and_known_reason():
    client = Mock(project="hachijo-flight-forecast")
    client.load_table_from_json.return_value.result.return_value = None
    client.query.return_value.result.return_value = None
    item = {
        "date": "2026-06-19",
        "flight_number": "ANA1891",
        "scheduled_time": "08:30",
        "status": "欠航",
        "status_reason": "未確認",
        "wind_direction": 180.0,
        "wind_speed": 5.0,
    }

    with (
        patch("bigquery_storage.bigquery.Client", return_value=client),
        patch("bigquery_storage.ensure_destination"),
    ):
        assert bigquery_storage.upsert_flight_weather_logs([item]) == 1

    merge_sql = client.query.call_args.args[0]
    assert "wind_direction = COALESCE(S.wind_direction, T.wind_direction)" in merge_sql
    assert "wind_speed = COALESCE(S.wind_speed, T.wind_speed)" in merge_sql
    assert "S.status_reason IS NULL OR S.status_reason = '未確認'" in merge_sql
    client.delete_table.assert_called_once()


def test_upsert_removes_staging_table_when_load_fails():
    client = Mock(project="hachijo-flight-forecast")
    client.load_table_from_json.return_value.result.side_effect = RuntimeError("load failed")
    item = {
        "date": "2026-06-19",
        "flight_number": "ANA1891",
        "scheduled_time": "08:30",
        "status": "運航",
    }

    with (
        patch("bigquery_storage.bigquery.Client", return_value=client),
        patch("bigquery_storage.ensure_destination"),
        pytest.raises(RuntimeError, match="load failed"),
    ):
        bigquery_storage.upsert_flight_weather_logs([item])

    client.delete_table.assert_called_once()


def test_demo_data_is_only_created_explicitly():
    flights = get_demo_flight_data()

    assert len(flights) == 3
    assert flights[1]["status"] == "運航(条件付)"


def test_odpt_arrival_statuses_count_as_operated():
    assert STATUS_MAPPING["odpt.FlightStatus:Arrived"] == "運航"
    assert STATUS_MAPPING["odpt.FlightStatus:EstimatedArrival"] == "運航"
    assert STATUS_MAPPING["odpt.FlightStatus:Delayed"] == "運航"
    assert STATUS_MAPPING["odpt.FlightStatus:Conditional"] == "運航(条件付)"
    assert STATUS_MAPPING["odpt.FlightStatus:Diverted"] == "条件付き→引返欠航"
    assert STATUS_MAPPING["odpt.FlightStatus:Returned"] == "条件付き→引返欠航"


def test_legacy_status_labels_are_normalized_for_display():
    assert normalize_status("通常") == "運航"
    assert normalize_status("条件付き運航") == "運航(条件付)"
    assert normalize_status("条件付→運航") == "運航(条件付)"
    assert normalize_status("引き返し(出発空港着)") == "条件付き→引返欠航"
    assert normalize_database_status("条件付き→就航") == "運航(条件付)"
    assert normalize_database_status("通常") == "運航"

