import pytest

from ensemble_evaluation import risk_labels
from forecast_archive import build_archive_days
from forecast_engine import predict_flight_probability


@pytest.mark.parametrize("direction,speed,label,factor", [
    (119.99, 9, None, 1), (120, 3.99, None, 1),
    (120, 4, "中", .8), (190, 4, "中", .8),
    (190.01, 4, "小", .9), (240, 4, "小", .9),
    (240.01, 9, None, 1), (185, 6.02, "中", .8),
    (185, 6.49, "中", .8), (185, 6.5, "大", .7),
    (185, 8.99, "大", .7), (185, 9, "特大", .6),
    (210, 6.5, "中", .8), (210, 9, "大", .7),
])
def test_southerly_boundaries(direction, speed, label, factor):
    result = predict_flight_probability(direction, speed, 10, 20, 15,
        history=[("運航", direction, speed)] * 5)
    assert result["weather_factor"] == factor
    assert result["probability"] == min(97, 100 * factor)
    if label:
        assert risk_labels(result["warning_msg"]) == [f"南風リスク{label}"]
    else:
        assert "南風リスク" not in result["warning_msg"]


def test_gust_does_not_stack_with_southerly():
    result = predict_flight_probability(185, 11, 21, 20, 15,
        history=[("運航", 185, 11)] * 5)
    assert result["weather_factors"] == {"gust": .55}
    assert result["probability"] == 55


def test_reflection_keeps_original_score_and_only_targets_final_flight():
    rows = [{"forecast_target_date": "2026-09-08", "flight_number": number,
        "model": "jma_seamless", "probability": 95.2, "outcome_status": outcome}
        for number, outcome in [("ANA1891", "運航"), ("ANA1893", "運航"), ("ANA1895", "欠航")]]
    flights = build_archive_days(rows)[0]["flights"]
    assert all("申し訳" not in flight["reflection"] for flight in flights[:2])
    assert flights[2]["score"] == 95
    assert "運営者の確認" in flights[2]["reflection"]
    assert "リスク補正を追加" in flights[2]["reflection"]
