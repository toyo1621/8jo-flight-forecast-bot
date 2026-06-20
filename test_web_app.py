from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock, patch

from forecast_engine import MAX_PROBABILITY, find_similar_flights, predict_flight_probability
from web_app import (
    BASE_DIR,
    FORECAST_DAYS,
    app,
    build_daily_forecasts,
    calculate_confidence,
    calculate_model_reference_probabilities,
    fallback_confidence,
    _select_evenly,
    _with_model_difference_warning,
    fetch_jma_forecast,
    wind_direction_label,
)

JST = timezone(timedelta(hours=9))


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
        days = build_daily_forecasts(
            SAMPLE_WEATHER,
            reference_date=date(2026, 6, 19),
            current_time=datetime(2026, 6, 19, 12, 0, tzinfo=JST),
        )

    assert days[0]["date_label"] == "6/20"
    assert days[0]["flights"][0]["number"] == "ANA1891(1便)"
    assert days[0]["flights"][0]["probability"] == 88.0
    assert days[0]["flights"][0]["wind_direction_label"] == "南"
    assert days[0]["confidence"]["grade"] == "B"


def test_forecast_period_reaches_ten_days_ahead():
    assert FORECAST_DAYS == 11


def test_jma_forecast_requests_jma_seamless_model():
    response = Mock()
    response.json.return_value = {
        "hourly": {
            "time": ["2026-06-20T08:00"],
            "wind_speed_10m": [5.0],
            "wind_direction_10m": [180.0],
            "wind_gusts_10m": [8.0],
            "cloud_cover_low": [20.0],
            "visibility": [15000.0],
        }
    }
    with patch("web_app.requests.get", return_value=response) as get:
        result = fetch_jma_forecast()

    response.raise_for_status.assert_called_once()
    assert get.call_args.kwargs["params"]["models"] == "jma_seamless"
    assert result["2026-06-20T08:00"]["visibility"] == 15.0


def test_model_difference_warning_uses_twenty_point_boundary():
    result = {"probability": 80.0, "warning_msg": "特になし", "alert_required": False}

    warned = _with_model_difference_warning(result, 60.0)
    quiet = _with_model_difference_warning(result, 60.1)

    assert warned["warning_msg"] == "気象モデル差に注意"
    assert warned["alert_required"] is True
    assert quiet["warning_msg"] == "特になし"


def test_today_flight_disappears_after_arrival_plus_30_minutes():
    weather = {
        f"2026-06-20T{hour:02d}:00": SAMPLE_WEATHER["2026-06-20T08:00"]
        for hour in (8, 13, 17)
    }
    current_time = datetime(2026, 6, 20, 9, 1, tzinfo=JST)

    with patch("web_app.predict_flight_probability", return_value={"probability": 88.0}):
        days = build_daily_forecasts(weather, current_time=current_time)

    assert [flight["raw_number"] for flight in days[0]["flights"]] == ["ANA1893", "ANA1895"]


def test_today_flight_remains_at_exactly_arrival_plus_30_minutes():
    current_time = datetime(2026, 6, 20, 9, 0, tzinfo=JST)

    with patch("web_app.predict_flight_probability", return_value={"probability": 88.0}):
        days = build_daily_forecasts(SAMPLE_WEATHER, current_time=current_time)

    assert days[0]["flights"][0]["raw_number"] == "ANA1891"


def test_wind_direction_label_uses_sixteen_points():
    assert wind_direction_label(0) == "北"
    assert wind_direction_label(45) == "北東"
    assert wind_direction_label(225) == "南西"
    assert wind_direction_label(359) == "北"
    assert wind_direction_label(None) is None


def test_find_similar_flights_filters_same_flight_and_orders_by_weather():
    history = [
        {"date": "2026-01-01", "flight_number": "ANA1891", "flight_display_name": "ANA1891(1便)", "status": "通常", "status_reason": None, "wind_direction": 180.0, "wind_speed": 5.0, "wind_gusts": 8.0, "cloud_cover_low": 20.0, "visibility": 10.0},
        {"date": "2026-01-02", "flight_number": "ANA1891", "flight_display_name": "ANA1891(1便)", "status": "欠航", "status_reason": "強風", "wind_direction": 260.0, "wind_speed": 14.0, "wind_gusts": 20.0, "cloud_cover_low": 90.0, "visibility": 5.0},
        {"date": "2026-01-03", "flight_number": "ANA1893", "flight_display_name": "ANA1893(2便)", "status": "通常", "status_reason": None, "wind_direction": 180.0, "wind_speed": 5.0, "wind_gusts": 8.0, "cloud_cover_low": 20.0, "visibility": 10.0},
    ]
    weather = {"wind_direction": 182.0, "wind_speed": 5.2, "wind_gusts": 8.0, "cloud_cover_low": 20.0, "visibility": 10.0}

    with patch("forecast_engine.load_detailed_history", return_value=history):
        result = find_similar_flights("ANA1891", weather)

    assert [row["date"] for row in result] == ["2026-01-01", "2026-01-02"]
    assert result[0]["date_label"] == "2026/01/01"
    assert result[0]["flight_display_name"] == "ANA1891(1便)"


def test_find_similar_flights_prefers_visibility_when_scores_are_equal():
    history = [
        {"date": "2026-01-01", "flight_number": "ANA1891", "flight_display_name": "ANA1891(1便)", "status": "通常", "status_reason": None, "wind_direction": 180.0, "wind_speed": 5.0, "wind_gusts": 8.0, "cloud_cover_low": 20.0, "visibility": None},
        {"date": "2026-01-02", "flight_number": "ANA1891", "flight_display_name": "ANA1891(1便)", "status": "通常", "status_reason": None, "wind_direction": 180.0, "wind_speed": 5.0, "wind_gusts": 8.0, "cloud_cover_low": 20.0, "visibility": 10.0},
    ]
    weather = {"wind_direction": 180.0, "wind_speed": 5.0, "wind_gusts": 8.0, "cloud_cover_low": 20.0, "visibility": 10.0}

    with patch("forecast_engine.load_detailed_history", return_value=history):
        result = find_similar_flights("ANA1891", weather, limit=1)

    assert result[0]["date"] == "2026-01-02"


def test_find_similar_flights_prioritizes_matching_adverse_condition():
    base = {"flight_number": "ANA1891", "flight_display_name": "ANA1891(1便)", "status": "通常", "status_reason": None}
    history = [
        {**base, "date": "2026-01-01", "wind_direction": 180.0, "wind_speed": 5.0, "wind_gusts": 8.0, "cloud_cover_low": 20.0, "visibility": 15.0},
        {**base, "date": "2026-01-02", "wind_direction": 210.0, "wind_speed": 6.0, "wind_gusts": 9.0, "cloud_cover_low": 90.0, "visibility": 4.0},
    ]
    weather = {"wind_direction": 180.0, "wind_speed": 5.0, "wind_gusts": 8.0, "cloud_cover_low": 95.0, "visibility": 3.0}

    with patch("forecast_engine.load_detailed_history", return_value=history):
        result = find_similar_flights("ANA1891", weather, limit=1)

    assert result[0]["date"] == "2026-01-02"


def test_find_similar_flights_prioritizes_matching_strong_wind_and_direction():
    base = {"flight_number": "ANA1891", "flight_display_name": "ANA1891(1便)", "status": "通常", "status_reason": None, "cloud_cover_low": 30.0, "visibility": 15.0}
    history = [
        {**base, "date": "2026-01-01", "wind_direction": 180.0, "wind_speed": 5.0, "wind_gusts": 8.0},
        {**base, "date": "2026-01-02", "wind_direction": 245.0, "wind_speed": 13.0, "wind_gusts": 19.0},
    ]
    weather = {"wind_direction": 250.0, "wind_speed": 14.0, "wind_gusts": 20.0, "cloud_cover_low": 30.0, "visibility": 15.0}

    with patch("forecast_engine.load_detailed_history", return_value=history):
        result = find_similar_flights("ANA1891", weather, limit=1)

    assert result[0]["date"] == "2026-01-02"


def test_low_cloud_warning_uses_precise_wording():
    with patch("forecast_engine.load_history", return_value=[("通常", 180.0, 5.0)]):
        result = predict_flight_probability(180.0, 5.0, 8.0, 100.0, 15.0)

    assert result["warning_msg"] == "低層雲の影響注意 (低層雲量 100.0%)"


def test_probability_cap_is_97_percent():
    with patch("forecast_engine.load_history", return_value=[]):
        result = predict_flight_probability(180.0, 3.0, 5.0, 10.0, 20.0)

    assert MAX_PROBABILITY == 97.0
    assert result["probability"] == 97.0


def test_low_cloud_and_gust_adjustments_each_use_09():
    history = [("通常", 210.0, 18.0)] * 3 + [("欠航", 210.0, 18.0)] * 6
    with patch("forecast_engine.load_history", return_value=history):
        result = predict_flight_probability(210.0, 18.09, 24.4, 100.0, 12.2)

    assert result["data_count"] == 9
    assert result["probability"] == 27.0


def test_southerly_wind_warning_includes_boundary_values():
    with patch("forecast_engine.load_history", return_value=[("通常", 180.0, 9.0)]):
        lower = predict_flight_probability(120.0, 9.0, 10.0, 20.0, 15.0)
        upper = predict_flight_probability(240.0, 9.0, 10.0, 20.0, 15.0)

    assert "南風注意" in lower["warning_msg"]
    assert "南風注意" in upper["warning_msg"]
    assert lower["alert_required"] is True


def test_southerly_wind_warning_requires_direction_and_speed():
    with patch("forecast_engine.load_history", return_value=[("通常", 180.0, 9.0)]):
        weak = predict_flight_probability(180.0, 8.9, 10.0, 20.0, 15.0)
        outside = predict_flight_probability(241.0, 9.0, 10.0, 20.0, 15.0)

    assert "南風注意" not in weak["warning_msg"]
    assert "南風注意" not in outside["warning_msg"]


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


def test_model_reference_probabilities_use_each_models_median():
    members = [
        {"_model": "gfs_seamless", "wind_speed": value}
        for value in (10.0, 20.0, 30.0)
    ] + [
        {"_model": "ecmwf_ifs025", "wind_speed": value}
        for value in (40.0, 50.0)
    ]
    with patch(
        "web_app.predict_flight_probability",
        side_effect=lambda **weather: {"probability": weather["wind_speed"]},
    ):
        probabilities = calculate_model_reference_probabilities(members)

    assert probabilities == {"gfs_seamless": 20.0, "ecmwf_ifs025": 45.0}


def test_confidence_note_uses_short_wording():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert "(天候信頼度は{{ day.confidence.member_count }}通りの天気予測から{{ day.confidence.spread }}ptと判定)" in template


def test_mobile_css_prevents_horizontal_overflow():
    stylesheet = (BASE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    assert "overflow-x: clip" in stylesheet
    assert ".header::after { right: 0; width: 55%; }" in stylesheet


def test_stylesheet_url_has_cache_buster():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'href="static/styles.css?v=' in template


def test_orange_flight_style_depends_on_probability_below_sixty():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    stylesheet = (BASE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    assert "{% if flight.probability < 60 %} flight--low-probability{% endif %}" in template
    assert "flight.alert_required" not in template
    assert ".flight--low-probability .probability" in stylesheet
    assert ".flight--alert" not in stylesheet


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
        patch("web_app.fetch_jma_forecast", return_value={}),
        patch("web_app.fetch_ensemble_forecast", return_value={}),
        patch("web_app.predict_flight_probability", return_value=result),
        patch("web_app._flight_display_expired", return_value=False),
        app.test_client() as client,
    ):
        response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "八丈島就航統計予測" in body
    assert "羽田→八丈島便の就航傾向を、過去の就航実績と天気から見やすくするサイトです。" in body
    assert "GFS(アメリカ海洋大気庁)・ECMWF(欧州中期予報センター)" in body
    assert "主予報はOpen-Meteo標準予報を使用しています。" in body
    assert "主予報: Open-Meteo標準予報" in body
    assert "主予報(Open-Meteo)での就航確率" in body
    assert "天候信頼度" in body
    assert "風向 南 180°" in body
    assert "低層雲量 20%" in body
    assert ">雲量<" not in body
    assert "なぜ作ったか" in body
    assert "ざっくりどういう仕組みか" in body
    assert "GFS 31通りとECMWF 31通り、合計62通り" in body
    assert "就航確率60%未満の便はオレンジ" in body
    assert "GitHub Actionsで6時間ごとに再計算" in body
    assert "気象業法への配慮" in body
    assert "予報気象情報" in body
    assert "気象条件が近い過去の就航実績10件" in body
    assert "6ポイント以内" not in body


def test_history_template_includes_flight_name_and_visibility_fallback():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert "{{ history.date_label }} {{ history.flight_display_name }}" in template
    assert "/ 視程 {% if history.visibility is not none %}{{ history.visibility }} km{% else %}欠測{% endif %}" in template
    assert "GFS予報での参考就航確率" in template
    assert "ECMWF予報での参考就航確率" in template
    assert "JMA予報での参考就航確率" in template


def test_index_handles_weather_api_error():
    with patch("web_app.fetch_forecast", side_effect=ValueError("bad data")):
        response = app.test_client().get("/")

    assert response.status_code == 200
    assert "現在、予報を取得できません" in response.get_data(as_text=True)


def test_health():
    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}

