import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app_config import (
    ENSEMBLE_FORECAST_URL,
    FORECAST_CONFIG_VERSION,
    JST,
    MAIN_FORECAST_URL,
)
from typhoon_impact import normalize_typhoon_impact

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
WEATHER_FIELD_SOURCES_KEY = "_weather_field_sources"
MODEL_SPECS = (
    ("jma_seamless", "jma_probability", "weather"),
    ("gfs_seamless", "gfs_probability", "ensembles"),
    ("ecmwf_ifs025", "ecmwf_probability", "ensembles"),
)


class CalculationStatus(str, Enum):
    AVAILABLE = "available"
    INSUFFICIENT_HISTORY = "insufficient_history"
    WEATHER_MISSING = "weather_missing"
    MODEL_FETCH_FAILED = "model_fetch_failed"
    INSUFFICIENT_MEMBERS = "insufficient_members"
    INVALID_INPUT = "invalid_input"
    INVALID_OUTPUT = "invalid_output"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ModelInput:
    model: str
    payload: dict
    source: str

    def as_dict(self):
        return {"model": self.model, **self.payload}


def runtime_prediction_identity():
    """Return the workflow identity used by a single static build."""
    return {
        "run_id": os.getenv("GITHUB_RUN_ID") or f"local-{uuid.uuid4().hex}",
        "run_attempt": _run_attempt(os.getenv("GITHUB_RUN_ATTEMPT")),
        "code_version": os.getenv("GITHUB_SHA") or "unknown-local",
    }


def _run_attempt(value):
    try:
        attempt = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, attempt)


def build_artifact_id(
    run_id, run_attempt, generated_at, code_version, config_version, snapshot_ids=None
):
    snapshot_identity = json.dumps(sorted(snapshot_ids or ()), separators=(",", ":"))
    value = ":".join(
        str(item)
        for item in (
            run_id,
            _run_attempt(run_attempt),
            generated_at,
            code_version,
            config_version,
            snapshot_identity,
        )
    )
    return f"forecast-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


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


def _json_hash(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _valid_probability(value):
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value)) and 0 <= float(value) <= 100
    except (TypeError, ValueError):
        return False


def _model_status(probability, flight_status, model_summary=None):
    if _valid_probability(probability):
        return "available"
    if model_summary:
        summary_status = model_summary.get("status")
        member_count = model_summary.get("member_count", 0)
        if summary_status in {
            "insufficient_history",
            "weather_missing",
            "model_fetch_failed",
            "insufficient_members",
            "invalid_input",
            "invalid_output",
            "expired",
        }:
            return summary_status
        if summary_status == "unavailable":
            return CalculationStatus.MODEL_FETCH_FAILED.value
        if member_count:
            return CalculationStatus.INSUFFICIENT_MEMBERS.value
    if flight_status == "available":
        return CalculationStatus.INVALID_OUTPUT.value
    valid_statuses = {status.value for status in CalculationStatus}
    return flight_status if flight_status in valid_statuses else CalculationStatus.INVALID_INPUT.value


def _model_input(flight, model, primary_weather, field_sources):
    if model == "jma_seamless":
        return ModelInput(
            model=model,
            payload={"weather": primary_weather, "field_sources": field_sources},
            source="weather",
        )
    members = [
        {
            "member_id": item.get("member_id"),
            "weather": item.get("weather", {}),
        }
        for item in (flight.get("_ensemble_member_inputs") or [])
        if item.get("model") == model
    ]
    members.sort(key=lambda item: str(item.get("member_id") or ""))
    return ModelInput(model=model, payload={"members": members}, source="ensembles")


def build_prediction_snapshot_rows(
    days,
    bundle,
    generated_at=None,
    run_id=None,
    run_attempt=None,
):
    generated_at = generated_at or datetime.now(JST).isoformat()
    generated_timestamp = _timestamp(generated_at) or generated_at
    identity = runtime_prediction_identity()
    run_id = run_id or identity["run_id"]
    run_attempt = _run_attempt(run_attempt if run_attempt is not None else identity["run_attempt"])
    code_version = identity["code_version"]
    config_version = bundle.get("config_version") or FORECAST_CONFIG_VERSION
    source_updated_at = bundle.get("source_updated_at") or {}
    source_fallbacks = bundle.get("source_fallbacks") or {}
    typhoon_impacts = bundle.get("typhoon_impacts") or {}
    rows = []

    for day in days:
        date_string = day.get("date")
        typhoon_impact = normalize_typhoon_impact(typhoon_impacts.get(date_string))
        typhoon_risk_level = typhoon_impact.get("risk_level")
        for flight in day.get("flights", []):
            flight_number = flight.get("raw_number") or flight.get("flight_number")
            if not date_string or not flight_number:
                continue
            valid_at = _valid_at(date_string, flight.get("forecast_hour"))
            weather_values = {
                key: flight.get(key)
                for key in SNAPSHOT_WEATHER_FIELDS
                if key in flight
            }
            field_sources = flight.get(WEATHER_FIELD_SOURCES_KEY)
            if not isinstance(field_sources, dict):
                field_sources = {
                    key: "unknown" for key in weather_values if key != "_primary_supplement_status"
                }
            calculation_status = flight.get("calculation_status")
            if calculation_status is None:
                calculation_status = "available" if _valid_probability(flight.get("probability")) else "unavailable"

            model_statuses = flight.get("_model_calculation_statuses") or {}
            model_summaries = (flight.get("confidence") or {}).get("models") or {}

            for model, probability_key, source_key in MODEL_SPECS:
                probability = flight.get(probability_key)
                model_status = _model_status(
                    probability,
                    model_statuses.get(model, calculation_status),
                    model_summaries.get(model),
                )
                retrieved_at = _timestamp(source_updated_at.get(source_key))
                fallback_used = bool(source_fallbacks.get(source_key))
                valid_source = valid_at is not None
                provenance_status = "known" if retrieved_at and valid_source else "unknown"
                source_endpoint = (
                    MAIN_FORECAST_URL if source_key == "weather" else ENSEMBLE_FORECAST_URL
                )
                model_input_record = _model_input(flight, model, weather_values, field_sources)
                model_input = model_input_record.as_dict()
                model_input_json = json.dumps(
                    model_input, ensure_ascii=False, sort_keys=True, default=str
                )
                weather_json = model_input_json
                weather_field_sources_json = json.dumps(
                    field_sources if model == "jma_seamless" else {"model": model, "source": "ensemble_members"},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                history_snapshot = {
                    "data_count": flight.get("data_count"),
                    "step_used": flight.get("step_used"),
                    "history_flight_number": flight.get("history_flight_number"),
                    "history_fingerprint": flight.get("history_fingerprint"),
                }
                row = {
                        "run_id": run_id,
                        "run_attempt": run_attempt,
                        "forecast_target_date": date_string,
                        "flight_number": flight_number,
                        "model": model,
                        "calculation_status": model_status,
                        "probability": probability,
                        "base_probability": flight.get("base_probability")
                        if model == "jma_seamless"
                        else None,
                        "weather_factor": flight.get("weather_factor")
                        if model == "jma_seamless"
                        else None,
                        "typhoon_factor": flight.get("typhoon_factor"),
                        "factor_breakdown_json": json.dumps(
                            {
                                "base_probability": flight.get("base_probability"),
                                "weather_factor": flight.get("weather_factor"),
                                "weather_factors": flight.get("weather_factors", {}),
                                "typhoon_factor": flight.get("typhoon_factor"),
                                "typhoon_adjustment_status": flight.get(
                                    "typhoon_adjustment_status"
                                ),
                                "ablation": flight.get("factor_ablation", {}),
                                "external_typhoon": typhoon_impact,
                                "history": history_snapshot,
                            }
                            if model == "jma_seamless"
                            else {
                                "external_typhoon": typhoon_impact,
                                "history": history_snapshot,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
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
                        "model_input_json": model_input_json,
                        "weather_field_sources_json": weather_field_sources_json,
                        "publication_tracking_enabled": True,
                        "typhoon_risk_level": typhoon_risk_level,
                        "created_at": generated_timestamp,
                    }
                content_fields = {
                    key: value
                    for key, value in row.items()
                    if key
                    not in {
                        "created_at",
                        "prediction_generated_at",
                        "run_id",
                        "run_attempt",
                        "weather_retrieved_at",
                        "lead_hours",
                    }
                }
                fingerprint_fields = dict(content_fields)
                if valid_at is None:
                    # A missing valid time is a stable unavailable input, not the
                    # build timestamp. This keeps retries idempotent.
                    fingerprint_fields["weather_valid_at"] = "unknown"
                content_fingerprint = _json_hash(fingerprint_fields)
                row["content_fingerprint"] = content_fingerprint
                row["input_fingerprint"] = _json_hash(model_input)
                row["snapshot_id"] = content_fingerprint
                rows.append(row)
    return rows
