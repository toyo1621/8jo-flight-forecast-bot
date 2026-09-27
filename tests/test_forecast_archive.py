from flight_forecast.forecast_archive import build_archive_days


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
    assert "原因を特定できる理由記録はありません" in flight["review_evidence"]["reason"]
    assert "記録がありません" in flight["review_evidence"]["forecast"]


def test_review_uses_saved_forecast_and_factors_without_inventing_cause():
    import json

    days = build_archive_days([{
        "forecast_target_date": "2026-09-03", "flight_number": "ANA1891",
        "model": "jma_seamless", "probability": 80, "outcome_status": "欠航",
        "weather_json": json.dumps({"wind_direction": 180, "wind_speed": 5,
                                   "wind_gusts": None}),
        "factor_breakdown_json": json.dumps({"weather_factors": {"southerly": 0.8}}),
        "collected_wind_speed": 12,
    }])
    evidence = days[0]["flights"][0]["review_evidence"]
    assert "平均風速 5 m/s" in evidence["forecast"]
    assert "12 m/s" not in evidence["forecast"]
    assert "最大瞬間風速" not in evidence["forecast"]
    assert evidence["factors"] == "南風 ×0.8"
