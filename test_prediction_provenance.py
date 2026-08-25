import json

from prediction_provenance import build_prediction_snapshot_rows


def test_prediction_snapshot_rows_capture_time_and_source_provenance():
    days = [
        {
            "date": "2026-08-25",
            "flights": [
                {
                    "raw_number": "ANA1891",
                    "forecast_hour": 8,
                    "calculation_status": "available",
                    "probability": 82.0,
                    "jma_probability": 82.0,
                    "gfs_probability": 75.0,
                    "ecmwf_probability": None,
                    "wind_direction": 180.0,
                    "wind_speed": 4.0,
                    "wind_gusts": 7.0,
                    "cloud_cover_low": 20.0,
                    "visibility": 15.0,
                    "_weather_field_sources": {
                        "wind_direction": "jma",
                        "wind_speed": "jma",
                        "wind_gusts": "open_meteo_supplement",
                        "cloud_cover_low": "jma",
                        "visibility": "open_meteo_supplement",
                    },
                }
            ],
        }
    ]
    bundle = {
        "source_updated_at": {
            "weather": "2026-08-24T00:00:00+09:00",
            "ensembles": "2026-08-24T01:00:00+09:00",
        },
        "source_fallbacks": {"weather": False, "ensembles": True},
        "typhoon_impacts": {"2026-08-25": "low"},
        "config_version": "test-config",
    }

    rows = build_prediction_snapshot_rows(
        days,
        bundle,
        generated_at="2026-08-24T02:00:00+09:00",
        run_id="run-1",
    )

    assert [row["model"] for row in rows] == [
        "jma_seamless",
        "gfs_seamless",
        "ecmwf_ifs025",
    ]
    assert rows[0]["weather_valid_at"] == "2026-08-25T08:00:00+09:00"
    assert rows[0]["lead_hours"] == 32
    assert rows[0]["provenance_status"] == "known"
    assert rows[0]["fallback_used"] is False
    assert rows[1]["fallback_used"] is True
    assert rows[1]["calculation_status"] == "available"
    assert rows[2]["probability"] is None
    assert rows[2]["calculation_status"] == "available"
    assert json.loads(rows[0]["weather_field_sources_json"])["wind_gusts"] == "open_meteo_supplement"
    breakdown = json.loads(rows[0]["factor_breakdown_json"])
    assert breakdown["external_typhoon"]["risk_level"] == "low"
    assert breakdown["ablation"] == {}


def test_prediction_snapshot_marks_missing_retrieval_as_unknown():
    rows = build_prediction_snapshot_rows(
        [
            {
                "date": "2026-08-25",
                "flights": [
                    {
                        "raw_number": "ANA1891",
                        "forecast_hour": 8,
                        "probability": None,
                        "calculation_status": "insufficient_history",
                    }
                ],
            }
        ],
        {"source_updated_at": {}, "source_fallbacks": {}},
        generated_at="2026-08-24T02:00:00+09:00",
        run_id="run-2",
    )

    assert rows[0]["provenance_status"] == "unknown"
    assert rows[0]["calculation_status"] == "insufficient_history"
