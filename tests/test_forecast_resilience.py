import json
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from flight_forecast.app_config import JST
from flight_forecast.build_static import build_site, forecast_publication_quality
from flight_forecast.forecast_cache import save_forecast_bundle
from flight_forecast.validate_static_site import validate_site
from flight_forecast.web_app import _has_usable_forecast_weather, load_forecast_bundle


def forecast_weather(target_date, gust=7.0):
    return {
        f"{target_date}T{hour:02d}:00": {
            "wind_direction": 180.0,
            "wind_speed": 4.0,
            "wind_gusts": gust,
            "cloud_cover_low": 20.0,
            "visibility": 15.0 if gust is not None else None,
            "precipitation": 0.0,
            "_primary_supplement_status": "complete" if gust is not None else "unavailable",
        }
        for hour in (8, 13, 17)
    }


def seed_forecast_cache(tmp_path, monkeypatch, weather, acquired_at):
    cache_file = tmp_path / "forecast.json"
    monkeypatch.setenv("FORECAST_CACHE_FILE", str(cache_file))
    monkeypatch.delenv("FORECAST_CACHE_MAX_AGE_MINUTES", raising=False)
    save_forecast_bundle(
        weather,
        cache_file=cache_file,
        source_updated_at={"weather": acquired_at},
    )
    return cache_file


def test_failed_supplement_uses_complete_cache_without_refreshing_its_age(tmp_path, monkeypatch):
    now = datetime.now(JST)
    target_date = (now + timedelta(days=1)).date().isoformat()
    cached_weather = forecast_weather(target_date)
    acquired_at = (now - timedelta(hours=1)).isoformat()
    cache_file = seed_forecast_cache(tmp_path, monkeypatch, cached_weather, acquired_at)
    original_bytes = cache_file.read_bytes()

    with patch("flight_forecast.web_app.fetch_forecast", return_value=forecast_weather(target_date, None)):
        bundle = load_forecast_bundle()

    assert bundle["source"] == "cache"
    assert bundle["weather"] == cached_weather
    assert bundle["data_updated_at"] == acquired_at
    assert bundle["source_updated_at"]["weather"] == acquired_at
    assert bundle["source_fallbacks"]["weather"] is True
    assert "必須気象データの補完に失敗" in bundle["notices"][0]
    assert cache_file.read_bytes() == original_bytes


@pytest.mark.parametrize("age", [timedelta(hours=8), timedelta(minutes=-1)])
def test_failed_supplement_rejects_expired_or_future_cache(tmp_path, monkeypatch, age):
    now = datetime.now(JST)
    target_date = (now + timedelta(days=1)).date().isoformat()
    cache_file = seed_forecast_cache(
        tmp_path, monkeypatch, forecast_weather(target_date), (now - age).isoformat()
    )
    original_bytes = cache_file.read_bytes()

    with (
        patch("flight_forecast.web_app.fetch_forecast", return_value=forecast_weather(target_date, None)),
        pytest.raises(ValueError, match="全面欠測"),
    ):
        load_forecast_bundle()

    assert cache_file.read_bytes() == original_bytes


@pytest.mark.parametrize("gust", [None, -1.0, True, "not a number"])
def test_failed_supplement_rejects_missing_or_invalid_cached_gust(tmp_path, monkeypatch, gust):
    now = datetime.now(JST)
    target_date = (now + timedelta(days=1)).date().isoformat()
    seed_forecast_cache(
        tmp_path, monkeypatch, forecast_weather(target_date, gust), now.isoformat()
    )

    with (
        patch("flight_forecast.web_app.fetch_forecast", return_value=forecast_weather(target_date, None)),
        pytest.raises(ValueError, match="全面欠測"),
    ):
        load_forecast_bundle()


def test_partial_missing_weather_keeps_live_values_and_reports_missing_fields(tmp_path, monkeypatch):
    now = datetime.now(JST)
    target_date = (now + timedelta(days=1)).date().isoformat()
    seed_forecast_cache(tmp_path, monkeypatch, forecast_weather(target_date), now.isoformat())
    live_weather = forecast_weather(target_date, None)
    live_weather[f"{target_date}T08:00"] = forecast_weather(target_date)[f"{target_date}T08:00"]

    with (
        patch("flight_forecast.web_app.fetch_forecast", return_value=live_weather),
        patch("flight_forecast.web_app.fetch_ensemble_forecast", return_value={}),
        patch("flight_forecast.web_app.fetch_typhoon_impacts", return_value={}),
    ):
        bundle = load_forecast_bundle()

    assert bundle["source"] == "live"
    assert bundle["weather"] == live_weather
    assert bundle["source_fallbacks"]["weather"] is False
    assert "欠測として計算" in bundle["notices"][0]


def test_completed_or_unrelated_hours_cannot_mask_missing_future_flight_inputs():
    now = datetime(2026, 10, 14, 19, tzinfo=JST)
    weather = {
        **forecast_weather("2026-10-14"),
        **forecast_weather("2026-10-15", None),
        "2026-10-15T04:00": next(iter(forecast_weather("2026-10-15").values())),
    }

    assert _has_usable_forecast_weather(weather, current_time=now) is False


@pytest.mark.parametrize("completed", [False, True])
def test_all_pending_weather_missing_aborts_before_snapshot_save_or_html_write(tmp_path, completed):
    now = datetime(2026, 10, 14, 19, tzinfo=JST)
    days = [{"date": "2026-10-15", "flights": [{"calculation_status": "weather_missing"}]}]
    if completed:
        days.insert(0, {
            "date": "2026-10-14", "flights": [{"calculation_status": "expired"}]
        })

    with (
        patch("flight_forecast.build_static.load_forecast_bundle", return_value={"weather": {}, "ensembles": {}, "typhoon_impacts": {}}),
        patch("flight_forecast.build_static.fetch_detailed_history", return_value=[]),
        patch("flight_forecast.build_static.build_daily_forecasts", return_value=days),
        patch("flight_forecast.build_static.save_prediction_snapshots") as save,
        patch("flight_forecast.build_static.save_prediction_publication_candidates") as save_candidates,
        pytest.raises(RuntimeError, match="全面欠測"),
    ):
        build_site(tmp_path, current_time=now)

    save.assert_not_called()
    save_candidates.assert_not_called()
    assert not (tmp_path / "index.html").exists()


@pytest.mark.parametrize("statuses,expected", [
    (["weather_missing", "available"], (2, 1, 1)),
    (["insufficient_history", "insufficient_history"], (2, 0, 0)),
    (["expired"], (0, 0, 0)),
])
def test_publication_quality_preserves_partial_missing_history_shortage_and_completed_flights(statuses, expected):
    now = datetime(2026, 10, 14, 19, tzinfo=JST)
    days = [
        {"date": "2026-10-13", "flights": [{"calculation_status": "weather_missing"}]},
        {"date": "2026-10-15", "flights": [{"calculation_status": status} for status in statuses]},
    ]

    quality = forecast_publication_quality(days, now)

    assert tuple(quality.values()) == expected


@pytest.mark.parametrize("quality,error", [
    (None, "forecast_qualityが不正"),
    ({"pending_flight_count": True, "weather_missing_flight_count": 0, "available_flight_count": 0}, "forecast_qualityが不正"),
    ({"pending_flight_count": 1, "weather_missing_flight_count": -1, "available_flight_count": 0}, "forecast_qualityが不正"),
    ({"pending_flight_count": 1, "weather_missing_flight_count": 1, "available_flight_count": 1}, "件数が一致"),
    ({"pending_flight_count": 2, "weather_missing_flight_count": 2, "available_flight_count": 0}, "全面欠測"),
    ({"pending_flight_count": 2, "weather_missing_flight_count": 1, "available_flight_count": 0}, None),
    ({"pending_flight_count": 2, "weather_missing_flight_count": 0, "available_flight_count": 0}, None),
    ({"pending_flight_count": 0, "weather_missing_flight_count": 0, "available_flight_count": 0}, None),
])
def test_static_validation_rejects_invalid_or_fully_missing_quality_only(tmp_path, quality, error):
    manifest = {
        "artifact_id": "forecast-test", "snapshot_ids": ["s"], "snapshot_count": 1,
        "generated_at": "2026-10-14T19:00:00+09:00", "code_version": "sha", "config_version": "cfg",
        "forecast_quality": quality,
    }
    (tmp_path / "build-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "sitemap.xml").write_text(
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>', encoding="utf-8"
    )

    errors = validate_site(tmp_path)

    if error is None:
        assert errors == []
    else:
        assert any(error in item for item in errors)
