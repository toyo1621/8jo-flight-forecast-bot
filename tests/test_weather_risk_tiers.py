import math

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


def test_gust_and_wind_show_both_labels_with_half_secondary_penalty():
    result = predict_flight_probability(
        90.0, 10.0, 12.0, 20.0, 15.0, history=history(10.0, 12.0)
    )

    assert result["weather_factors"] == {"wind": 0.8, "gust": 0.975}
    assert result["weather_factor"] == 0.78
    assert result["probability"] == 78.0
    assert "突風注意小" in result["warning_msg"]
    assert "強風注意大" in result["warning_msg"]


@pytest.mark.parametrize(
    "direction,speed,gust,factors,combined",
    [
        (150, 10, 16, {"southerly": 0.6, "gust": 0.9}, 0.54),
        (210, 5, 21, {"gust": 0.6, "southerly": 0.95}, 0.57),
        (90, 6.5, 20, {"gust": 0.6, "wind": 0.95}, 0.57),
        (150, 10, 21, {"southerly": 0.6, "gust": 0.8}, 0.48),
        (90, 10, 16, {"wind": 0.8, "gust": 0.9}, 0.72),
        (150, 10, 9.99, {"southerly": 0.6}, 0.6),
        (90, 3.99, 20, {"gust": 0.6}, 0.6),
        (90, 3.99, 9.99, {}, 1.0),
    ],
)
def test_mean_wind_and_gust_combine_without_counting_southerly_and_wind_twice(
    direction, speed, gust, factors, combined
):
    rows = [{**row, "wind_direction": direction} for row in history(speed, gust)]
    result = predict_flight_probability(direction, speed, gust, 20, 15, history=rows)

    assert result["weather_factors"] == factors
    assert result["weather_factor"] == combined
    assert result["probability"] == round(min(97, 100 * combined), 1)
    assert result["data_count"] == 5


@pytest.mark.parametrize("direction", [90, 150, 210])
def test_wind_boundaries_never_raise_score_and_keep_effective_factor_product(direction):
    results = []
    for speed in (3.99, 4.0, 6.49, 6.5, 8.99, 9.0, 9.99, 10.0):
        row = []
        for gust in (9.99, 10.0, 14.99, 15.0, 19.99, 20.0):
            rows = [{**item, "wind_direction": direction} for item in history(speed, gust)]
            result = predict_flight_probability(direction, speed, gust, 20, 15, history=rows)
            assert math.prod(result["weather_factors"].values()) == pytest.approx(
                result["weather_factor"]
            )
            row.append(result["probability"])
        results.append(row)

    for scores in [*results, *zip(*results)]:
        assert list(scores) == sorted(scores, reverse=True)
