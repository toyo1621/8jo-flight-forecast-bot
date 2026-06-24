from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock, patch

from flask import render_template

from app_config import LOW_PROBABILITY_THRESHOLD
from forecast_engine import MAX_PROBABILITY, find_similar_flights, predict_flight_probability
from presentation import decorate_flight_for_display
from web_app import (
    BASE_DIR,
    FORECAST_DAYS,
    app,
    build_daily_forecasts,
    calculate_confidence,
    calculate_model_reference_probabilities,
    calculate_model_reference_risks,
    deterministic_risk_summary,
    fallback_confidence,
    _select_evenly,
    _prepare_reference_weather,
    _with_typhoon_proximity_risk,
    _with_model_difference_warning,
    fetch_jma_forecast,
    fetch_haneda_forecast,
    load_forecast_bundle,
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
        "pressure_msl": 1012.0,
        "surface_pressure": 1002.0,
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
            "precipitation": [0.0],
        }
    }
    with patch("web_app.requests.get", return_value=response) as get:
        result = fetch_jma_forecast()

    response.raise_for_status.assert_called_once()
    assert get.call_args.kwargs["params"]["models"] == "jma_seamless"
    assert result["2026-06-20T08:00"]["visibility"] == 15.0
    assert result["2026-06-20T08:00"]["precipitation"] == 0.0
    assert result["2026-06-20T08:00"]["pressure_msl"] is None


def test_haneda_forecast_requests_haneda_coordinates():
    response = Mock()
    response.json.return_value = {
        "hourly": {
            "time": ["2026-06-20T08:00"],
            "wind_speed_10m": [5.0],
            "wind_direction_10m": [180.0],
            "wind_gusts_10m": [8.0],
            "cloud_cover_low": [20.0],
            "visibility": [15000.0],
            "precipitation": [0.0],
            "pressure_msl": [1004.8],
            "surface_pressure": [1001.0],
        }
    }
    with patch("web_app.requests.get", return_value=response) as get:
        result = fetch_haneda_forecast()

    assert get.call_args.kwargs["params"]["latitude"] == 35.5494
    assert get.call_args.kwargs["params"]["longitude"] == 139.7798
    assert result["2026-06-20T08:00"]["pressure_msl"] == 1004.8


def test_jma_reference_uses_main_forecast_for_missing_required_values():
    candidate = {
        "wind_direction": None,
        "wind_speed": 5.0,
        "wind_gusts": None,
        "cloud_cover_low": 20.0,
        "visibility": None,
        "precipitation": None,
    }

    result = _prepare_reference_weather(candidate, SAMPLE_WEATHER["2026-06-20T08:00"])

    assert result["wind_direction"] == 180.0
    assert result["wind_speed"] == 5.0
    assert result["wind_gusts"] == 7.0
    assert result["visibility"] == 15.0
    assert result["precipitation"] is None


def test_daily_forecast_skips_main_weather_without_required_values():
    weather = {
        "2026-06-20T08:00": {
            **SAMPLE_WEATHER["2026-06-20T08:00"],
            "wind_direction": None,
        }
    }

    days = build_daily_forecasts(
        weather,
        reference_date=date(2026, 6, 19),
        current_time=datetime(2026, 6, 19, 12, 0, tzinfo=JST),
    )

    assert days == []


def test_model_difference_warning_uses_twenty_point_boundary():
    result = {"probability": 80.0, "warning_msg": "特になし", "alert_required": False}

    warned = _with_model_difference_warning(result, 60.0)
    quiet = _with_model_difference_warning(result, 60.1)

    assert warned["warning_msg"] == "気象モデル差に注意"
    assert warned["alert_required"] is True
    assert quiet["warning_msg"] == "特になし"


def test_typhoon_risk_uses_ensemble_downside_and_pressure():
    result = {"probability": 97.0, "warning_msg": "特になし", "alert_required": False}
    confidence = {
        "source": "ensemble",
        "spread": 58.4,
        "low_probability": 38.6,
    }

    ensemble_warned = _with_typhoon_proximity_risk(result, confidence)
    pressure_warned = _with_typhoon_proximity_risk(
        result,
        {"source": "ensemble", "spread": 20.0, "low_probability": 80.0},
        haneda_weather={"pressure_msl": 1004.9},
    )

    assert ensemble_warned["probability"] == 58.2
    assert ensemble_warned["warning_msg"] == "台風接近リスク"
    assert ensemble_warned["alert_required"] is True
    assert pressure_warned["probability"] == 58.2


def test_typhoon_risk_applies_to_all_flights_on_same_day():
    base_weather = {
        "wind_direction": 240.0,
        "wind_speed": 4.0,
        "wind_gusts": 8.0,
        "cloud_cover_low": 20.0,
        "visibility": 15.0,
        "precipitation": 0.0,
        "pressure_msl": 1010.0,
        "surface_pressure": 1000.0,
    }
    weather = {
        "2026-06-28T08:00": {**base_weather, "pressure_msl": 1004.8},
        "2026-06-28T13:00": base_weather,
        "2026-06-28T17:00": base_weather,
    }
    result = {"probability": 97.0, "warning_msg": "特になし", "alert_required": False}

    with (
        patch("web_app.predict_flight_probability", return_value=result),
        patch("web_app.find_similar_flights", return_value=[]),
    ):
        days = build_daily_forecasts(
            weather,
            reference_date=date(2026, 6, 24),
            current_time=datetime(2026, 6, 24, 10, 0, tzinfo=JST),
        )

    assert [flight["probability"] for flight in days[0]["flights"]] == [58.2, 58.2, 58.2]
    assert all("台風接近リスク" in flight["warning_msg"] for flight in days[0]["flights"])


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
        result = predict_flight_probability(210.0, 18.09, 18.5, 85.0, 12.2)

    assert result["data_count"] == 9
    assert result["probability"] == 27.0


def test_severe_visibility_low_cloud_and_gust_adjustments_are_stronger():
    history = [("通常", 210.0, 5.0)] * 10
    with patch("forecast_engine.load_history", return_value=history):
        low_visibility = predict_flight_probability(210.0, 5.0, 8.0, 20.0, 2.0)
        severe_low_cloud = predict_flight_probability(210.0, 5.0, 8.0, 96.0, 15.0)
        severe_gust = predict_flight_probability(210.0, 5.0, 20.3, 20.0, 15.0)

    assert low_visibility["probability"] == 45.0
    assert severe_low_cloud["probability"] == 75.0
    assert severe_gust["probability"] == 55.0


def test_precipitation_from_two_mm_adds_rain_risk():
    history = [("通常", 180.0, 5.0)] * 10
    with patch("forecast_engine.load_history", return_value=history):
        dry = predict_flight_probability(180.0, 5.0, 8.0, 20.0, 15.0, precipitation=1.9)
        rainy = predict_flight_probability(180.0, 5.0, 8.0, 20.0, 15.0, precipitation=2.0)

    assert dry["probability"] == 97.0
    assert rainy["probability"] == 85.0
    assert "降水注意" in rainy["warning_msg"]


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


def test_model_reference_risks_summarize_each_model_members():
    members = [
        {"_model": "gfs_seamless", "wind_speed": 9.0},
        {"_model": "gfs_seamless", "wind_speed": 12.0},
        {"_model": "ecmwf_ifs025", "wind_speed": 4.0, "cloud_cover_low": 95.0},
    ]

    with patch(
        "web_app.predict_flight_probability",
        side_effect=[
            {"warning_msg": "特になし"},
            {"warning_msg": "強風注意 (予報風速: 12.0 m/s)"},
            {"warning_msg": "低層雲の影響注意 (低層雲量 95.0%)"},
        ],
    ):
        risks = calculate_model_reference_risks(members, SAMPLE_WEATHER["2026-06-20T08:00"])

    assert risks == {
        "gfs_seamless": "強風注意 (1/2通り)",
        "ecmwf_ifs025": "低層雲の影響注意 (1/1通り)",
    }


def test_deterministic_risk_summary_uses_simple_labels():
    assert deterministic_risk_summary({"warning_msg": "特になし"}) == "特になし"
    assert deterministic_risk_summary(
        {"warning_msg": "南風注意、突風注意 (予報突風: 16.0 m/s)"}
    ) == "南風注意、突風注意"


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


def test_template_includes_quick_guide_for_non_experts():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    stylesheet = (BASE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'class="quick-guide"' in template
    assert "◎95%以上 / 〇75%以上 / △35%以上 / ×35%未満" in template
    assert "GFS・ECMWF・JMAは別モデルで計算した参考値" in template
    assert ".quick-guide" in stylesheet


def test_orange_flight_style_depends_on_probability_below_sixty():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    stylesheet = (BASE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    assert "{% if flight.is_low_probability %} flight--low-probability{% endif %}" in template
    assert LOW_PROBABILITY_THRESHOLD == 60.0
    assert "flight.alert_required" not in template
    assert ".flight--low-probability .probability" in stylesheet
    assert ".flight--alert" not in stylesheet


def test_flight_card_shows_model_reference_probabilities_with_threshold_styles():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    stylesheet = (BASE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'class="model-probabilities"' in template
    assert "{% for model in flight.model_probabilities %}" in template
    assert 'src="{{ model.flag_path }}"' in template
    assert "model-probability--{{ model.tone }}" in template
    assert "モデル別リスク" in template
    assert "model-risk--{{ model.risk_tone }}" in template
    assert "(Open-Meteo主予報)" in template
    assert "詳しく見る(運航実績・気象情報)" in template
    assert ".model-probability--ok" in stylesheet
    assert ".model-probability--low" in stylesheet
    assert ".flight-meta" in stylesheet
    assert ".model-flag" in stylesheet
    assert "flight.probability_symbol" in template
    assert "model.symbol" in template
    assert ".probability-symbol" in stylesheet
    assert ".probability-inline-symbol" in stylesheet
    assert ".probability small" in stylesheet


def test_probability_symbol_thresholds_render_in_template():
    flight = decorate_flight_for_display({
        "date": "2026-06-20",
        "number": "ANA1891(1便)",
        "raw_number": "ANA1891",
        "time": "08:30",
        "probability": 96.0,
        "gfs_probability": 76.0,
        "ecmwf_probability": 35.0,
        "jma_probability": 34.9,
        "warning_msg": "なし",
        "wind_direction": 180.0,
        "wind_direction_label": "南",
        "wind_speed": 4.0,
        "wind_gusts": 7.0,
        "cloud_cover_low": 20.0,
        "visibility": 15.0,
        "similar_history": [],
    })
    day = {
        "date": "2026-06-20",
        "date_label": "6/20",
        "weekday": "土",
        "flights": [flight],
        "confidence": {"grade": "A", "label": "10ポイント以内", "source": "lead_time", "lead_days": 1},
    }
    with app.test_request_context("/"):
        body = render_template("index.html", days=[day], error=None, updated_at="2026/06/20 00:00")

    assert "◎</span><strong>96.0" in body
    assert "〇</span>76.0%" in body
    assert "△</span>35.0%" in body
    assert "×</span>34.9%" in body


def test_flag_icon_assets_exist():
    assert (BASE_DIR / "static" / "flags" / "us.svg").exists()
    assert (BASE_DIR / "static" / "flags" / "eu.svg").exists()
    assert (BASE_DIR / "static" / "flags" / "jp.svg").exists()


def test_decorate_flight_for_display_builds_model_rows():
    flight = decorate_flight_for_display(
        {
            "probability": 88.0,
            "gfs_probability": 75.0,
            "ecmwf_probability": 59.9,
            "ecmwf_risk": "強風注意 (2/31通り)",
            "jma_probability": None,
        }
    )

    assert flight["probability_symbol"] == "〇"
    assert flight["is_low_probability"] is False
    assert flight["model_probabilities"] == [
        {
            "label": "GFS",
            "probability": 75.0,
            "symbol": "〇",
            "tone": "ok",
            "risk": "特になし",
            "risk_tone": "ok",
            "flag_path": "static/flags/us.svg",
            "flag_alt": "US",
        },
        {
            "label": "ECMWF",
            "probability": 59.9,
            "symbol": "△",
            "tone": "low",
            "risk": "強風注意 (2/31通り)",
            "risk_tone": "alert",
            "flag_path": "static/flags/eu.svg",
            "flag_alt": "EU",
        },
    ]


def test_load_forecast_bundle_uses_cached_main_forecast_on_api_error():
    cached = {
        "weather": SAMPLE_WEATHER,
        "jma": {"2026-06-20T08:00": SAMPLE_WEATHER["2026-06-20T08:00"]},
        "ensembles": {"2026-06-20T08:00": []},
    }

    with (
        patch("web_app.fetch_forecast", side_effect=ValueError("bad data")),
        patch("web_app.load_cached_forecast_bundle", return_value=cached),
        patch("web_app.save_forecast_bundle") as save,
    ):
        bundle = load_forecast_bundle()

    assert bundle["source"] == "cache"
    assert bundle["weather"] == SAMPLE_WEATHER
    assert "前回取得した予報データ" in bundle["notices"][0]
    save.assert_not_called()


def test_load_forecast_bundle_reuses_cached_optional_sources():
    cached = {
        "weather": SAMPLE_WEATHER,
        "jma": {"cached-jma": {"wind_direction": 180.0, "wind_speed": 5.0}},
        "ensembles": {"cached-ensemble": []},
        "haneda": {"cached-haneda": {"pressure_msl": 1008.0}},
    }

    with (
        patch("web_app.fetch_forecast", return_value=SAMPLE_WEATHER),
        patch("web_app.fetch_haneda_forecast", side_effect=ValueError("bad haneda")),
        patch("web_app.fetch_jma_forecast", side_effect=ValueError("bad jma")),
        patch("web_app.fetch_ensemble_forecast", side_effect=ValueError("bad ensemble")),
        patch("web_app.load_cached_forecast_bundle", return_value=cached),
        patch("web_app.save_forecast_bundle") as save,
    ):
        bundle = load_forecast_bundle()

    assert bundle["source"] == "live"
    assert bundle["haneda"] == cached["haneda"]
    assert bundle["jma"] == cached["jma"]
    assert bundle["ensembles"] == cached["ensembles"]
    assert "羽田側の予報は前回取得データ" in bundle["notices"][0]
    assert "JMA予報は前回取得データ" in bundle["notices"][1]
    assert "アンサンブル予報は前回取得データ" in bundle["notices"][2]
    save.assert_called_once_with(SAMPLE_WEATHER, cached["jma"], cached["ensembles"], cached["haneda"])


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
        patch("web_app.fetch_haneda_forecast", return_value={}),
        patch("web_app.fetch_jma_forecast", return_value={}),
        patch("web_app.fetch_ensemble_forecast", return_value={}),
        patch("web_app.predict_flight_probability", return_value=result),
        patch("web_app._flight_display_expired", return_value=False),
        app.test_client() as client,
    ):
        response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "八丈島運航統計予測" in body
    assert "羽田→八丈島便の運航傾向を、過去の運航実績と天気から見やすくするサイトです。" in body
    assert "天候信頼度は、Open-Meteo APIからオープンデータ" not in body
    assert "GFS・ECMWF・JMAは別モデルで計算した参考値" in body
    assert "主予報はOpen-Meteo標準予報を使用しています。" in body
    assert "更新 " in body
    assert "(6時間ごとに更新)" in body
    assert "青: 運航確率60%以上" in body
    assert "オレンジ: 運航確率60%未満" in body
    assert "主予報: Open-Meteo標準予報" in body
    assert "主予報(Open-Meteo)での運航確率" in body
    assert "天候信頼度" in body
    assert "モデル別の参考運航確率" in body
    assert ">雲量<" not in body
    assert "なぜ作ったか" in body
    assert "ざっくりどういう仕組みか" in body
    assert "GFS 31通りとECMWF 31通り、合計62通り" in body
    assert "短期はJMA参考値と主予報との差を特に確認" in body
    assert "運航確率60%未満の便はオレンジ" in body
    assert "GitHub Actionsで6時間ごとに再計算" in body
    assert "気象業法への配慮" in body
    assert "予報気象情報" in body
    assert "モデル別リスク" in body
    assert "気象条件が近い過去の運航実績10件" in body
    assert "6ポイント以内" not in body


def test_history_template_includes_flight_name_and_visibility_fallback():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert "{{ history.date_label }} {{ history.flight_display_name }}" in template
    assert "/ 視程 {% if history.visibility is not none %}{{ history.visibility }} km{% else %}欠測{% endif %}" in template
    assert "{{ model.label }}予報での参考運航確率" in template


def test_index_handles_weather_api_error():
    with (
        patch("web_app.fetch_forecast", side_effect=ValueError("bad data")),
        patch("web_app.load_cached_forecast_bundle", return_value=None),
    ):
        response = app.test_client().get("/")

    assert response.status_code == 200
    assert "現在、予報を取得できません" in response.get_data(as_text=True)


def test_health():
    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_workflows_run_tests_and_data_quality_reports():
    ci = (BASE_DIR / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    pages = (BASE_DIR / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    collection = (BASE_DIR / ".github" / "workflows" / "data_collection.yml").read_text(encoding="utf-8")

    assert "python -m pytest -q" in ci
    assert "python data_quality.py --backend sqlite" in ci
    assert "python data_quality.py --backend bigquery" in pages
    assert "python data_quality.py --backend bigquery" in collection
    assert "actions/upload-artifact@" in pages
    assert "actions/upload-artifact@" in collection


