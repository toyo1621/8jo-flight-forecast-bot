import pytest

from ensemble_evaluation import format_risk_summary, risk_labels
from forecast_engine import predict_flight_probability


@pytest.mark.parametrize("visibility,label,factor", [
    (0, "特大", .6), (0.5, "特大", .6), (0.999, "特大", .6),
    (1, "大", .7), (1.3, "大", .7), (1.999, "大", .7),
    (2, "中", .8), (2.3, "中", .8), (2.999, "中", .8),
    (3, "小", .9), (4.3, "小", .9), (4.999, "小", .9),
    (5, None, 1), (10, None, 1), (None, None, 1),
])
def test_visibility_tiers(visibility, label, factor):
    result = predict_flight_probability(90, 5, 8, 20, visibility,
        history=[("運航", 90, 5)] * 5)
    assert result["weather_factor"] == factor
    assert result["probability"] == min(97, factor * 100)
    if label:
        expected = f"視程不良リスク{label}"
        assert result["warning_msg"] == f"{expected}（{visibility:g}km）"
        assert risk_labels(result["warning_msg"]) == [expected]
        assert format_risk_summary({expected: 2}, 3) == f"{expected} (2/3通り)"
        assert result["weather_factors"] == {"visibility": factor}
    else:
        assert "視程不良リスク" not in result["warning_msg"]
        assert "visibility" not in result["weather_factors"]


def test_visibility_multiplies_southerly_risk():
    result = predict_flight_probability(185, 6.02, 13, 20, 2.3,
        history=[("運航", 185, 6.02)] * 5)
    assert result["weather_factors"] == {"visibility": .8, "southerly": .8}
    assert result["probability"] == 64
