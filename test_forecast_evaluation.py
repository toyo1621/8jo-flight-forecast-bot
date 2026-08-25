from datetime import date

from forecast_evaluation import (
    brier_score,
    evaluate_rows,
    expected_calibration_error,
    markdown_report,
    partition_evaluable_predictions,
    rolling_time_evaluation,
)


def _row(
    target_date="2026-08-20",
    probability=80.0,
    outcome_status="運航",
    model="jma_seamless",
    generated="2026-08-19T00:00:00+09:00",
    valid="2026-08-20T08:00:00+09:00",
    provenance="known",
):
    return {
        "forecast_target_date": target_date,
        "probability": probability,
        "outcome_status": outcome_status,
        "model": model,
        "calculation_status": "available",
        "prediction_generated_at": generated,
        "weather_retrieved_at": "2026-08-18T00:00:00+09:00",
        "weather_valid_at": valid,
        "provenance_status": provenance,
    }


def test_partition_excludes_unknown_provenance_and_future_leakage():
    eligible, excluded = partition_evaluable_predictions(
        [
            _row(),
            _row(provenance="unknown"),
            _row(generated="2026-08-21T00:00:00+09:00"),
        ]
    )

    assert len(eligible) == 1
    assert excluded == {"unknown_provenance": 1, "prediction_after_valid_time": 1}


def test_brier_and_calibration_metrics_are_computed_in_probability_units():
    rows = [
        {"probability": 100.0, "outcome": 1},
        {"probability": 0.0, "outcome": 0},
    ]

    assert brier_score(rows) == 0.0
    assert expected_calibration_error(rows) == 0.0


def test_rolling_time_evaluation_uses_only_prior_dates_for_training():
    rows = [
        {"target_date": date(2026, 8, 20), "probability": 80.0, "outcome": 1, "model": "jma"},
        {"target_date": date(2026, 8, 21), "probability": 70.0, "outcome": 0, "model": "jma"},
        {"target_date": date(2026, 8, 22), "probability": 60.0, "outcome": 1, "model": "jma"},
    ]

    folds = rolling_time_evaluation(rows, min_train_dates=1)

    assert [fold["train_end"] for fold in folds] == ["2026-08-20", "2026-08-21"]
    assert [fold["test_date"] for fold in folds] == ["2026-08-21", "2026-08-22"]
    assert folds[0]["baseline_prior_percent"] == 100.0
    assert folds[1]["baseline_prior_percent"] == 50.0


def test_evaluate_rows_reports_insufficient_data_without_inventing_metrics():
    report = evaluate_rows([_row(provenance="unknown")])

    assert report["status"] == "insufficient_data"
    assert report["eligible_count"] == 0
    assert report["models"] == {}
    assert "評価可能な予測値がありません" in markdown_report(report)
