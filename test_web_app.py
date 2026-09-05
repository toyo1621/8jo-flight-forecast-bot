import re
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock, patch

from flask import render_template

from app_config import LOW_PROBABILITY_THRESHOLD
from forecast_cache import is_cached_forecast_fresh, save_forecast_bundle
from forecast_engine import (
    MAX_PROBABILITY,
    find_similar_flights,
    predict_flight_probability,
)
from presentation import decorate_flight_for_display
from web_app import (
    BASE_DIR,
    FORECAST_DAYS,
    _select_evenly,
    _supplement_primary_forecast,
    _with_typhoon_impact,
    _with_typhoon_risk_summary,
    app,
    build_daily_forecasts,
    calculate_confidence,
    calculate_model_reference_probabilities,
    calculate_model_reference_risks,
    deterministic_risk_summary,
    fallback_confidence,
    fetch_forecast,
    fetch_typhoon_impacts,
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


def typhoon_impact(level):
    return {
        "risk_level": level,
        "score": 72,
        "factors": {"wind": 20, "rain": 10},
        "inputs": {"windSpeedMps": 8, "rainfallMm24h": 12},
    }


def test_build_daily_forecasts():
    result = {
        "probability": 88.0,
        "alert_required": False,
        "warning_msg": "なし",
        "data_count": 10,
        "step_used": 1,
    }
    with (
        patch("web_app.predict_flight_probability", return_value=result),
        patch("web_app.find_similar_flights", return_value=[]),
    ):
        days = build_daily_forecasts(
            SAMPLE_WEATHER,
            reference_date=date(2026, 6, 19),
            current_time=datetime(2026, 6, 19, 12, 0, tzinfo=JST),
        )

    assert days[0]["date_label"] == "6/20"
    assert days[0]["flights"][0]["number"] == "ANA1891(1便)"
    assert days[0]["flights"][0]["probability"] == 88.0
    assert days[0]["flights"][0]["jma_probability"] == 88.0
    assert [model["label"] for model in days[0]["flights"][0]["model_probabilities"]] == ["JMA"]
    assert days[0]["flights"][0]["wind_direction_label"] == "南"
    assert days[0]["confidence"]["grade"] is None
    assert days[0]["confidence"]["source"] == "lead_time_caution"


def test_build_daily_forecasts_evaluates_each_ensemble_member_once():
    members = [
        {"_model": "gfs_seamless", "_member_id": "gfs_seamless:01", "wind_speed": 5.0},
        {"_model": "gfs_seamless", "_member_id": "gfs_seamless:02", "wind_speed": 6.0},
    ]
    calls = []

    def predictor(**weather):
        calls.append(weather)
        return {
            "probability": weather.get("wind_speed", 0.0),
            "alert_required": False,
            "warning_msg": "特になし",
            "data_count": 10,
            "step_used": 1,
        }

    with (
        patch("web_app.predict_flight_probability", side_effect=predictor),
        patch("web_app.find_similar_flights", return_value=[]),
    ):
        build_daily_forecasts(
            SAMPLE_WEATHER,
            ensembles_by_time={"2026-06-20T08:00": members},
            reference_date=date(2026, 6, 19),
            current_time=datetime(2026, 6, 19, 12, 0, tzinfo=JST),
        )

    assert len(calls) == 1 + len(members)
    assert [call["wind_speed"] for call in calls] == [4.0, 5.0, 6.0]


def test_forecast_period_reaches_ten_days_ahead():
    assert FORECAST_DAYS == 11


def test_main_forecast_uses_jma_and_supplements_unavailable_fields():
    jma_response = Mock()
    jma_response.json.return_value = {
        "hourly": {
            "time": ["2026-06-20T08:00"],
            "wind_speed_10m": [5.0],
            "wind_direction_10m": [180.0],
            "wind_gusts_10m": [None],
            "cloud_cover_low": [20.0],
            "visibility": [None],
            "precipitation": [0.0],
        }
    }
    supplemental_response = Mock()
    supplemental_response.json.return_value = {
        "hourly": {
            "time": ["2026-06-20T08:00"],
            "wind_speed_10m": [7.0],
            "wind_direction_10m": [90.0],
            "wind_gusts_10m": [8.0],
            "cloud_cover_low": [80.0],
            "visibility": [15000.0],
            "precipitation": [3.0],
        }
    }
    with patch(
        "web_app.requests.get",
        side_effect=[jma_response, supplemental_response],
    ) as get:
        result = fetch_forecast()

    jma_response.raise_for_status.assert_called_once()
    supplemental_response.raise_for_status.assert_called_once()
    assert get.call_args_list[0].kwargs["params"]["models"] == "jma_seamless"
    assert "models" not in get.call_args_list[1].kwargs["params"]
    assert result["2026-06-20T08:00"]["wind_speed"] == 5.0
    assert result["2026-06-20T08:00"]["wind_direction"] == 180.0
    assert result["2026-06-20T08:00"]["wind_gusts"] == 8.0
    assert result["2026-06-20T08:00"]["visibility"] == 15.0
    assert result["2026-06-20T08:00"]["precipitation"] == 0.0
    assert result["2026-06-20T08:00"]["pressure_msl"] is None
    assert result["2026-06-20T08:00"]["_primary_supplement_status"] == "complete"
    assert result["2026-06-20T08:00"]["_weather_field_sources"] == {
        "wind_direction": "jma",
        "wind_speed": "jma",
        "wind_gusts": "open_meteo_supplement",
        "cloud_cover_low": "jma",
        "visibility": "open_meteo_supplement",
        "precipitation": "jma",
    }


def test_main_forecast_keeps_jma_when_supplement_is_unavailable():
    primary = {
        "2026-06-20T08:00": {
            **SAMPLE_WEATHER["2026-06-20T08:00"],
            "wind_gusts": None,
            "visibility": None,
        }
    }

    with patch(
        "web_app._fetch_deterministic_forecast",
        side_effect=[primary, ValueError("bad supplement")],
    ):
        result = fetch_forecast()

    weather = result["2026-06-20T08:00"]
    assert weather["wind_speed"] == 4.0
    assert weather["wind_gusts"] is None
    assert weather["visibility"] is None
    assert weather["_primary_supplement_status"] == "unavailable"
    assert weather["_weather_field_sources"]["wind_gusts"] == "missing"
    assert weather["_weather_field_sources"]["visibility"] == "missing"


def test_typhoon_impacts_use_jma_flight_risk_levels():
    response = Mock()
    response.json.return_value = {
        "source": "jma",
        "days": [
            {
                "date": "2026-07-13",
                "summaryRiskLevel": "severe",
                "targets": {"flight": {"riskLevel": "low"}},
            },
            {
                "date": "2026-07-14",
                "summaryRiskLevel": "high",
                "targets": {
                    "flight": {
                        "riskLevel": "medium",
                        "score": 42,
                        "factors": {"wind": 20},
                        "inputs": {"windSpeedMps": 8},
                    }
                },
            },
        ],
    }

    with patch("web_app.requests.get", return_value=response) as get:
        impacts = fetch_typhoon_impacts()

    response.raise_for_status.assert_called_once()
    assert get.call_args.kwargs["params"] == {"source": "jma"}
    assert impacts["2026-07-13"]["risk_level"] == "low"
    assert impacts["2026-07-13"]["factor_breakdown_available"] is False
    assert impacts["2026-07-14"]["risk_level"] == "medium"
    assert impacts["2026-07-14"]["factors"] == {"wind": 20}
    assert impacts["2026-07-14"]["inputs"] == {"windSpeedMps": 8}


def test_typhoon_impacts_do_not_fall_back_to_summary_risk():
    response = Mock()
    response.json.return_value = {
        "source": "jma",
        "days": [
            {
                "date": "2026-07-13",
                "summaryRiskLevel": "severe",
                "targets": {"flight": {}},
            },
            {
                "date": "2026-07-14",
                "summaryRiskLevel": "low",
                "targets": {"flight": {"riskLevel": "medium"}},
            },
        ],
    }

    with patch("web_app.requests.get", return_value=response):
        impacts = fetch_typhoon_impacts()

    assert list(impacts) == ["2026-07-14"]
    assert impacts["2026-07-14"]["risk_level"] == "medium"


def test_primary_supplement_reports_fields_that_remain_missing():
    primary = {
        "2026-06-20T08:00": {
            "wind_direction": 180.0,
            "wind_speed": 5.0,
            "wind_gusts": None,
            "cloud_cover_low": 20.0,
            "visibility": None,
            "precipitation": 0.0,
        }
    }
    supplement = {
        "2026-06-20T08:00": {
            "wind_direction": 90.0,
            "wind_speed": 9.0,
            "wind_gusts": 8.0,
            "cloud_cover_low": 80.0,
            "visibility": None,
            "precipitation": 4.0,
        }
    }

    result = _supplement_primary_forecast(primary, supplement)["2026-06-20T08:00"]

    assert result == {
        "wind_direction": 180.0,
        "wind_speed": 5.0,
        "wind_gusts": 8.0,
        "cloud_cover_low": 20.0,
        "visibility": None,
        "precipitation": 0.0,
        "_primary_supplement_status": "partial",
        "_weather_field_sources": {
            "wind_direction": "jma",
            "wind_speed": "jma",
            "wind_gusts": "open_meteo_supplement",
            "cloud_cover_low": "jma",
            "visibility": "missing",
            "precipitation": "jma",
        },
    }


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


def test_typhoon_risk_uses_external_impact_multipliers():
    result = {"probability": 97.0, "warning_msg": "特になし", "alert_required": False}

    quiet = _with_typhoon_impact(result, typhoon_impact("low"))
    small = _with_typhoon_impact(result, typhoon_impact("medium"))
    medium = _with_typhoon_impact(result, typhoon_impact("high"))
    large = _with_typhoon_impact(result, typhoon_impact("severe"))

    assert quiet["probability"] == result["probability"]
    assert quiet["typhoon_factor"] == 1.0
    assert quiet["typhoon_adjustment_status"] == "not_applicable"
    assert small["probability"] == 87.3
    assert small["warning_msg"] == "台風接近リスク小"
    assert medium["probability"] == 77.6
    assert medium["warning_msg"] == "台風接近リスク中"
    assert large["probability"] == 67.9
    assert large["warning_msg"] == "台風接近リスク大"
    assert all(item["alert_required"] is True for item in (small, medium, large))
    assert _with_typhoon_risk_summary("台風接近リスク", typhoon_impact("severe")) == "台風接近リスク大"


def test_typhoon_factor_is_kept_separate_from_weather_factor():
    result = {
        "probability": 72.0,
        "base_probability": 80.0,
        "weather_factor": 0.9,
        "weather_factors": {"low_cloud": 0.9},
        "warning_msg": "低層雲の影響注意",
        "alert_required": True,
    }

    adjusted = _with_typhoon_impact(result, typhoon_impact("severe"))

    assert adjusted["probability"] == 50.4
    assert adjusted["typhoon_factor"] == 0.7
    assert adjusted["factor_ablation"] == {
        "base": 80.0,
        "weather_only": 72.0,
        "typhoon_only": 56.0,
        "combined": 50.4,
    }


def test_typhoon_factor_breakdown_missing_keeps_warning_without_numeric_adjustment():
    result = {"probability": 80.0, "warning_msg": "特になし", "alert_required": False}

    adjusted = _with_typhoon_impact(result, "severe")

    assert adjusted["probability"] == 80.0
    assert adjusted["typhoon_factor"] is None
    assert adjusted["typhoon_adjustment_status"] == "warning_only"
    assert "数値補正なし" in adjusted["warning_msg"]


def test_typhoon_numeric_adjustment_can_be_disabled_for_small_samples():
    result = {"probability": 80.0, "warning_msg": "特になし", "alert_required": False}

    with patch("web_app.TYPHOON_NUMERIC_ADJUSTMENT_ENABLED", False):
        adjusted = _with_typhoon_impact(result, typhoon_impact("severe"))

    assert adjusted["probability"] == 80.0
    assert adjusted["typhoon_adjustment_status"] == "warning_only"


def test_typhoon_risk_does_not_turn_unavailable_into_a_number():
    result = {
        "probability": None,
        "calculation_status": "insufficient_history",
        "warning_msg": "過去実績が4件のため、統計参考値を算出できません。",
        "alert_required": False,
    }

    adjusted = _with_typhoon_impact(result, typhoon_impact("severe"))

    assert adjusted == result


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
            typhoon_impacts_by_date={"2026-06-28": typhoon_impact("severe")},
        )

    assert [flight["probability"] for flight in days[0]["flights"]] == [67.9, 67.9, 67.9]
    assert all("台風接近リスク大" in flight["warning_msg"] for flight in days[0]["flights"])


def test_low_typhoon_impact_does_not_adjust_next_week():
    base_weather = {
        **SAMPLE_WEATHER["2026-06-20T08:00"],
        "precipitation": 0.0,
    }
    weather = {
        f"2026-06-28T{hour:02d}:00": base_weather
        for hour in (8, 13, 17)
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
            typhoon_impacts_by_date={"2026-06-28": "low"},
        )

    assert [flight["probability"] for flight in days[0]["flights"]] == [97.0, 97.0, 97.0]
    assert all("台風接近リスク" not in flight["warning_msg"] for flight in days[0]["flights"])


def test_missing_typhoon_impact_is_not_assumed_low():
    result = {"probability": 97.0, "warning_msg": "特になし", "alert_required": False}
    weather = {"2026-06-28T08:00": SAMPLE_WEATHER["2026-06-20T08:00"]}

    with (
        patch("web_app.predict_flight_probability", return_value=result),
        patch("web_app.find_similar_flights", return_value=[]),
    ):
        days = build_daily_forecasts(
            weather,
            reference_date=date(2026, 6, 24),
            current_time=datetime(2026, 6, 24, 10, 0, tzinfo=JST),
            typhoon_impacts_by_date={},
        )

    assert days[0]["flights"][0]["probability"] == 97.0
    assert "台風接近リスク" not in days[0]["flights"][0]["warning_msg"]


def test_today_flight_disappears_after_arrival_plus_30_minutes():
    weather = {
        f"2026-06-20T{hour:02d}:00": SAMPLE_WEATHER["2026-06-20T08:00"]
        for hour in (8, 13, 17)
    }
    current_time = datetime(2026, 6, 20, 9, 1, tzinfo=JST)

    with (
        patch("web_app.predict_flight_probability", return_value={"probability": 88.0}),
        patch("web_app.find_similar_flights", return_value=[]),
    ):
        days = build_daily_forecasts(weather, current_time=current_time)

    assert [flight["raw_number"] for flight in days[0]["flights"]] == ["ANA1893", "ANA1895"]


def test_today_flight_remains_at_exactly_arrival_plus_30_minutes():
    current_time = datetime(2026, 6, 20, 9, 0, tzinfo=JST)

    with (
        patch("web_app.predict_flight_probability", return_value={"probability": 88.0}),
        patch("web_app.find_similar_flights", return_value=[]),
    ):
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
    with patch("forecast_engine.load_history", return_value=[("通常", 180.0, 5.0)] * 5):
        result = predict_flight_probability(180.0, 5.0, 8.0, 100.0, 15.0)

    assert result["warning_msg"] == "低層雲の影響注意 (低層雲量 100.0%)"


def test_probability_without_history_is_unavailable():
    with patch("forecast_engine.load_history", return_value=[]):
        result = predict_flight_probability(180.0, 3.0, 5.0, 10.0, 20.0)

    assert MAX_PROBABILITY == 97.0
    assert result["probability"] is None
    assert result["calculation_status"] == "insufficient_history"
    assert result["reason_code"] == "no_history"
    assert result["data_count"] == 0
    assert "算出できません" in result["warning_msg"]


def test_probability_with_fewer_than_minimum_history_rows_is_unavailable():
    with patch("forecast_engine.load_history", return_value=[("通常", 180.0, 5.0)] * 4):
        result = predict_flight_probability(180.0, 3.0, 5.0, 10.0, 20.0)

    assert result["probability"] is None
    assert result["calculation_status"] == "insufficient_history"
    assert result["reason_code"] == "below_minimum_history"
    assert result["data_count"] == 4


def test_probability_history_is_filtered_by_flight_number():
    history = [
        *( [("ANA1891", "運航", 180.0, 5.0)] * 3 ),
        *( [("ANA1891", "欠航", 180.0, 5.0)] * 2 ),
        *( [("ANA1893", "欠航", 180.0, 5.0)] * 5 ),
    ]
    with patch("forecast_engine.load_history", return_value=history):
        first = predict_flight_probability(180.0, 5.0, 8.0, 20.0, 15.0, flight_number="ANA1891")
        second = predict_flight_probability(180.0, 5.0, 8.0, 20.0, 15.0, flight_number="ANA1893")

    assert first["probability"] == 60.0
    assert second["probability"] == 0.0
    assert first["history_flight_number"] == "ANA1891"


def test_low_cloud_and_gust_adjustments_each_use_09():
    history = [("通常", 210.0, 18.0)] * 3 + [("欠航", 210.0, 18.0)] * 6
    with patch("forecast_engine.load_history", return_value=history):
        result = predict_flight_probability(210.0, 18.09, 18.5, 85.0, 12.2)

    assert result["data_count"] == 9
    assert result["probability"] == 27.0
    assert result["weather_factors"] == {"low_cloud": 0.9, "gust": 0.9}
    assert result["weather_factor"] == 0.81


def test_visibility_low_cloud_and_gust_adjustments_are_tiered():
    history = [("通常", 210.0, 5.0)] * 10
    with patch("forecast_engine.load_history", return_value=history):
        extreme_visibility = predict_flight_probability(210.0, 5.0, 8.0, 20.0, 0.9)
        severe_visibility = predict_flight_probability(210.0, 5.0, 8.0, 20.0, 1.0)
        moderate_visibility = predict_flight_probability(210.0, 5.0, 8.0, 20.0, 3.0)
        clear_visibility = predict_flight_probability(210.0, 5.0, 8.0, 20.0, 5.0)
        severe_low_cloud = predict_flight_probability(210.0, 5.0, 8.0, 96.0, 15.0)
        severe_gust = predict_flight_probability(210.0, 5.0, 20.3, 20.0, 15.0)

    assert extreme_visibility["probability"] == 50.0
    assert severe_visibility["probability"] == 70.0
    assert moderate_visibility["probability"] == 80.0
    assert clear_visibility["probability"] == 97.0
    assert severe_low_cloud["probability"] == 75.0
    assert severe_gust["probability"] == 55.0


def test_adjusted_visibility_factor_keeps_heavy_rain_and_gust_penalties():
    history = [("通常", 98.0, 6.77)] * 10
    with patch("forecast_engine.load_history", return_value=history):
        result = predict_flight_probability(
            98.0,
            6.77,
            16.5,
            20.0,
            2.1,
            precipitation=9.1,
        )

    assert result["weather_factors"] == {
        "visibility": 0.7,
        "precipitation": 0.7,
        "gust": 0.9,
    }
    assert result["weather_factor"] == 0.441
    assert result["probability"] == 44.1


def test_precipitation_from_two_mm_adds_rain_risk():
    history = [("通常", 180.0, 5.0)] * 10
    with patch("forecast_engine.load_history", return_value=history):
        dry = predict_flight_probability(180.0, 5.0, 8.0, 20.0, 15.0, precipitation=1.9)
        rainy = predict_flight_probability(180.0, 5.0, 8.0, 20.0, 15.0, precipitation=2.0)

    assert dry["probability"] == 97.0
    assert rainy["probability"] == 85.0
    assert "降水注意" in rainy["warning_msg"]


def test_southerly_wind_warning_includes_boundary_values():
    with patch("forecast_engine.load_history", return_value=[("通常", 180.0, 9.0)] * 5):
        lower = predict_flight_probability(120.0, 9.0, 10.0, 20.0, 15.0)
        upper = predict_flight_probability(240.0, 9.0, 10.0, 20.0, 15.0)

    assert "南風注意" in lower["warning_msg"]
    assert "南風注意" in upper["warning_msg"]
    assert lower["alert_required"] is True


def test_southerly_wind_warning_requires_direction_and_speed():
    with patch("forecast_engine.load_history", return_value=[("通常", 180.0, 9.0)] * 5):
        weak = predict_flight_probability(180.0, 8.9, 10.0, 20.0, 15.0)
        outside = predict_flight_probability(241.0, 9.0, 10.0, 20.0, 15.0)

    assert "南風注意" not in weak["warning_msg"]
    assert "南風注意" not in outside["warning_msg"]


def test_calculate_confidence_uses_ensemble_spread():
    members = [
        {
            "_model": "gfs_seamless",
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
    assert confidence["source"] == "ensemble_partial"
    assert confidence["models"]["gfs_seamless"]["valid_member_count"] == 40


def test_calculate_confidence_ignores_unavailable_member_predictions():
    members = [
        {"_model": "gfs_seamless", "wind_direction": 180.0, "wind_speed": 5.0}
        for _ in range(20)
    ]

    with patch(
        "web_app.predict_flight_probability",
        return_value={"probability": None, "calculation_status": "insufficient_history"},
    ):
        confidence = calculate_confidence(members)

    assert confidence["grade"] is None
    assert confidence["source"] == "unavailable"
    assert confidence["models"]["gfs_seamless"]["status"] == "insufficient_members"


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


def test_deterministic_risk_summary_keeps_jma_risk_labels():
    assert deterministic_risk_summary({"warning_msg": "特になし"}) == "特になし"
    assert deterministic_risk_summary(
        {"warning_msg": "突風注意 (予報突風: 16.0 m/s)、台風接近リスク小"}
    ) == "突風注意、台風接近リスク"


def test_confidence_note_uses_short_wording():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert "予報シナリオの一致度" in template
    assert "{{ day.confidence.valid_member_count }}・期待{{ day.confidence.expected_member_count }}通り" in template


def test_mobile_css_prevents_horizontal_overflow():
    stylesheet = (BASE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    assert "overflow-x: clip" in stylesheet
    assert ".header::after { right: 0; width: 55%; }" in stylesheet


def test_stylesheet_url_has_cache_buster():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'href="{{ asset_prefix }}static/styles.css?v=' in template


def test_template_includes_quick_guide_for_non_experts():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    stylesheet = (BASE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'class="quick-guide"' in template
    assert "◎95以上 / 〇75以上 / △35以上 / ×35未満" in template
    assert "比較欄にはGFS・ECMWF・JMAを併記" in template
    assert ".quick-guide" in stylesheet


def test_template_includes_forecast_day_index():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    stylesheet = (BASE_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    assert 'class="forecast-index"' in template
    assert 'href="#date-{{ day.date }}"' in template
    assert ".forecast-index-links" in stylesheet


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
    assert 'src="{{ asset_prefix }}{{ model.flag_path }}"' in template
    assert "model-probability--{{ model.tone }}" in template
    assert "モデル別リスク" in template
    assert "model-risk--{{ model.risk_tone }}" in template
    assert "JMA主予報 / 過去実績×リスク係数による参考スコア" in template
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
        "ecmwf_probability": 34.9,
        "jma_probability": 96.0,
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
    assert "〇</span>76.0<span class=\"score-unit\"> / 100" in body
    assert "×</span>34.9<span class=\"score-unit\"> / 100" in body
    assert "JP" in body
    assert "JMA" in body


def test_insufficient_probability_renders_as_unavailable_without_percent():
    flight = decorate_flight_for_display(
        {
            "date": "2026-06-20",
            "number": "ANA1891(1便)",
            "raw_number": "ANA1891",
            "time": "08:30",
            "probability": None,
            "calculation_status": "insufficient_history",
            "warning_msg": "過去実績が4件のため、統計参考値を算出できません。",
            "wind_direction": 180.0,
            "wind_direction_label": "南",
            "wind_speed": 4.0,
            "wind_gusts": 7.0,
            "cloud_cover_low": 20.0,
            "visibility": 15.0,
            "precipitation": None,
            "pressure_msl": None,
            "similar_history": [],
        }
    )
    day = {
        "date": "2026-06-20",
        "date_label": "6/20",
        "weekday": "土",
        "flights": [flight],
        "confidence": {"grade": "A", "label": "10ポイント以内", "source": "lead_time", "lead_days": 1},
    }
    with app.test_request_context("/"):
        body = render_template("index.html", days=[day], error=None, updated_at="2026/06/20 00:00")

    assert 'class="probability-unavailable"' in body
    assert "算出不可" in body
    assert "None%" not in body


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
            "jma_probability": 88.0,
            "jma_risk": "特になし",
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
        {
            "label": "JMA",
            "probability": 88.0,
            "symbol": "〇",
            "tone": "ok",
            "risk": "特になし",
            "risk_tone": "ok",
            "flag_path": "static/flags/jp.svg",
            "flag_alt": "JP",
        },
    ]


def test_decorate_flight_for_display_does_not_treat_missing_probability_as_low():
    flight = decorate_flight_for_display(
        {
            "probability": None,
            "calculation_status": "insufficient_history",
            "warning_msg": "過去実績が4件のため、統計参考値を算出できません。",
        }
    )

    assert flight["probability_symbol"] is None
    assert flight["is_low_probability"] is False
    assert flight["model_probabilities"] == []


def test_load_forecast_bundle_uses_cached_main_forecast_on_api_error():
    cached = {
        "cached_at": datetime.now(JST).isoformat(),
        "weather": SAMPLE_WEATHER,
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
    assert bundle["data_updated_at"] == cached["cached_at"]
    assert "前回取得した予報データ" in bundle["notices"][0]
    save.assert_not_called()


def test_load_forecast_bundle_reports_missing_primary_supplement():
    weather = {
        "2026-06-20T08:00": {
            **SAMPLE_WEATHER["2026-06-20T08:00"],
            "_primary_supplement_status": "partial",
        }
    }
    with (
        patch("web_app.fetch_forecast", return_value=weather),
        patch("web_app.fetch_ensemble_forecast", return_value={}),
        patch("web_app.fetch_typhoon_impacts", return_value={"2026-06-20": "low"}),
        patch("web_app.load_cached_forecast_bundle", return_value=None),
        patch("web_app.save_forecast_bundle", return_value={}),
    ):
        bundle = load_forecast_bundle()

    assert "最大瞬間風速・視程の一部を取得できず" in bundle["notices"][0]


def test_load_forecast_bundle_reuses_cached_optional_sources():
    cached = {
        "cached_at": datetime.now(JST).isoformat(),
        "weather": SAMPLE_WEATHER,
        "ensembles": {"cached-ensemble": []},
        "typhoon_impacts": {"2026-06-20": "medium"},
    }

    with (
        patch("web_app.fetch_forecast", return_value=SAMPLE_WEATHER),
        patch("web_app.fetch_ensemble_forecast", side_effect=ValueError("bad ensemble")),
        patch("web_app.fetch_typhoon_impacts", side_effect=ValueError("bad typhoon")),
        patch("web_app.load_cached_forecast_bundle", return_value=cached),
        patch("web_app.save_forecast_bundle") as save,
    ):
        bundle = load_forecast_bundle()

    assert bundle["source"] == "live"
    assert bundle["ensembles"] == cached["ensembles"]
    assert bundle["typhoon_impacts"] == cached["typhoon_impacts"]
    assert "アンサンブル予報は前回取得データ" in bundle["notices"][0]
    assert "台風影響度は前回取得データ" in bundle["notices"][1]
    save.assert_called_once_with(
        SAMPLE_WEATHER,
        ensembles=cached["ensembles"],
        typhoon_impacts=cached["typhoon_impacts"],
        source_updated_at={
            "ensembles": cached["cached_at"],
            "typhoon_impacts": cached["cached_at"],
        },
    )


def test_load_forecast_bundle_does_not_reuse_stale_cached_optional_sources():
    cached = {
        "cached_at": "2020-01-01T00:00:00+09:00",
        "weather": SAMPLE_WEATHER,
        "ensembles": {"cached-ensemble": [{"wind_direction": 180.0, "wind_speed": 12.0}]},
        "typhoon_impacts": {"2026-06-20": "severe"},
    }

    with (
        patch("web_app.fetch_forecast", return_value=SAMPLE_WEATHER),
        patch("web_app.fetch_ensemble_forecast", side_effect=ValueError("bad ensemble")),
        patch("web_app.fetch_typhoon_impacts", side_effect=ValueError("bad typhoon")),
        patch("web_app.load_cached_forecast_bundle", return_value=cached),
        patch("web_app.save_forecast_bundle") as save,
    ):
        bundle = load_forecast_bundle()

    assert bundle["source"] == "live"
    assert bundle["ensembles"] == {}
    assert bundle["typhoon_impacts"] == {}
    assert "アンサンブル予報を取得できませんでした。" in bundle["notices"][0]
    assert "台風影響度を取得できなかったため" in bundle["notices"][1]
    save.assert_called_once_with(
        SAMPLE_WEATHER,
        ensembles={},
        typhoon_impacts={},
        source_updated_at={},
    )


def test_optional_cache_source_cannot_be_refreshed_indefinitely():
    current = datetime.now(JST)
    cached = {
        "cached_at": current.isoformat(),
        "source_updated_at": {
            "weather": current.isoformat(),
            "ensembles": (current - timedelta(hours=8)).isoformat(),
            "typhoon_impacts": current.isoformat(),
        },
        "weather": SAMPLE_WEATHER,
        "ensembles": {"stale-ensemble": []},
        "typhoon_impacts": {"2026-06-20": "low"},
    }

    with (
        patch("web_app.fetch_forecast", return_value=SAMPLE_WEATHER),
        patch("web_app.fetch_ensemble_forecast", side_effect=ValueError("bad ensemble")),
        patch("web_app.fetch_typhoon_impacts", return_value={"2026-06-20": "low"}),
        patch("web_app.load_cached_forecast_bundle", return_value=cached),
        patch("web_app.save_forecast_bundle", return_value={}),
    ):
        bundle = load_forecast_bundle()

    assert bundle["ensembles"] == {}
    assert "アンサンブル予報を取得できませんでした。" in bundle["notices"]


def test_save_forecast_bundle_preserves_cached_source_timestamp(tmp_path):
    old_timestamp = "2026-07-15T01:00:00+09:00"

    payload = save_forecast_bundle(
        SAMPLE_WEATHER,
        ensembles={"cached-ensemble": []},
        cache_file=tmp_path / "forecast.json",
        source_updated_at={"ensembles": old_timestamp},
    )

    assert payload["source_updated_at"]["ensembles"] == old_timestamp
    assert payload["source_updated_at"]["weather"] == payload["cached_at"]


def test_load_forecast_bundle_rejects_stale_main_cache():
    cached = {
        "cached_at": "2020-01-01T00:00:00+09:00",
        "weather": SAMPLE_WEATHER,
    }
    with (
        patch("web_app.fetch_forecast", side_effect=ValueError("bad data")),
        patch("web_app.load_cached_forecast_bundle", return_value=cached),
    ):
        try:
            load_forecast_bundle()
        except ValueError as exc:
            assert str(exc) == "bad data"
        else:
            raise AssertionError("stale main cache must not be used")


def test_future_dated_cache_is_not_fresh():
    now = datetime(2026, 7, 15, 12, 0, tzinfo=JST)
    payload = {"cached_at": (now + timedelta(minutes=1)).isoformat(), "weather": SAMPLE_WEATHER}

    assert is_cached_forecast_fresh(payload, now=now) is False


def test_partial_typhoon_coverage_is_reported():
    weather = {
        "2026-07-15T08:00": SAMPLE_WEATHER["2026-06-20T08:00"],
        "2026-07-16T08:00": SAMPLE_WEATHER["2026-06-20T08:00"],
    }
    with (
        patch("web_app.fetch_forecast", return_value=weather),
        patch("web_app.fetch_ensemble_forecast", return_value={}),
        patch("web_app.fetch_typhoon_impacts", return_value={"2026-07-15": "low"}),
        patch("web_app.load_cached_forecast_bundle", return_value=None),
        patch("web_app.save_forecast_bundle", return_value={}),
    ):
        bundle = load_forecast_bundle()

    assert "2026-07-16の台風影響度は未取得" in bundle["notices"][0]


def test_select_evenly_balances_ensemble_members():
    members = list(range(51))

    selected = _select_evenly(members, 31)

    assert len(selected) == 31
    assert selected[0] == 0
    assert selected[-1] == 50
    assert selected == sorted(set(selected))


def test_fallback_confidence_decreases_with_lead_time():
    reference = date(2026, 6, 19)

    assert fallback_confidence(reference, reference)["grade"] is None
    assert fallback_confidence(date(2026, 6, 25), reference)["source"] == "lead_time_caution"


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
        patch("web_app.fetch_typhoon_impacts", return_value={"2026-06-20": "low"}),
        patch("web_app.predict_flight_probability", return_value=result),
        patch("web_app.find_similar_flights", return_value=[]),
        patch("web_app._flight_display_expired", return_value=False),
        app.test_client() as client,
    ):
        response = client.get("/")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "八丈島便 運航の目安" in body
    assert 'class="today-summary"' in body
    assert "今日の運航目安" in body
    assert "ANA公式の運航状況" in body
    assert 'href="https://www.ana.co.jp/fs/dom/jp/"' in body
    assert "運航参考スコア" in body
    assert "88.0 / 100" in body
    assert ">88.0%</strong>" not in body
    assert "羽田空港から八丈島空港へ向かうANA1891・ANA1893・ANA1895便の運航目安を、天気・台風影響度・過去の運航実績から確認できます。" in body
    assert "天候信頼度は、Open-Meteo APIからオープンデータ" not in body
    assert "比較欄にはGFS・ECMWF・JMAを併記" in body
    assert "主予報は気象庁(JMA)モデルをOpen-Meteo経由で使用しています。" in body
    assert "予報データ取得 " in body
    assert "(6時間ごとに更新)" in body
    assert "青: 参考スコア60以上" in body
    assert "オレンジ: 参考スコア60未満" in body
    assert "主予報: 気象庁(JMA) GSM・MSMモデル (Open-Meteo経由)" in body
    assert "主予報(JMA)での統計参考値" in body
    assert "予報シナリオの一致度" in body
    assert "モデル別の運航参考スコア" in body
    assert "未校正の統計参考値で、将来の運航確率ではありません" in body
    assert "表示スコアが過去実績から求めた基礎値より低くなります" in body
    assert "天気予報の更新で条件が変わると、スコアも上がったり下がったりします" in body
    assert ">雲量<" not in body
    assert "なぜ作ったか" in body
    assert "ざっくりどういう仕組みか" in body
    assert "GFS・ECMWFを混ぜずにモデル別" in body
    assert "日本周辺の短期予報を重視してJMAを主予報" in body
    assert "八丈島・東京方面 台風影響目安" in body
    assert "運航率に0.9・0.8・0.7を掛けます" in body
    assert "運航参考スコア60未満の便はオレンジ" in body
    assert "GitHub Actionsで6時間ごとに再計算" in body
    assert "気象業法への配慮" in body
    assert "予報気象情報" in body
    assert "モデル別リスク" in body
    assert "気象条件が近い過去の運航実績10件" in body
    assert "6ポイント以内" not in body


def test_access_stats_render_in_footer_when_static_data_is_available():
    with app.app_context():
        body = render_template(
            "index.html",
            days=[],
            error=None,
            updated_at="2026/08/24 00:00",
            notices=[],
            low_probability_threshold=60,
            access_stats={
                "days": [
                    {"date": "2026-08-24", "label": "8/24", "pageviews": 1234},
                    {"date": "2026-08-23", "label": "8/23", "pageviews": None},
                ],
                "generated_at": "2026-08-24T09:00+09:00",
            },
        )

    assert "過去7日間のアクセス数" in body
    assert "1,234" in body
    assert "未計測" in body
    assert "2026-08-24T09:00+09:00更新" in body


def test_access_stats_render_last_known_good_as_stale_without_zeroing_counts():
    with app.app_context():
        body = render_template(
            "index.html",
            days=[],
            error=None,
            updated_at="2026/08/24 00:00",
            notices=[],
            low_probability_threshold=60,
            access_stats={
                "status": "stale",
                "days": [{"date": "2026-08-24", "label": "8/24", "pageviews": 12}],
                "generated_at": "2026-08-24T09:00+09:00",
            },
        )

    assert "12" in body
    assert "が最終取得時刻です。現在は更新できていません。" in body
    assert "0ページビュー" not in body


def test_access_stats_render_unavailable_without_fabricating_zero():
    with app.app_context():
        body = render_template(
            "index.html",
            days=[],
            error=None,
            updated_at="2026/08/24 00:00",
            notices=[],
            low_probability_threshold=60,
            access_stats={
                "status": "unavailable",
                "days": [],
                "generated_at": None,
            },
        )

    assert "過去7日間のアクセス数" in body
    assert "現在取得できません。予報の公開には影響ありません。" in body
    assert "ページビュー" not in body
    assert "0ページビュー" not in body


def test_history_template_includes_flight_name_and_visibility_fallback():
    template = (BASE_DIR / "templates" / "index.html").read_text(encoding="utf-8")

    assert "{{ history.date_label }} {{ history.flight_display_name }}" in template
    assert "/ 視程 {% if history.visibility is not none %}{{ history.visibility }} km{% else %}欠測{% endif %}" in template
    assert "{{ model.label }}予報での統計参考値" in template


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
    codeql = (BASE_DIR / ".github" / "workflows" / "codeql.yml").read_text(encoding="utf-8")
    pages = (BASE_DIR / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
    collection = (BASE_DIR / ".github" / "workflows" / "data_collection.yml").read_text(encoding="utf-8")
    evaluation = (BASE_DIR / ".github" / "workflows" / "forecast_evaluation.yml").read_text(encoding="utf-8")

    assert "python -m pytest -q" in ci
    assert "python -m ruff check ." in ci
    assert "python -m pip_audit -r requirements.txt" in ci
    assert "github/codeql-action/analyze@" in codeql
    assert "python data_quality.py --format markdown" in pages
    assert "python data_quality.py --format markdown" in collection
    assert "python collection_monitor.py --days 14" in collection
    assert "--replay-run-id" in (BASE_DIR / "data_collector.py").read_text(encoding="utf-8")
    assert "actions/upload-artifact@" in pages
    assert "actions/upload-artifact@" in collection
    assert "--fail-on error" in pages
    assert "--fail-on error" in collection
    assert "forecast_evaluation.py" in evaluation
    assert "--fail-on-insufficient-data" in evaluation
    for workflow in (ci, codeql, pages, collection, evaluation):
        assert re.search(r"uses:\s+[^\s@]+@[0-9a-f]{40}", workflow)
        assert re.search(r"uses:\s+[^\s@]+@v\d+", workflow) is None


