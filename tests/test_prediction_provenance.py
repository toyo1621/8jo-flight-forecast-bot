import json

from flight_forecast.prediction_provenance import (
    build_artifact_id,
    build_prediction_snapshot_rows,
)


def test_artifact_identity_includes_the_snapshot_set():
    common = ("run-1", 1, "2026-08-24T02:00:00+09:00", "sha", "config")

    assert build_artifact_id(*common, snapshot_ids=["a"]) != build_artifact_id(
        *common, snapshot_ids=["b"]
    )
    assert build_artifact_id(*common, snapshot_ids=["b", "a"]) == build_artifact_id(
        *common, snapshot_ids=["a", "b"]
    )


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
    assert rows[0]["publication_tracking_enabled"] is True
    assert rows[1]["fallback_used"] is True
    assert rows[1]["calculation_status"] == "available"
    assert rows[2]["probability"] is None
    assert rows[2]["calculation_status"] == "invalid_output"
    assert json.loads(rows[1]["model_input_json"])["members"] == []
    assert json.loads(rows[0]["model_input_json"])["weather"]["wind_speed"] == 4.0
    assert json.loads(rows[0]["weather_field_sources_json"])["wind_gusts"] == "open_meteo_supplement"
    breakdown = json.loads(rows[0]["factor_breakdown_json"])
    assert breakdown["external_typhoon"]["risk_level"] == "low"
    assert breakdown["ablation"] == {}


def test_prediction_snapshot_marks_missing_retrieval_as_unknown():
    days = [
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
    ]
    bundle = {"source_updated_at": {}, "source_fallbacks": {}}
    rows = build_prediction_snapshot_rows(
        days,
        bundle,
        generated_at="2026-08-24T02:00:00+09:00",
        run_id="run-2",
    )
    resent = build_prediction_snapshot_rows(
        days,
        bundle,
        generated_at="2026-08-24T03:00:00+09:00",
        run_id="run-3",
    )

    assert rows[0]["provenance_status"] == "unknown"
    assert rows[0]["calculation_status"] == "insufficient_history"
    assert rows[0]["snapshot_id"] == resent[0]["snapshot_id"]


def test_snapshot_identity_deduplicates_same_content_but_changes_with_input():
    day = {
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
                "_ensemble_member_inputs": [
                    {
                        "model": "gfs_seamless",
                        "member_id": "gfs:01",
                        "weather": {"wind_speed": 5.0},
                    }
                ],
                "confidence": {
                    "models": {
                        "gfs_seamless": {"status": "available", "member_count": 1},
                        "ecmwf_ifs025": {"status": "unavailable", "member_count": 0},
                    }
                },
            }
        ],
    }
    bundle = {
        "source_updated_at": {
            "weather": "2026-08-24T00:00:00+09:00",
            "ensembles": "2026-08-24T01:00:00+09:00",
        },
        "source_fallbacks": {},
        "typhoon_impacts": {},
        "config_version": "test-config",
    }

    first = build_prediction_snapshot_rows(
        [day], bundle, generated_at="2026-08-24T02:00:00+09:00", run_id="run-1"
    )
    resent = build_prediction_snapshot_rows(
        [day], bundle, generated_at="2026-08-24T03:00:00+09:00", run_id="run-2"
    )
    changed = {**day, "flights": [{**day["flights"][0], "wind_speed": 6.0}]}
    changed_rows = build_prediction_snapshot_rows(
        [changed], bundle, generated_at="2026-08-24T02:00:00+09:00", run_id="run-1"
    )

    assert [row["snapshot_id"] for row in first] == [row["snapshot_id"] for row in resent]
    assert first[0]["run_id"] == "run-1"
    assert resent[0]["run_id"] == "run-2"
    assert first[0]["snapshot_id"] != changed_rows[0]["snapshot_id"]
    assert json.loads(first[1]["weather_json"])["members"][0]["weather"]["wind_speed"] == 5.0
    assert json.loads(first[0]["weather_json"])["model"] == "jma_seamless"


def test_snapshot_identity_ignores_retrieval_clock_for_the_same_input():
    day = {
        "date": "2026-08-25",
        "flights": [
            {
                "raw_number": "ANA1891",
                "forecast_hour": 8,
                "calculation_status": "available",
                "probability": 82.0,
                "jma_probability": 82.0,
                "wind_direction": 180.0,
                "wind_speed": 4.0,
            }
        ],
    }
    first = build_prediction_snapshot_rows(
        [day],
        {"source_updated_at": {"weather": "2026-08-24T00:00:00+09:00"}},
        generated_at="2026-08-24T02:00:00+09:00",
        run_id="run-1",
    )
    resent = build_prediction_snapshot_rows(
        [day],
        {"source_updated_at": {"weather": "2026-08-24T01:00:00+09:00"}},
        generated_at="2026-08-24T03:00:00+09:00",
        run_id="run-2",
    )

    assert first[0]["snapshot_id"] == resent[0]["snapshot_id"]
    assert first[0]["weather_retrieved_at"] != resent[0]["weather_retrieved_at"]
