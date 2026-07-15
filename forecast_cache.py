import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from app_config import JST


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_FILE = BASE_DIR / ".cache" / "forecast_bundle.json"
CACHE_VERSION = 3
DEFAULT_CACHE_MAX_AGE = timedelta(hours=7)


def _cache_file():
    return Path(os.getenv("FORECAST_CACHE_FILE", DEFAULT_CACHE_FILE))


def _cache_max_age():
    minutes = os.getenv("FORECAST_CACHE_MAX_AGE_MINUTES")
    if not minutes:
        return DEFAULT_CACHE_MAX_AGE
    try:
        return timedelta(minutes=max(0, int(minutes)))
    except ValueError:
        return DEFAULT_CACHE_MAX_AGE


def save_forecast_bundle(
    weather,
    jma=None,
    ensembles=None,
    cache_file=None,
    typhoon_impacts=None,
    source_updated_at=None,
):
    path = Path(cache_file) if cache_file is not None else _cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    cached_at = datetime.now(JST).isoformat()
    provided_timestamps = source_updated_at or {}
    payload = {
        "version": CACHE_VERSION,
        "cached_at": cached_at,
        "weather": weather,
        "jma": jma or {},
        "ensembles": ensembles or {},
        "typhoon_impacts": typhoon_impacts or {},
        "source_updated_at": {
            "weather": provided_timestamps.get("weather", cached_at),
            "jma": provided_timestamps.get("jma", cached_at),
            "ensembles": provided_timestamps.get("ensembles", cached_at),
            "typhoon_impacts": provided_timestamps.get("typhoon_impacts", cached_at),
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def load_cached_forecast_bundle(cache_file=None):
    path = Path(cache_file) if cache_file is not None else _cache_file()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("version") != CACHE_VERSION or not payload.get("weather"):
        return None
    return payload


def forecast_source_timestamp(payload, source="weather"):
    if not payload:
        return None
    timestamps = payload.get("source_updated_at")
    if isinstance(timestamps, dict) and timestamps.get(source):
        return timestamps[source]
    return payload.get("cached_at")


def is_cached_forecast_fresh(payload, now=None, max_age=None, source="weather"):
    timestamp = forecast_source_timestamp(payload, source)
    if not timestamp:
        return False
    try:
        cached_at = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return False
    now = now or datetime.now(JST)
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=JST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    max_age = max_age if max_age is not None else _cache_max_age()
    age = now - cached_at
    return timedelta(0) <= age <= max_age


def format_forecast_timestamp(value):
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=JST)
    return timestamp.astimezone(JST).strftime("%Y/%m/%d %H:%M")
