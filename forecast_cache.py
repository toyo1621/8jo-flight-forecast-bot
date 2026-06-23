import json
import os
from datetime import datetime
from pathlib import Path

from app_config import JST


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_FILE = BASE_DIR / ".cache" / "forecast_bundle.json"
CACHE_VERSION = 1


def _cache_file():
    return Path(os.getenv("FORECAST_CACHE_FILE", DEFAULT_CACHE_FILE))


def save_forecast_bundle(weather, jma=None, ensembles=None, haneda=None, cache_file=None):
    path = Path(cache_file) if cache_file is not None else _cache_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CACHE_VERSION,
        "cached_at": datetime.now(JST).isoformat(),
        "weather": weather,
        "jma": jma or {},
        "ensembles": ensembles or {},
        "haneda": haneda or {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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
