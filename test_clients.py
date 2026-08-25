from clients.open_meteo import parse_deterministic_response, parse_ensemble_response
from clients.typhoon_impact import parse_typhoon_impact_response


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
