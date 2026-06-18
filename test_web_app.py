from unittest.mock import patch

from datetime import date

from web_app import app, build_daily_forecasts, calculate_confidence, fallback_confidence


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
        "warning_msg": "特になし",
        "data_count": 10,
        "step_used": 1,
    }
    with patch("web_app.predict_flight_probability", return_value=result):
        days = build_daily_forecasts(SAMPLE_WEATHER, reference_date=date(2026, 6, 19))

    assert days[0]["date_label"] == "6/20"
    assert days[0]["flights"][0]["number"] == "ANA1891"
    assert days[0]["flights"][0]["probability"] == 88.0
    assert days[0]["confidence"]["grade"] == "B"


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

    assert confidence["grade"] == "E"
    assert confidence["member_count"] == 40


def test_fallback_confidence_decreases_with_lead_time():
    reference = date(2026, 6, 19)

    assert fallback_confidence(reference, reference)["grade"] == "A"
    assert fallback_confidence(date(2026, 6, 25), reference)["grade"] == "E"


def test_index_renders_forecast():
    result = {
        "probability": 88.0,
        "alert_required": False,
        "warning_msg": "特になし",
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
    assert "八丈島フライト予報" in response.get_data(as_text=True)
    assert "88.0" in response.get_data(as_text=True)
    assert "予測信頼度" in response.get_data(as_text=True)
    assert "なぜ作ったのか" in response.get_data(as_text=True)
    assert "ざっくりした仕組み" in response.get_data(as_text=True)
    assert "気象業務法への配慮" in response.get_data(as_text=True)
    assert "予報業務の許可について" in response.get_data(as_text=True)
    assert "6時間ごと" in response.get_data(as_text=True)


def test_index_handles_weather_api_error():
    with patch("web_app.fetch_forecast", side_effect=ValueError("bad data")):
        response = app.test_client().get("/")

    assert response.status_code == 200
    assert "現在、予報を取得できません" in response.get_data(as_text=True)


def test_health():
    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
