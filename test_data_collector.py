import json
from unittest.mock import Mock, patch

import pytest
import requests

from data_collector import (
    CollectionError,
    get_flight_data_odpt,
    get_weather_data,
    main,
    merge_with_daily_schedule,
    replay_collection_run,
    validate_collected_records,
)


def _weather_response():
    response = Mock(status_code=200)
    response.json.return_value = {
        "hourly": {
            "time": [f"2026-07-15T{hour:02d}:00" for hour in range(24)],
            "wind_speed_10m": list(range(24)),
            "wind_direction_10m": [180.0] * 24,
            "wind_gusts_10m": [18.0] * 24,
            "cloud_cover_low": [20.0] * 24,
            "visibility": [15000.0] * 24,
        }
    }
    return response


def _actual_flight(number, status="運航"):
    return {
        "date": "2026-07-15",
        "flight_number": number,
        "scheduled_time": {"ANA1891": "08:30", "ANA1893": "13:10", "ANA1895": "16:40"}[number],
        "status": status,
    }


def _complete_record(number):
    return {
        **_actual_flight(number),
        "wind_direction": 180.0,
        "wind_speed": 5.0,
        "wind_gusts": 8.0,
        "cloud_cover_low": 20.0,
        "visibility": 15.0,
    }


def test_first_flight_weather_uses_configured_eight_oclock_hour():
    with patch("data_collector.requests.get", return_value=_weather_response()):
        weather = get_weather_data("2026-07-15", "08:30", target_hour=8)

    assert weather["wind_speed"] == round(8 / 3.6, 2)
    assert weather["visibility_source"] == "open_meteo_forecast"


def test_odpt_request_failure_raises_without_secret_in_message():
    response = Mock(status_code=503)
    error = requests.HTTPError("secret-url", response=response)
    failed = Mock()
    failed.raise_for_status.side_effect = error

    with (
        patch("data_collector.requests.get", return_value=failed),
        pytest.raises(CollectionError) as raised,
    ):
        get_flight_data_odpt("do-not-log-this-key")

    assert "do-not-log-this-key" not in str(raised.value)
    assert "HTTP 503" in str(raised.value)
    assert raised.value.__suppress_context__ is True


def test_odpt_transient_failure_is_retried_and_raw_payload_can_be_captured():
    first = Mock(status_code=503)
    second = Mock(status_code=200)
    second.json.return_value = [
        {
            "odpt:originAirport": "odpt.Airport:HND",
            "odpt:flightNumber": ["NH1891"],
            "odpt:flightStatus": "odpt.FlightStatus:Normal",
            "odpt:flightDate": "2026-07-15",
        }
    ]
    captured = []

    with (
        patch("data_collector.requests.get", side_effect=[first, second]) as get,
        patch("data_collector.time.sleep") as sleep,
    ):
        result = get_flight_data_odpt(
            "test-key",
            raw_sink=lambda *args: captured.append(args),
            run_id="run-1",
        )

    assert [flight["flight_number"] for flight in result] == ["ANA1891"]
    assert get.call_count == 2
    sleep.assert_called_once_with(1)
    assert captured[0][0] == "odpt_flight_information_arrival"


def test_odpt_request_can_filter_by_target_date():
    response = Mock(status_code=200)
    response.json.return_value = [
        {
            "odpt:originAirport": "odpt.Airport:HND",
            "odpt:flightNumber": ["NH1891"],
            "odpt:flightStatus": "odpt.FlightStatus:Normal",
            "odpt:flightDate": "2026-07-15",
        }
    ]

    with patch("data_collector.requests.get", return_value=response) as get:
        get_flight_data_odpt("test-key", target_date="2026-07-15")

    assert get.call_args.kwargs["params"]["odpt:flightDate"] == "2026-07-15"


def test_replay_collection_run_rebuilds_rows_from_raw_payloads():
    odpt_payload = [
        {
            "odpt:originAirport": "odpt.Airport:HND",
            "odpt:flightNumber": [f"NH{number[3:]}"],
            "odpt:flightStatus": "odpt.FlightStatus:Normal",
            "odpt:flightDate": "2026-07-15",
        }
        for number in ("ANA1891", "ANA1893", "ANA1895")
    ]
    weather_payload = {
        "hourly": {
            "time": [f"2026-07-15T{hour:02d}:00" for hour in (8, 13, 17)],
            "wind_speed_10m": [18.0, 18.0, 18.0],
            "wind_direction_10m": [180.0, 180.0, 180.0],
            "wind_gusts_10m": [18.0, 18.0, 18.0],
            "cloud_cover_low": [20.0, 20.0, 20.0],
            "visibility": [15000.0, 15000.0, 15000.0],
        }
    }
    raw_rows = [
        {
            "source": "odpt_flight_information_arrival",
            "payload_json": json.dumps(odpt_payload),
        },
        {
            "source": "open_meteo_forecast",
            "payload_json": json.dumps(weather_payload),
        },
    ]

    with (
        patch("data_collector.load_raw_collection_payloads", return_value=raw_rows),
        patch("data_collector.save_collected_data", return_value=3) as save,
    ):
        result = replay_collection_run("run-1")

    assert result == 3
    save.assert_called_once()
    assert len(save.call_args.args[0]) == 3


def test_incomplete_daily_flights_are_not_merged_for_storage():
    flights = [_actual_flight("ANA1891"), _actual_flight("ANA1893")]

    with pytest.raises(CollectionError, match="ANA1895"):
        merge_with_daily_schedule("2026-07-15", flights)


def test_complete_daily_flights_keep_shared_forecast_hours():
    flights = [_actual_flight(number) for number in ("ANA1891", "ANA1893", "ANA1895")]

    merged = merge_with_daily_schedule("2026-07-15", flights)

    assert [flight["target_hour"] for flight in merged] == [8, 13, 17]


def test_missing_weather_prevents_entire_batch_from_being_saved():
    records = [_complete_record(number) for number in ("ANA1891", "ANA1893", "ANA1895")]
    records[1]["visibility"] = None

    with pytest.raises(CollectionError, match="ANA1893"):
        validate_collected_records(records)


def test_cleanup_only_does_not_call_external_apis(monkeypatch):
    monkeypatch.setattr("sys.argv", ["data_collector.py", "--cleanup-only"])
    with (
        patch("data_collector.delete_unresolved_status_rows", return_value=2) as cleanup,
        patch("data_collector.get_flight_data_odpt") as fetch_flights,
        patch("data_collector.get_weather_data") as fetch_weather,
    ):
        main()

    cleanup.assert_called_once_with()
    fetch_flights.assert_not_called()
    fetch_weather.assert_not_called()


def test_collection_failure_does_not_write_bigquery(monkeypatch):
    monkeypatch.setattr("sys.argv", ["data_collector.py"])
    monkeypatch.setenv("ODPT_API_KEY", "test-key")
    with (
        patch("data_collector.delete_unresolved_status_rows", return_value=0),
        patch(
            "data_collector.get_flight_data_odpt",
            side_effect=CollectionError("ODPT APIからのデータ取得に失敗しました。"),
        ),
        patch("data_collector.record_collection_run") as record_run,
        patch("data_collector.save_collected_data") as save,
        pytest.raises(CollectionError, match="ODPT API"),
    ):
        main()

    save.assert_not_called()
    assert record_run.call_count == 2
    assert record_run.call_args_list[-1].args[2] == "failed"


def test_collection_date_argument_is_recorded_and_passed_to_odpt(monkeypatch):
    monkeypatch.setattr("sys.argv", ["data_collector.py", "--date", "2026-07-15"])
    monkeypatch.setenv("ODPT_API_KEY", "test-key")
    weather = {
        "wind_direction": 180.0,
        "wind_speed": 5.0,
        "wind_gusts": 8.0,
        "cloud_cover_low": 20.0,
        "visibility": 15.0,
        "visibility_source": "open_meteo_forecast",
    }
    with (
        patch("data_collector.delete_unresolved_status_rows", return_value=0),
        patch(
            "data_collector.get_flight_data_odpt",
            return_value=[_actual_flight(number) for number in ("ANA1891", "ANA1893", "ANA1895")],
        ) as get_flights,
        patch("data_collector.get_weather_data", return_value=weather),
        patch("data_collector.record_collection_run") as record_run,
        patch("data_collector.save_collected_data", return_value=3),
    ):
        main()

    assert get_flights.call_args.kwargs["target_date"] == "2026-07-15"
    assert record_run.call_args_list[0].args[1] == "2026-07-15"
    assert record_run.call_args_list[-1].kwargs["source_status"]["odpt_flight_information_arrival"] == "succeeded"
