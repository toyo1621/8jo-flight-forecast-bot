from forecast_archive import build_archive_days


def test_archive_distinguishes_missing_outcome_from_cancellation():
    days = build_archive_days(
        [
            {
                "forecast_target_date": "2026-09-04",
                "flight_number": "ANA1891",
                "model": "jma_seamless",
                "calculation_status": "available",
                "probability": 42.4,
                "prediction_generated_at": "2026-09-04T06:00:00+09:00",
                "outcome_status": None,
            }
        ]
    )

    flight = days[0]["flights"][0]
    assert flight["score"] == 42
    assert flight["outcome"] is None
    assert flight["outcome_confirmed"] is False
    assert "未取得" in flight["reflection"]


def test_archive_marks_high_score_cancellation_as_prediction_limit():
    days = build_archive_days(
        [
            {
                "forecast_target_date": "2026-09-03",
                "flight_number": "ANA1893",
                "model": "jma_seamless",
                "calculation_status": "available",
                "probability": 91,
                "prediction_generated_at": "2026-09-03T06:00:00+09:00",
                "outcome_status": "欠航",
            }
        ]
    )

    flight = days[0]["flights"][1]
    assert flight["outcome"] == "欠航"
    assert "予測の限界" in flight["reflection"]
