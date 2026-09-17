from flight_forecast.wind_statistics import (
    build_wind_cancellation_summary,
    direction_index,
)


def row(**changes):
    return {
        "date": "2026-09-01",
        "status": "運航",
        "wind_direction": 180.0,
        "wind_speed": 5.0,
        "wind_gusts": 12.0,
        "status_reason_category": "not_applicable",
        **changes,
    }


def test_direction_index_wraps_north_and_uses_sixteen_compass_points():
    assert direction_index(0) == 0
    assert direction_index(360) == 0
    assert direction_index(11.24) == 0
    assert direction_index(11.25) == 1
    assert direction_index(180) == 8
    assert direction_index(348.75) == 0
    assert direction_index(True) is None
    assert direction_index(float("nan")) is None


def test_summary_counts_operated_and_cancelled_outcomes_by_speed_and_gust():
    history = [row()] * 3 + [
        row(status="欠航", status_reason_category="weather"),
        row(status="条件付き→引返欠航", status_reason_category="unknown"),
        row(status="運航(条件付)"),
    ]

    summary = build_wind_cancellation_summary(history)
    south = summary["rows"][8]

    assert summary["sample_count"] == 6
    assert summary["operated_count"] == 4
    assert summary["cancelled_count"] == 2
    assert summary["weather_reason_count"] == 1
    assert south["speed_cells"][1] == {
        "key": "4-to-6-5",
        "label": "4〜6.5m/s未満",
        "sample_count": 6,
        "cancelled_count": 2,
        "rate": 33.3,
        "evidence": "limited",
        "tone": "high",
    }
    assert south["gust_cells"][1]["rate"] == 33.3


def test_summary_hides_rates_as_insufficient_below_public_sample_minimum():
    summary = build_wind_cancellation_summary([row(status="欠航")] * 4)
    cell = summary["rows"][8]["speed_cells"][1]

    assert cell["rate"] == 100.0
    assert cell["evidence"] == "insufficient"
    assert cell["tone"] == "insufficient"


def test_summary_publishes_rate_at_five_samples():
    summary = build_wind_cancellation_summary(
        [row(status="欠航"), row(), row(), row(), row()]
    )
    south = summary["rows"][8]
    cell = south["speed_cells"][1]

    assert south["center_degrees_label"] == "180"
    assert summary["rows"][1]["center_degrees_label"] == "22.5"
    assert cell["sample_count"] == 5
    assert cell["rate"] == 20.0
    assert cell["evidence"] == "limited"


def test_summary_excludes_invalid_weather_and_unknown_statuses():
    summary = build_wind_cancellation_summary(
        [
            row(wind_direction=None),
            row(wind_speed=-1),
            row(wind_gusts=None),
            row(status="予定"),
            "invalid",
        ]
    )

    assert summary["sample_count"] == 0
    assert summary["first_date"] is None
    assert summary["last_date"] is None
