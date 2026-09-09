import pytest

from forecast_engine import find_similar_flights, predict_flight_probability
from history_selection import select_history


def row(**changes):
    return {"date": "2026-09-01", "flight_number": "ANA1891", "status": "運航",
            "wind_direction": 350, "wind_speed": 6, "wind_gusts": 13, **changes}


@pytest.mark.parametrize("changes,accepted", [
    ({"wind_direction": 10}, True), ({"wind_direction": 10.01}, False),
    ({"wind_direction": 330}, True), ({"wind_direction": 329.99}, False),
    ({"wind_speed": 9}, True), ({"wind_speed": 9.01}, False),
    ({"wind_speed": 3}, True), ({"wind_speed": 2.99}, False),
    ({"wind_gusts": 18}, True), ({"wind_gusts": 18.01}, False),
    ({"wind_gusts": 8}, True), ({"wind_gusts": 7.99}, False),
    ({"wind_gusts": None}, False), ({"wind_gusts": True}, False),
    ({"wind_speed": float("nan")}, False), ({"wind_direction": float("inf")}, False),
    ({"wind_speed": -1}, False), ({"status": "予定"}, False),
    ({"flight_number": "ANA1893"}, False),
])
def test_strict_boundaries(changes, accepted):
    assert bool(select_history([row(**changes)], "ANA1891", row())) is accepted


def test_scoring_and_details_use_identical_candidates_without_fallback():
    history = [row(date=f"2026-08-{day:02}", status="欠航" if day == 1 else "運航") for day in range(1, 13)]
    history += [row(wind_direction=180)] * 30
    result = predict_flight_probability(350, 6, 13, 20, 15, flight_number="ANA1891", history=history)
    details = find_similar_flights("ANA1891", row(), history=history)
    assert result["data_count"] == 12
    assert result["base_probability"] == 91.7
    assert len(details) == 10
    assert details[0]["date"] == "2026-08-12"
    assert predict_flight_probability(350, 6, 13, 20, 15, flight_number="ANA1891",
        history=history[:4] + history[12:])["probability"] is None


def test_order_is_direction_then_speed_then_gust_then_date():
    history = [row(wind_direction=351), row(wind_speed=7), row(wind_gusts=14), row()]
    assert [r["wind_differences"] for r in select_history(history, "ANA1891", row())] == [
        (0, 0, 0), (0, 0, 1), (0, 1, 0), (1, 0, 0)]


def test_missing_forecast_gust_is_not_zero_and_old_tuples_are_not_inferred():
    assert predict_flight_probability(350, 6, None, 20, 15, history=[row()] * 5)["calculation_status"] == "weather_missing"
    assert select_history([("運航", 350, 6)] * 5, "ANA1891", row()) == []
