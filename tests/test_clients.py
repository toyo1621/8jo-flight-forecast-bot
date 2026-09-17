from unittest.mock import Mock, patch

import pytest
import requests

from flight_forecast.clients.open_meteo import (
    fetch_deterministic_forecast,
    parse_deterministic_response,
    parse_ensemble_response,
    select_evenly,
)
from flight_forecast.clients.typhoon_impact import parse_typhoon_impact_response


def test_open_meteo_ensemble_parser_preserves_model_member_and_skips_incomplete_members():
    payload = {
        "hourly": {
            "time": ["2026-08-25T08:00", "2026-08-25T09:00"],
            "wind_speed_10m_member01": [4.0, 5.0],
            "wind_direction_10m_member01": [180.0, 180.0],
            "wind_gusts_10m_member01": [7.0, 8.0],
            "cloud_cover_low_member01": [20.0, 30.0],
            "precipitation_member01": [0.0, 0.0],
            "wind_speed_10m_member02": [6.0, 7.0],
            "wind_direction_10m_member02": [190.0, 190.0],
            "wind_gusts_10m_member02": [9.0, 10.0],
            "cloud_cover_low_member02": [40.0, 50.0],
            "precipitation_member02": [0.0, None],
        }
    }

    parsed = parse_ensemble_response(
        payload,
        model="gfs_seamless",
        variables=(
            "wind_speed_10m",
            "wind_direction_10m",
            "wind_gusts_10m",
            "cloud_cover_low",
            "precipitation",
        ),
        max_members=31,
    )

    assert [member["_member_id"] for member in parsed["2026-08-25T08:00"]] == [
        "gfs_seamless:01",
        "gfs_seamless:02",
    ]
    assert parsed["2026-08-25T09:00"][0]["wind_speed"] == 5.0
    assert len(parsed["2026-08-25T09:00"]) == 1


def test_ensemble_member_sampling_keeps_range_without_duplicates():
    selected = select_evenly(list(range(51)), 31)

    assert len(selected) == 31
    assert selected[0] == 0
    assert selected[-1] == 50
    assert selected == sorted(set(selected))


def test_open_meteo_deterministic_parser_keeps_optional_missing_fields_explicit():
    parsed = parse_deterministic_response(
        {
            "hourly": {
                "time": ["2026-08-25T08:00"],
                "wind_speed_10m": [4.0],
                "wind_direction_10m": [180.0],
                "cloud_cover_low": [20.0],
                "precipitation": [0.0],
                "visibility": [12000.0],
            }
        }
    )

    assert parsed["2026-08-25T08:00"]["visibility"] == 12.0
    assert parsed["2026-08-25T08:00"]["wind_gusts"] is None


def test_open_meteo_parser_rejects_duplicate_times_and_invalid_numbers():
    payload = {
        "hourly": {
            "time": ["2026-08-25T08:00", "2026-08-25T08:00"],
            "wind_speed_10m": [4.0, 5.0],
            "wind_direction_10m": [180.0, 180.0],
            "cloud_cover_low": [20.0, 20.0],
            "precipitation": [0.0, 0.0],
        }
    }
    with pytest.raises(ValueError, match="重複時刻"):
        parse_deterministic_response(payload)

    payload["hourly"]["time"] = ["2026-08-25T08:00", "2026-08-25T09:00"]
    payload["hourly"]["wind_speed_10m"] = [True, 5.0]
    with pytest.raises(ValueError, match="数値範囲"):
        parse_deterministic_response(payload)


def test_open_meteo_request_retries_transient_http_failure_with_bound():
    failed = Mock(status_code=503)
    failed.raise_for_status.side_effect = requests.HTTPError(response=failed)
    succeeded = Mock(status_code=200)
    succeeded.json.return_value = {
        "hourly": {
            "time": ["2026-08-25T08:00"],
            "wind_speed_10m": [4.0],
            "wind_direction_10m": [180.0],
            "cloud_cover_low": [20.0],
            "precipitation": [0.0],
        }
    }

    with patch("flight_forecast.clients.http.time.sleep") as sleep:
        result = fetch_deterministic_forecast(
            model="jma_seamless",
            latitude=33.1,
            longitude=139.7,
            endpoint="https://example.test",
            forecast_days=1,
            request_get=Mock(side_effect=[failed, succeeded]),
        )

    assert result["2026-08-25T08:00"]["wind_speed"] == 4.0
    sleep.assert_called_once_with(1)


def test_open_meteo_request_does_not_retry_non_transient_http_failure():
    failed = Mock(status_code=400)
    failed.raise_for_status.side_effect = requests.HTTPError(response=failed)
    request_get = Mock(return_value=failed)

    with (
        patch("flight_forecast.clients.http.time.sleep") as sleep,
        pytest.raises(requests.HTTPError),
    ):
        fetch_deterministic_forecast(
            model="jma_seamless",
            latitude=33.1,
            longitude=139.7,
            endpoint="https://example.test",
            forecast_days=1,
            request_get=request_get,
        )

    request_get.assert_called_once()
    sleep.assert_not_called()


def test_typhoon_parser_uses_flight_target_and_preserves_factor_fixture():
    parsed = parse_typhoon_impact_response(
        {
            "source": "jma",
            "sourceDetails": {"mode": "ensemble", "weatherProvider": "jma"},
            "scoreConfig": {
                "version": "v2",
                "targetWeights": {"flight": {"wind": 0.5}},
                "factorMaxValues": {"wind": 20},
            },
            "days": [
                {
                    "date": "2026-08-25",
                    "summaryRiskLevel": "severe",
                    "targets": {
                        "flight": {
                            "riskLevel": "high",
                            "score": 42,
                            "factors": {"wind": 20},
                            "inputs": {"windSpeedMps": 12},
                            "reasons": ["強風"],
                        }
                    },
                }
            ],
        },
        source="jma",
        valid_levels={"low", "medium", "high", "severe"},
    )

    assert parsed["2026-08-25"]["risk_level"] == "high"
    assert parsed["2026-08-25"]["factor_breakdown_available"] is True
    assert parsed["2026-08-25"]["factor_weights"] == {"wind": 0.5}


def test_typhoon_parser_rejects_invalid_or_duplicate_dates():
    payload = {
        "source": "jma",
        "days": [
            {"date": "not-a-date", "targets": {"flight": {"riskLevel": "high"}}},
            {"date": "2026-08-25", "targets": {"flight": {"riskLevel": "high"}}},
            {"date": "2026-08-25", "targets": {"flight": {"riskLevel": "severe"}}},
        ],
    }

    with pytest.raises(ValueError, match="日付の重複"):
        parse_typhoon_impact_response(payload, "jma", {"low", "medium", "high", "severe"})
