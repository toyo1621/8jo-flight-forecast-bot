from datetime import date
from unittest.mock import patch

from web_app import (
    BASE_DIR,
    app,
    build_daily_forecasts,
    calculate_confidence,
    fallback_confidence,
    _select_evenly,
    wind_direction_label,
)


SAMPLE_WEATHER = {
    "2026-06-20T08:00": {
        "wind_direction": 180.0,
        "wind_speed": 4.0,
        "wind_gusts": 7.0,
        "cloud_cover_low": 20.0,
        "visibility": 15.0,
    }
}


def test_build_daily_forecasts():
    result = {
        "probability": 88.0,
        "alert_required": False,
        "warning_msg": "なし",
        "data_count": 10,
        "step_used": 1,
    }
    with patch("web_app.predict_flight_probability", return_value=result):
        days = build_daily_forecasts(SAMPLE_WEATHER, reference_date=date(2026, 6, 19))

    assert days[0]["date_label"] == "6/20"
    assert days[0]["flights"][0]["number"] == "ANA1891"
    assert days[0]["flights"][0]["probability"] == 88.0
    assert days[0]["flights"][0]["wind_direction_label"] == "南"
    assert days[0]["confidence"]["grade"] == "B"


def test_wind_direction_label_uses_sixteen_points():
    assert wind_direction_label(0) == "北"
    assert wind_direction_label(45) == "北東"
    assert wind_direction_label(225) == "南西"
    assert wind_direction_label(359) == "北"
    assert wind_direction_label(None) is None


def test_calculate_confidence_uses_ensemble_spread():
    members = [
        {
            "wind_direction": 180.0,
            "wind_speed": float(value),
            "wind_gusts": 7.0,
            "cloud_cover_low": 20.0,
            "visibility": 15.0,
        }
        for value in range(40)
    ]

    with patch(
        "web_app.predict_flight_probability",
        side_effect=lambda **weather: {"probability": weather["wind_speed"]},
    ):
        confidence = calculate_confidence(members)

    assert confidence["grade"] == "D"
    assert confidence["member_count"] == 40


def test_confidence_note_uses_short_wording():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert "(天候信頼度は{{ day.confidence.member_count }}通りの天気予測から{{ day.confidence.spread }}ptと判定)" in template


def test_select_evenly_balances_ensemble_members():
    members = list(range(51))

    selected = _select_evenly(members, 31)

    assert len(selected) == 31
    assert selected[0] == 0
    assert selected[-1] == 50
    assert selected == sorted(set(selected))


def test_fallback_confidence_decreases_with_lead_time():
    reference = date(2026, 6, 19)

    assert fallback_confidence(reference, reference)["grade"] == "A"
    assert fallback_confidence(date(2026, 6, 25), reference)["grade"] == "E"


def test_index_renders_forecast():
    result = {
        "probability": 88.0,
        "alert_required": False,
        "warning_msg": "なし",
        "data_count": 10,
        "step_used": 1,
    }
    with (
        patch("web_app.fetch_forecast", return_value=SAMPLE_WEATHER),
        patch("web_app.fetch_ensemble_forecast", return_value={}),
        patch("web_app.predict_flight_probability", return_value=result),
        app.test_client() as client,
    ):
        response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "八丈島フライト予報" in body
    assert "天候信頼度" in body
    assert "風向 南 180°" in body
    assert "雲量 20%" in body
    assert "なぜ作ったか" in body
    assert "ざっくりどういう仕組みか" in body
    assert "気象業法への配慮" in body
    assert "6ポイント以内" not in body


def test_index_handles_weather_api_error():
    with patch("web_app.fetch_forecast", side_effect=ValueError("bad data")):
        response = app.test_client().get("/")

    assert response.status_code == 200
    assert "現在、予報を取得できません" in response.get_data(as_text=True)


def test_health():
    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
