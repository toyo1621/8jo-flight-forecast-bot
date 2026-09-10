import json
import math
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from flight_forecast.app_config import JST

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_FILE = BASE_DIR / ".cache" / "forecast_bundle.json"
CACHE_VERSION = 4
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
    ensembles=None,
    cache_file=None,
    typhoon_impacts=None,
    source_updated_at=None,
):
    path = Path(cache_file) if cache_file is not None else _cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    cached_at = datetime.now(JST).isoformat()
    provided_timestamps = source_updated_at or {}
    source_timestamps = {
        source: provided_timestamps[source]
        for source in ("weather", "ensembles", "typhoon_impacts")
        if provided_timestamps.get(source)
    }
    payload = {
        "version": CACHE_VERSION,
        "cached_at": cached_at,
        "weather": weather,
        "ensembles": ensembles or {},
        "typhoon_impacts": typhoon_impacts or {},
        "source_updated_at": source_timestamps,
    }
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary:
        temporary.write(serialized)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)
    return payload


def _valid_timestamp(value, now=None):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    now = now or datetime.now(JST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    return parsed <= now + timedelta(minutes=5)


def _valid_number(value, minimum=None, maximum=None):
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and not (
        (minimum is not None and number < minimum)
        or (maximum is not None and number > maximum)
    )


def _valid_time_key(value):
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _valid_weather_map(weather):
    if not isinstance(weather, dict) or not weather:
        return False
    ranges = {
        "wind_direction": (0, 360),
        "wind_speed": (0, None),
        "wind_gusts": (0, None),
        "cloud_cover_low": (0, 100),
        "visibility": (0, None),
        "precipitation": (0, None),
        "pressure_msl": (0, None),
        "surface_pressure": (0, None),
    }
    for timestamp, values in weather.items():
        if not _valid_time_key(timestamp) or not isinstance(values, dict):
            return False
        if "wind_direction" not in values or "wind_speed" not in values:
            return False
        for key, (minimum, maximum) in ranges.items():
            if key in values and not _valid_number(values[key], minimum, maximum):
                return False
        if not _valid_number(values.get("wind_direction"), 0, 360):
            return False
        if not _valid_number(values.get("wind_speed"), 0, None):
            return False
    return True


def _valid_ensemble_map(ensembles):
    if not isinstance(ensembles, dict):
        return False
    for timestamp, members in ensembles.items():
        if not _valid_time_key(timestamp) or not isinstance(members, list):
            return False
        for member in members:
            if not isinstance(member, dict):
                return False
            for key in (
                "wind_direction",
                "wind_speed",
                "wind_gusts",
                "cloud_cover_low",
                "visibility",
                "precipitation",
                "pressure_msl",
                "surface_pressure",
            ):
                if key in member and not _valid_number(
                    member[key], 0, 360 if key == "wind_direction" else None
                ):
                    return False
    return True


def _valid_typhoon_map(impacts):
    if not isinstance(impacts, dict):
        return False
    for target_date, impact in impacts.items():
        try:
            date.fromisoformat(target_date)
        except (TypeError, ValueError):
            return False
        if isinstance(impact, str):
            if impact not in {"low", "medium", "high", "severe"}:
                return False
        elif not isinstance(impact, dict) or impact.get("risk_level") not in {
            "low",
            "medium",
            "high",
            "severe",
        }:
            return False
    return True


def _valid_cached_bundle(payload):
    if not isinstance(payload, dict):
        return False
    if payload.get("version") != CACHE_VERSION:
        return False
    if not _valid_weather_map(payload.get("weather")):
        return False
    if not _valid_ensemble_map(payload.get("ensembles")):
        return False
    if not _valid_typhoon_map(payload.get("typhoon_impacts")):
        return False
    timestamps = payload.get("source_updated_at")
    if not isinstance(timestamps, dict):
        return False
    return all(_valid_timestamp(value) for value in timestamps.values() if value)


def load_cached_forecast_bundle(cache_file=None):
    path = Path(cache_file) if cache_file is not None else _cache_file()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not _valid_cached_bundle(payload):
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
