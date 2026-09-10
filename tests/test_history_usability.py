import json

import pytest
from jinja2 import Environment, FileSystemLoader

from flight_forecast.forecast_archive import build_archive_days


def history_fixture():
    rows = []
    for day, outcomes in [
        ("2026-09-08", ["運航", "運航", "欠航"]),
        ("2026-09-07", ["欠航"] * 3),
        ("2026-08-27", ["運航"] * 3),
        ("2026-08-26", ["運航", "条件付き→引返欠航", None]),
    ]:
        for number, outcome in zip(["ANA1891", "ANA1893", "ANA1895"], outcomes):
            rows.append({"forecast_target_date": day, "flight_number": number,
                "model": "jma_seamless", "probability": 95.2, "outcome_status": outcome,
                "publication_status": "published", "weather_json": json.dumps({"weather": {
                    "wind_direction": 185, "wind_speed": 6.02, "visibility": 0}}),
                "factor_breakdown_json": json.dumps({"weather_factors": {"southerly": .8}})})
    return build_archive_days(rows)


def test_history_counts_and_rendering():
    days = history_fixture()
    assert days[1]["cancelled_count"] == 3
    assert days[2]["result_class"] == "operated"
    assert days[3]["missing_count"] == days[3]["returned_count"] == 1
    assert days[3]["cancelled_count"] == 0
    env = Environment(loader=FileSystemLoader("templates"), autoescape=True)
    html = env.get_template("history.html").render(archive_days=days, structured_data={}, access_stats={})
    assert "運航0便・欠航3便" in html
    assert 'value="2026-08"' in html
    assert 'static/history.js' in html
    assert html.count('<tbody>') == 4
    detail = env.get_template("archive_date.html").render(day=days[0], structured_data={}, access_stats={})
    assert "南風 ×0.8" in detail
    assert "0 km" in detail
    assert "観測値と確認できない" in detail


@pytest.mark.parametrize("value", [None, "broken", "[]", '{"weather":null}', '{"weather":{"visibility":true}}'])
def test_missing_weather_is_not_fabricated(value):
    days = build_archive_days([{"forecast_target_date": "2026-09-01", "flight_number": "ANA1891",
        "model": "jma_seamless", "weather_json": value, "probability": None}])
    assert all(cell["value"] == "記録なし" for cell in days[0]["flights"][0]["weather"]["forecast"])
