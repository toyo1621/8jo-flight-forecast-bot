from flight_forecast.visibility_statistics import build_visibility_cancellation_summary


def row(**changes):
    return {
        "date": "2026-09-01",
        "flight_number": "ANA1891",
        "status": "運航",
        "visibility": 5.0,
        "cloud_cover_low": 75.0,
        "precipitation": 0.0,
        **changes,
    }


def test_metric_bands_use_independent_denominators_and_hide_small_samples():
    history = [row()] * 4 + [
        row(status="欠航", visibility=1.0, cloud_cover_low=None, precipitation=1.5),
        row(status="条件付き→引返欠航", visibility=1.99, cloud_cover_low=100.0, precipitation=6.0),
    ]
    summary = build_visibility_cancellation_summary(history)
    visibility, cloud, rain = summary["metrics"]

    assert summary["confirmed_count"] == 6
    assert visibility["sample_count"] == 6
    assert visibility["bands"][1]["sample_count"] == 2
    assert visibility["bands"][1]["rate"] == 100.0
    assert visibility["bands"][1]["evidence"] == "insufficient"
    assert visibility["bands"][4]["sample_count"] == 4
    assert cloud["sample_count"] == 5
    assert cloud["excluded_count"] == 1
    assert cloud["bands"][3]["sample_count"] == 5
    assert cloud["bands"][3]["rate"] == 20.0
    assert cloud["bands"][3]["evidence"] == "limited"
    assert rain["bands"][0]["sample_count"] == 4
    assert rain["bands"][2]["sample_count"] == 1
    assert rain["bands"][3]["sample_count"] == 1


def test_boundary_values_and_invalid_inputs_do_not_become_zero_risk():
    history = [
        row(visibility=0.99, cloud_cover_low=0, precipitation=0.1),
        row(visibility=2.0, cloud_cover_low=25, precipitation=1.49),
        row(visibility=3.0, cloud_cover_low=50, precipitation=1.5),
        row(visibility=5.0, cloud_cover_low=75, precipitation=6.0),
        row(visibility=True, cloud_cover_low=101, precipitation=float("nan")),
        row(visibility=-1, cloud_cover_low=-1, precipitation=-1),
        row(visibility=float("inf"), cloud_cover_low=False, precipitation=None),
        row(visibility=10**1000, cloud_cover_low=None, precipitation=None),
        row(status="予定"),
        row(flight_number="OTHER"),
        "bad",
    ]
    summary = build_visibility_cancellation_summary(history)
    visibility, cloud, rain = summary["metrics"]

    assert summary["confirmed_count"] == 8
    assert [cell["sample_count"] for cell in visibility["bands"]] == [1, 0, 1, 1, 1]
    assert [cell["sample_count"] for cell in cloud["bands"]] == [1, 1, 1, 1]
    assert [cell["sample_count"] for cell in rain["bands"]] == [0, 2, 1, 1]
    assert [metric["excluded_count"] for metric in summary["metrics"]] == [4, 4, 4]
    assert all(cell["evidence"] == "insufficient" for cell in visibility["bands"])


def test_missing_weather_never_creates_a_rate():
    summary = build_visibility_cancellation_summary([row(
        status="欠航", visibility=None, cloud_cover_low=None, precipitation=None
    )])
    assert summary["confirmed_count"] == 1
    for metric in summary["metrics"]:
        assert metric["sample_count"] == 0
        assert metric["excluded_count"] == 1
        assert metric["first_date"] is None
        assert all(cell["rate"] is None for cell in metric["bands"])
