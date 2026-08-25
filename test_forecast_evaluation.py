from datetime import date

from forecast_evaluation import (
    brier_score,
    evaluate_rows,
    expected_calibration_error,
    factor_ablation_evaluation,
    markdown_report,
    partition_evaluable_predictions,
    rolling_time_evaluation,
)


def _row(
    target_date="2026-08-20",
    probability=80.0,
    outcome_status="運航",
    model="jma_seamless",
    flight_number="ANA1891",
    lead_hours=24,
    generated="2026-08-19T00:00:00+09:00",
    valid="2026-08-20T08:00:00+09:00",
    provenance="known",
    category=None,
    code_version="test-code",
    config_version="test-config",
):
    return {
        "forecast_target_date": target_date,
        "probability": probability,
        "outcome_status": outcome_status,
        "model": model,
        "flight_number": flight_number,
        "lead_hours": lead_hours,
        "calculation_status": "available",
        "prediction_generated_at": generated,
        "weather_retrieved_at": "2026-08-18T00:00:00+09:00",
        "weather_valid_at": valid,
        "provenance_status": provenance,
        "status_reason_category": category,
        "code_version": code_version,
        "config_version": config_version,
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


def test_weather_only_population_excludes_non_weather_and_unknown_cancellations():
    eligible, excluded = partition_evaluable_predictions(
        [
            _row(outcome_status="欠航", category="weather"),
            _row(outcome_status="欠航", category="operational"),
            _row(outcome_status="欠航", category="unknown"),
        ],
        population="weather_only",
    )

    assert len(eligible) == 1
    assert excluded == {"non_weather_or_unknown_cancellation": 2}


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
    assert report["weather_only_count"] == 0
    assert "評価可能な予測値がありません" in markdown_report(report)


def test_factor_ablation_compares_recorded_factors_without_reconstructing_them():
    rows = [
        _row(
            probability=50.0,
            outcome_status="運航",
        )
        | {
            "factor_breakdown_json": (
                '{"ablation": {"base": 80, "weather_only": 70, '
                '"typhoon_only": 64, "combined": 56}}'
            ),
        },
        _row(
            probability=40.0,
            outcome_status="欠航",
            category="weather",
        )
        | {
            "factor_breakdown_json": (
                '{"ablation": {"base": 80, "weather_only": 60, '
                '"typhoon_only": 64, "combined": 48}}'
            ),
        },
    ]

    report = factor_ablation_evaluation(rows)

    assert report["status"] == "ok"
    assert report["all"]["base"]["count"] == 2
    assert report["all"]["combined"]["count"] == 2
    assert report["weather_only"]["combined"]["count"] == 2


def test_evaluation_reports_flight_lead_day_and_conditional_status_sensitivity():
    rows = [
        _row(flight_number="ANA1891", lead_hours=23, probability=80.0),
        _row(
            flight_number="ANA1893",
            lead_hours=49,
            probability=60.0,
            outcome_status="運航(条件付)",
        ),
    ]

    report = evaluate_rows(rows)

    assert report["by_flight"]["ANA1891"]["count"] == 1
    assert report["by_lead_day"]["0"]["count"] == 1
    assert report["by_lead_day"]["2"]["count"] == 1
    assert report["by_version"]["test-code@test-config"]["count"] == 2
    assert report["conditional_status_sensitivity"]["current_as_operated"]["eligible_count"] == 2
    assert report["conditional_status_sensitivity"]["as_non_operated"]["eligible_count"] == 2
    assert report["reason_coverage_by_flight"] == {}
    markdown = markdown_report(report)
    assert "便別・リード日別指標" in markdown
    assert "条件付運航の感度分析" in markdown
