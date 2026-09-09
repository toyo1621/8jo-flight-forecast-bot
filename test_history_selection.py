import pytest

from forecast_engine import find_similar_flights, predict_flight_probability
from history_selection import (
    select_candidates,
    select_history,
    select_history_with_metadata,
)


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
    assert bool(select_candidates([row(**changes)], "ANA1891", row())) is accepted


def test_five_strict_records_do_not_expand():
    rows, metadata = select_history_with_metadata([row()] * 5 + [row(wind_speed=10)] * 10, "ANA1891", row())
    assert len(rows) == 5
    assert not metadata["expanded"]


def test_four_records_expand_to_at_least_six_and_details_match():
    history = [row()] * 4 + [row(wind_speed=10)] * 2
    rows, metadata = select_history_with_metadata(history, "ANA1891", row())
    assert len(rows) == 6
    assert metadata["step"] == 2
    result = predict_flight_probability(350, 6, 13, 20, 15, flight_number="ANA1891", history=history)
    assert result["data_count"] == len(find_similar_flights("ANA1891", row(), history=history)) == 6
    assert result["history_selection"] == metadata


def test_expanded_five_is_not_enough_and_angles_are_bounded():
    history = [row()] * 4 + [row(wind_speed=10)] + [row(wind_direction=180)] * 20
    result = predict_flight_probability(350, 6, 13, 20, 15, flight_number="ANA1891", history=history)
    assert result["probability"] is None
    assert result["history_selection"]["angle"] == 45


@pytest.mark.parametrize("direction,step", [(20, 3), (35, 4)])
def test_direction_expansion_only_after_speed_expansion(direction, step):
    rows, metadata = select_history_with_metadata([row()] * 4 + [row(wind_direction=direction)] * 2, "ANA1891", row())
    assert len(rows) == 6
    assert metadata["step"] == step


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
