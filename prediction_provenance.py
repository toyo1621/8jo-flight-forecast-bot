import hashlib
import json
import os
import uuid
from datetime import datetime

from app_config import (
    ENSEMBLE_FORECAST_URL,
    FORECAST_CONFIG_VERSION,
    JST,
    MAIN_FORECAST_URL,
)

SNAPSHOT_WEATHER_FIELDS = (
    "wind_direction",
    "wind_speed",
    "wind_gusts",
    "cloud_cover_low",
    "visibility",
    "precipitation",
    "pressure_msl",
    "surface_pressure",
    "_primary_supplement_status",
)
MODEL_SPECS = (
    ("jma_seamless", "jma_probability", "weather"),
    ("gfs_seamless", "gfs_probability", "ensembles"),
    ("ecmwf_ifs025", "ecmwf_probability", "ensembles"),
)


def _timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=JST)
    return parsed.isoformat()


def _valid_at(date_string, forecast_hour):
    try:
        return datetime.fromisoformat(
            f"{date_string}T{int(forecast_hour):02d}:00:00+09:00"
        )
    except (TypeError, ValueError):
        return None


def _lead_hours(valid_at, retrieved_at):
    if valid_at is None or retrieved_at is None:
        return None
    return max(0, round((valid_at - retrieved_at).total_seconds() / 3600))


def _snapshot_id(run_id, date_string, flight_number, model, code_version, config_version):
    value = f"{run_id}:{date_string}:{flight_number}:{model}:{code_version}:{config_version}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_prediction_snapshot_rows(days, bundle, generated_at=None, run_id=None):
    generated_at = generated_at or datetime.now(JST).isoformat()
    generated_timestamp = _timestamp(generated_at) or generated_at
    run_id = run_id or os.getenv("GITHUB_RUN_ID") or f"local-{uuid.uuid4().hex}"
    code_version = os.getenv("GITHUB_SHA") or "unknown-local"
    config_version = bundle.get("config_version") or FORECAST_CONFIG_VERSION
    source_updated_at = bundle.get("source_updated_at") or {}
    source_fallbacks = bundle.get("source_fallbacks") or {}
    typhoon_impacts = bundle.get("typhoon_impacts") or {}
    rows = []

    for day in days:
        date_string = day.get("date")
        typhoon_risk_level = typhoon_impacts.get(date_string)
        for flight in day.get("flights", []):
            flight_number = flight.get("raw_number") or flight.get("flight_number")
            if not date_string or not flight_number:
                continue
            valid_at = _valid_at(date_string, flight.get("forecast_hour"))
            weather_json = json.dumps(
                {
                    key: flight.get(key)
                    for key in SNAPSHOT_WEATHER_FIELDS
                    if key in flight
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            calculation_status = flight.get("calculation_status")
            if calculation_status is None:
                calculation_status = "available" if flight.get("probability") is not None else "unavailable"

            for model, probability_key, source_key in MODEL_SPECS:
                probability = flight.get(probability_key)
                model_status = calculation_status if probability is None else "available"
                retrieved_at = _timestamp(source_updated_at.get(source_key))
                fallback_used = bool(source_fallbacks.get(source_key))
                valid_source = valid_at is not None
                provenance_status = "known" if retrieved_at and valid_source else "unknown"
                source_endpoint = (
                    MAIN_FORECAST_URL if source_key == "weather" else ENSEMBLE_FORECAST_URL
                )
                rows.append(
                    {
                        "snapshot_id": _snapshot_id(
                            run_id,
                            date_string,
                            flight_number,
                            model,
                            code_version,
                            config_version,
                        ),
                        "run_id": run_id,
                        "forecast_target_date": date_string,
                        "flight_number": flight_number,
                        "model": model,
                        "calculation_status": model_status,
                        "probability": probability,
                        "prediction_generated_at": generated_timestamp,
                        "weather_retrieved_at": retrieved_at,
                        "weather_valid_at": valid_at.isoformat() if valid_at else generated_timestamp,
                        "lead_hours": _lead_hours(valid_at, datetime.fromisoformat(retrieved_at)) if retrieved_at and valid_at else None,
                        "provider": "Open-Meteo",
                        "source_endpoint": source_endpoint,
                        "fallback_used": fallback_used,
                        "fallback_reason": "cached_source" if fallback_used else None,
                        "code_version": code_version,
                        "config_version": config_version,
                        "provenance_status": provenance_status,
                        "weather_json": weather_json,
                        "typhoon_risk_level": typhoon_risk_level,
                        "created_at": generated_timestamp,
                    }
                )
    return rows
