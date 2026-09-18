import pytest

from flight_forecast.forecast_engine import predict_flight_probability


def history(wind_speed, wind_gusts):
    return [
        {
            "status": "運航",
            "wind_direction": 90.0,
            "wind_speed": wind_speed,
            "wind_gusts": wind_gusts,
        }
    ] * 5


@pytest.mark.parametrize(
    "gust,factor,label",
    [
        (9.99, 1.0, None),
        (10.0, 0.95, "突風注意小"),
        (14.99, 0.95, "突風注意小"),
        (15.0, 0.8, "突風注意中"),
        (19.99, 0.8, "突風注意中"),
        (20.0, 0.6, "突風注意大"),
    ],
)
def test_gust_risk_tiers(gust, factor, label):
    result = predict_flight_probability(
        90.0, 3.0, gust, 20.0, 15.0, history=history(3.0, gust)
    )

    assert result["weather_factor"] == factor
    assert result["probability"] == min(97.0, 100.0 * factor)
    if label:
        assert label in result["warning_msg"]
    else:
        assert "突風注意" not in result["warning_msg"]


@pytest.mark.parametrize(
    "speed,factor,label",
    [
        (3.99, 1.0, None),
        (4.0, 0.95, "強風注意小"),
        (6.49, 0.95, "強風注意小"),
        (6.5, 0.9, "強風注意中"),
        (9.99, 0.9, "強風注意中"),
        (10.0, 0.8, "強風注意大"),
    ],
)
def test_wind_risk_tiers(speed, factor, label):
    result = predict_flight_probability(
        90.0, speed, 5.0, 20.0, 15.0, history=history(speed, 5.0)
    )

    assert result["weather_factor"] == factor
    assert result["probability"] == min(97.0, 100.0 * factor)
    if label:
        assert label in result["warning_msg"]
    else:
        assert "強風注意" not in result["warning_msg"]


@pytest.mark.parametrize(
    "precipitation,factor,label",
    [
        (1.49, 1.0, None),
        (1.5, 0.85, "降水注意弱"),
        (5.99, 0.85, "降水注意弱"),
        (6.0, 0.7, "降水注意強"),
        (7.99, 0.7, "降水注意強"),
        (8.0, 0.7, "降水注意強"),
    ],
)
def test_precipitation_risk_tiers(precipitation, factor, label):
    result = predict_flight_probability(
        90.0,
        3.0,
        5.0,
        20.0,
        15.0,
        precipitation=precipitation,
        history=history(3.0, 5.0),
    )

    assert result["weather_factor"] == factor
    assert result["probability"] == min(97.0, 100.0 * factor)
    if label:
        assert label in result["warning_msg"]
    else:
        assert "降水注意" not in result["warning_msg"]


def test_gust_and_wind_show_both_labels_but_apply_only_strongest_wind_factor():
    result = predict_flight_probability(
        90.0, 10.0, 12.0, 20.0, 15.0, history=history(10.0, 12.0)
    )

    assert result["weather_factors"] == {"wind": 0.8}
    assert result["probability"] == 80.0
    assert "突風注意小" in result["warning_msg"]
    assert "強風注意大" in result["warning_msg"]
