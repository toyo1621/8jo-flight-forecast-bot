import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, render_template

from app_config import (
    ENSEMBLE_FORECAST_URL,
    FLIGHTS,
    FORECAST_CONFIG_VERSION,
    FORECAST_DAYS,
    HACHIJO_AIRPORT_LATITUDE,
    HACHIJO_AIRPORT_LONGITUDE,
    JMA_MODEL_NAME,
    JST,
    LOW_PROBABILITY_THRESHOLD,
    MAIN_FORECAST_URL,
    TYPHOON_IMPACT_API_URL,
    TYPHOON_IMPACT_LABELS,
    TYPHOON_IMPACT_MULTIPLIERS,
    TYPHOON_IMPACT_SOURCE,
    TYPHOON_NUMERIC_ADJUSTMENT_ENABLED,
)
from clients.open_meteo import (
    fetch_deterministic_forecast as client_fetch_deterministic_forecast,
)
from clients.open_meteo import (
    fetch_ensemble_model as client_fetch_ensemble_model,
)
from clients.open_meteo import (
    select_evenly,
)
from clients.typhoon_impact import fetch_typhoon_impacts as client_fetch_typhoon_impacts
from ensemble_evaluation import RISK_LABELS, evaluate_ensemble_members, risk_labels
from flight_metadata import flight_display_name
from forecast_cache import (
    forecast_source_timestamp,
    format_forecast_timestamp,
    is_cached_forecast_fresh,
    load_cached_forecast_bundle,
    save_forecast_bundle,
)
from forecast_engine import find_similar_flights, predict_flight_probability
from presentation import decorate_flight_for_display
from typhoon_impact import has_factor_breakdown, typhoon_risk_level

BASE_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)
PREDICTION_WEATHER_FIELDS = (
    "wind_direction",
    "wind_speed",
    "wind_gusts",
    "cloud_cover_low",
    "visibility",
    "precipitation",
)
PRIMARY_SUPPLEMENT_FIELDS = ("wind_gusts", "visibility")
PRIMARY_SUPPLEMENT_STATUS_KEY = "_primary_supplement_status"
WEATHER_FIELD_SOURCES_KEY = "_weather_field_sources"


def _fetch_deterministic_forecast(model=None, latitude=HACHIJO_AIRPORT_LATITUDE, longitude=HACHIJO_AIRPORT_LONGITUDE):
    return client_fetch_deterministic_forecast(
        model=model,
        latitude=latitude,
        longitude=longitude,
        endpoint=MAIN_FORECAST_URL,
        forecast_days=FORECAST_DAYS,
        request_get=requests.get,
    )


def fetch_forecast():
    primary = _fetch_deterministic_forecast(JMA_MODEL_NAME)
    try:
        supplement = _fetch_deterministic_forecast()
    except (requests.RequestException, ValueError) as exc:
        LOGGER.warning("Open-Meteo supplemental forecast could not be loaded: %s", exc)
        supplement = {}
    return _supplement_primary_forecast(primary, supplement)


def _supplement_primary_forecast(primary, supplement):
    merged = {}
    for timestamp, primary_weather in primary.items():
        weather = dict(primary_weather)
        field_sources = dict(weather.get(WEATHER_FIELD_SOURCES_KEY) or {})
        for field in PREDICTION_WEATHER_FIELDS:
            if field in weather and field not in field_sources:
                field_sources[field] = "jma" if weather.get(field) is not None else "missing"
        supplemental_weather = supplement.get(timestamp, {})
        missing = []
        for field in PRIMARY_SUPPLEMENT_FIELDS:
            if weather.get(field) is None:
                supplemental_value = supplemental_weather.get(field)
                if supplemental_value is not None:
                    weather[field] = supplemental_value
                    field_sources[field] = "open_meteo_supplement"
            if weather.get(field) is None:
                missing.append(field)
                field_sources[field] = "missing"
            elif field not in field_sources:
                field_sources[field] = "jma"
        if not missing:
            status = "complete"
        elif len(missing) == len(PRIMARY_SUPPLEMENT_FIELDS):
            status = "unavailable"
        else:
            status = "partial"
        weather[PRIMARY_SUPPLEMENT_STATUS_KEY] = status
        weather[WEATHER_FIELD_SOURCES_KEY] = field_sources
        merged[timestamp] = weather
    return merged


def fetch_typhoon_impacts():
    return client_fetch_typhoon_impacts(
        endpoint=TYPHOON_IMPACT_API_URL,
        source=TYPHOON_IMPACT_SOURCE,
        valid_levels={"low", *TYPHOON_IMPACT_MULTIPLIERS},
        request_get=requests.get,
    )


def fetch_ensemble_forecast():
    model_variables = (
        (
            "ecmwf_ifs025",
            ("wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "cloud_cover_low", "precipitation"),
            31,
        ),
        (
            "gfs_seamless",
            ("wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "visibility", "precipitation"),
            31,
        ),
    )
    ensembles_by_time = {}
    errors = []
    with ThreadPoolExecutor(max_workers=len(model_variables)) as executor:
        futures = {
            executor.submit(_fetch_ensemble_model, model, variables, max_members): model
            for model, variables, max_members in model_variables
        }
        for future in as_completed(futures):
            try:
                model_ensembles = future.result()
            except (requests.RequestException, ValueError) as exc:
                errors.append(exc)
                continue
            for timestamp, members in model_ensembles.items():
                ensembles_by_time.setdefault(timestamp, []).extend(members)

    if not ensembles_by_time:
        raise errors[0] if errors else ValueError("アンサンブル予報を取得できませんでした。")
    return ensembles_by_time


def _select_evenly(values, limit):
    return select_evenly(values, limit)


def _fetch_ensemble_model(model, variables, max_members=None):
    return client_fetch_ensemble_model(
        model=model,
        variables=variables,
        max_members=max_members,
        latitude=HACHIJO_AIRPORT_LATITUDE,
        longitude=HACHIJO_AIRPORT_LONGITUDE,
        endpoint=ENSEMBLE_FORECAST_URL,
        forecast_days=FORECAST_DAYS,
        request_get=requests.get,
    )


def _prediction_weather(weather):
    return {
        key: weather.get(key)
        for key in PREDICTION_WEATHER_FIELDS
        if key in weather
    }


def calculate_ensemble_evaluation(ensemble_members, baseline_weather=None, flight_number=None):
    return evaluate_ensemble_members(
        ensemble_members,
        baseline_weather=baseline_weather,
        flight_number=flight_number,
        predictor=predict_flight_probability,
        prediction_fields=PREDICTION_WEATHER_FIELDS,
    )


def calculate_confidence(ensemble_members, baseline_weather=None, flight_number=None):
    return calculate_ensemble_evaluation(
        ensemble_members,
        baseline_weather=baseline_weather,
        flight_number=flight_number,
    ).confidence


def calculate_model_reference_probabilities(ensemble_members, baseline_weather=None, flight_number=None):
    return calculate_ensemble_evaluation(
        ensemble_members,
        baseline_weather=baseline_weather,
        flight_number=flight_number,
    ).model_probabilities


def calculate_model_reference_risks(ensemble_members, baseline_weather=None, flight_number=None):
    return calculate_ensemble_evaluation(
        ensemble_members,
        baseline_weather=baseline_weather,
        flight_number=flight_number,
    ).model_risks


def deterministic_risk_summary(result):
    labels = set(risk_labels(result.get("warning_msg")))
    return "、".join(label for label in RISK_LABELS if label in labels) or "特になし"


def fallback_confidence(target_date, reference_date):
    lead_days = max((target_date - reference_date).days, 0)
    return {
        "grade": None,
        "label": "評価不可",
        "lead_days": lead_days,
        "source": "lead_time_caution",
        "confidence_kind": "lead_time_caution",
        "caution": "アンサンブル予報が不足しているため、lead timeだけの暫定評価です。",
    }


def wind_direction_label(degrees):
    if degrees is None:
        return None
    directions = (
        "北",
        "北北東",
        "北東",
        "東北東",
        "東",
        "東南東",
        "南東",
        "南南東",
        "南",
        "南南西",
        "南西",
        "西南西",
        "西",
        "西北西",
        "北西",
        "北北西",
    )
    index = int(((float(degrees) % 360) + 11.25) % 360 // 22.5)
    return directions[index]


def _flight_display_expired(date_string, arrival_time, current_time):
    arrival = datetime.strptime(f"{date_string}T{arrival_time}", "%Y-%m-%dT%H:%M").replace(tzinfo=JST)
    return current_time > arrival + timedelta(minutes=30)


def _append_warning(result, warning):
    result = dict(result)
    current = result.get("warning_msg")
    if current in {None, "なし", "特になし"}:
        result["warning_msg"] = warning
    elif warning not in str(current).split("、"):
        result["warning_msg"] = f"{current}、{warning}"
    result["alert_required"] = True
    return result


def _typhoon_risk_warning(impact):
    label = TYPHOON_IMPACT_LABELS.get(typhoon_risk_level(impact))
    return f"台風接近リスク{label}" if label else None


def _typhoon_factor(impact):
    return TYPHOON_IMPACT_MULTIPLIERS.get(typhoon_risk_level(impact), 1.0)


def _factor_ablation(result, impact):
    probability = result.get("probability")
    if probability is None:
        return {}
    base_probability = result.get("base_probability")
    if base_probability is None:
        base_probability = probability
    weather_factor = result.get("weather_factor", 1.0) or 1.0
    typhoon_factor = _typhoon_factor(impact)
    return {
        "base": round(base_probability, 1),
        "weather_only": round(base_probability * weather_factor, 1),
        "typhoon_only": round(base_probability * typhoon_factor, 1),
        "combined": round(base_probability * weather_factor * typhoon_factor, 1),
    }


def _with_typhoon_impact(result, impact):
    result = dict(result)
    risk_level = typhoon_risk_level(impact)
    multiplier = TYPHOON_IMPACT_MULTIPLIERS.get(risk_level)
    warning = _typhoon_risk_warning(impact)
    if risk_level is None or result.get("probability") is None:
        return result
    base_probability = result.get("base_probability")
    if base_probability is None:
        base_probability = result["probability"]
    weather_factor = result.get("weather_factor", 1.0) or 1.0
    result["typhoon_factor"] = 1.0 if multiplier is None else multiplier
    result["factor_ablation"] = _factor_ablation(result, impact)
    if multiplier is None:
        result["typhoon_adjustment_status"] = "not_applicable"
        return result
    if not TYPHOON_NUMERIC_ADJUSTMENT_ENABLED or not has_factor_breakdown(impact):
        result["typhoon_factor"] = None
        result["typhoon_adjustment_status"] = "warning_only"
        return _append_warning(result, f"{warning}（数値補正なし）")
    result["typhoon_adjustment_status"] = "applied"
    result["probability"] = round(base_probability * weather_factor * multiplier, 1)
    return _append_warning(result, warning)


def _with_typhoon_probability_adjustment(probability, impact):
    multiplier = TYPHOON_IMPACT_MULTIPLIERS.get(typhoon_risk_level(impact))
    if (
        probability is None
        or multiplier is None
        or not TYPHOON_NUMERIC_ADJUSTMENT_ENABLED
        or not has_factor_breakdown(impact)
    ):
        return probability
    return round(probability * multiplier, 1)


def _with_typhoon_risk_summary(summary, impact):
    warning = _typhoon_risk_warning(impact)
    if warning is None:
        return summary
    if not summary or summary == "特になし":
        return warning
    if warning in summary:
        return summary
    if "台風接近リスク" in summary:
        return summary.replace("台風接近リスク", warning, 1)
    return f"{summary}、{warning}"


def _log_or_print(logger, message, exc):
    if logger is None:
        return
    if hasattr(logger, "warning"):
        logger.warning("%s: %s", message, exc)
    else:
        logger(f"{message}: {exc}")


def _append_typhoon_coverage_notice(notices, weather, impacts):
    if not impacts:
        return
    forecast_dates = sorted({timestamp[:10] for timestamp in weather})
    missing_dates = [date_string for date_string in forecast_dates if date_string not in impacts]
    if not missing_dates:
        return
    if len(missing_dates) == 1:
        period = missing_dates[0]
    else:
        period = f"{missing_dates[0]}〜{missing_dates[-1]}"
    notices.append(f"{period}の台風影響度は未取得のため、台風補正を適用していません。")


def _append_typhoon_factor_notice(notices, impacts):
    missing_breakdown_dates = [
        date_string
        for date_string, impact in impacts.items()
        if typhoon_risk_level(impact) in TYPHOON_IMPACT_MULTIPLIERS
        and not has_factor_breakdown(impact)
    ]
    if missing_breakdown_dates:
        notices.append(
            "台風影響度の因子内訳が取得できないため、"
            "該当日の台風接近リスクは注意表示のみで数値補正していません。"
        )
    if not TYPHOON_NUMERIC_ADJUSTMENT_ENABLED:
        notices.append(
            "台風補正の検証用feature flagが無効のため、台風接近リスクは注意表示のみです。"
        )


def load_forecast_bundle(logger=None):
    cached = load_cached_forecast_bundle()
    fresh_cached = cached if is_cached_forecast_fresh(cached, source="weather") else None
    notices = []

    def cached_source(source):
        if cached and is_cached_forecast_fresh(cached, source=source):
            return cached.get(source, {})
        return {}

    try:
        weather = fetch_forecast()
    except (requests.RequestException, ValueError) as exc:
        if fresh_cached:
            _log_or_print(logger, "Main forecast unavailable; using cached forecast", exc)
            notices.append("予報APIに接続できないため、前回取得した予報データを表示しています。")
            cached_ensembles = cached_source("ensembles")
            cached_typhoon_impacts = cached_source("typhoon_impacts")
            if not cached_ensembles:
                notices.append("有効期限内のアンサンブル予報キャッシュがありません。")
            if not cached_typhoon_impacts:
                notices.append("有効期限内の台風影響度キャッシュがないため、台風補正は適用していません。")
            _append_typhoon_coverage_notice(
                notices,
                fresh_cached["weather"],
                cached_typhoon_impacts,
            )
            _append_typhoon_factor_notice(notices, cached_typhoon_impacts)
            return {
                "weather": fresh_cached["weather"],
                "ensembles": cached_ensembles,
                "typhoon_impacts": cached_typhoon_impacts,
                "notices": notices,
                "source": "cache",
                "data_updated_at": forecast_source_timestamp(fresh_cached, "weather"),
                "source_updated_at": fresh_cached.get("source_updated_at", {}),
                "source_fallbacks": {
                    "weather": True,
                    "ensembles": bool(cached_ensembles),
                    "typhoon_impacts": bool(cached_typhoon_impacts),
                },
                "config_version": FORECAST_CONFIG_VERSION,
            }
        raise

    if any(
        item.get(PRIMARY_SUPPLEMENT_STATUS_KEY) in {"partial", "unavailable"}
        for item in weather.values()
    ):
        notices.append(
            "JMAモデルで提供されない最大瞬間風速・視程の一部を取得できず、"
            "該当項目は欠測として計算しています。"
        )

    source_updated_at = {}
    source_fallbacks = {
        "weather": False,
        "ensembles": False,
        "typhoon_impacts": False,
    }
    try:
        ensembles = fetch_ensemble_forecast()
    except (requests.RequestException, ValueError) as exc:
        _log_or_print(logger, "Ensemble forecast could not be loaded", exc)
        ensembles = cached_source("ensembles")
        if ensembles:
            source_updated_at["ensembles"] = forecast_source_timestamp(cached, "ensembles")
            source_fallbacks["ensembles"] = True
            notices.append("アンサンブル予報は前回取得データを使用しています。")
        else:
            notices.append("アンサンブル予報を取得できませんでした。")

    try:
        typhoon_impacts = fetch_typhoon_impacts()
    except (requests.RequestException, ValueError) as exc:
        _log_or_print(logger, "Typhoon impact scores could not be loaded", exc)
        typhoon_impacts = cached_source("typhoon_impacts")
        if typhoon_impacts:
            source_updated_at["typhoon_impacts"] = forecast_source_timestamp(cached, "typhoon_impacts")
            source_fallbacks["typhoon_impacts"] = True
            notices.append("台風影響度は前回取得データを使用しています。")
        else:
            notices.append("台風影響度を取得できなかったため、台風補正は適用していません。")

    _append_typhoon_coverage_notice(notices, weather, typhoon_impacts)
    _append_typhoon_factor_notice(notices, typhoon_impacts)
    saved = save_forecast_bundle(
        weather,
        ensembles=ensembles,
        typhoon_impacts=typhoon_impacts,
        source_updated_at=source_updated_at,
    )
    data_updated_at = (
        forecast_source_timestamp(saved, "weather") if isinstance(saved, dict) else None
    ) or datetime.now(JST).isoformat()
    return {
        "weather": weather,
        "ensembles": ensembles,
        "typhoon_impacts": typhoon_impacts,
        "notices": notices,
        "source": "live",
        "data_updated_at": data_updated_at,
        "source_updated_at": (
            saved.get("source_updated_at", source_updated_at)
            if isinstance(saved, dict)
            else source_updated_at
        ),
        "source_fallbacks": source_fallbacks,
        "config_version": FORECAST_CONFIG_VERSION,
    }


def build_daily_forecasts(
    weather_by_time,
    ensembles_by_time=None,
    reference_date=None,
    current_time=None,
    typhoon_impacts_by_date=None,
):
    ensembles_by_time = ensembles_by_time or {}
    typhoon_impacts_by_date = typhoon_impacts_by_date or {}
    current_time = current_time or datetime.now(JST)
    reference_date = reference_date or current_time.date()
    dates = sorted({timestamp[:10] for timestamp in weather_by_time})
    days = []
    for date_string in dates:
        date = datetime.strptime(date_string, "%Y-%m-%d").replace(tzinfo=JST)
        typhoon_impact = typhoon_impacts_by_date.get(date_string)
        flights = []
        for flight in FLIGHTS:
            if date.date() == current_time.date() and _flight_display_expired(date_string, flight["time"], current_time):
                continue
            timestamp = f"{date_string}T{flight['forecast_hour']:02d}:00"
            weather = weather_by_time.get(timestamp)
            if (
                weather is None
                or weather.get("wind_direction") is None
                or weather.get("wind_speed") is None
            ):
                continue
            result = predict_flight_probability(
                **_prediction_weather(weather),
                flight_number=flight["number"],
            )
            ensemble_evaluation = calculate_ensemble_evaluation(
                ensembles_by_time.get(timestamp, []),
                weather,
                flight_number=flight["number"],
            )
            confidence = ensemble_evaluation.confidence
            result = _with_typhoon_impact(result, typhoon_impact)
            model_probabilities = ensemble_evaluation.model_probabilities
            model_probabilities = {
                model: _with_typhoon_probability_adjustment(probability, typhoon_impact)
                for model, probability in model_probabilities.items()
            }
            model_risks = ensemble_evaluation.model_risks
            model_risks = {
                model: _with_typhoon_risk_summary(risk, typhoon_impact)
                for model, risk in model_risks.items()
            }
            flights.append(
                decorate_flight_for_display(
                    {
                        **flight,
                        **weather,
                        **result,
                        "number": flight_display_name(flight["number"]),
                        "raw_number": flight["number"],
                        "similar_history": find_similar_flights(flight["number"], weather),
                        "gfs_probability": model_probabilities.get("gfs_seamless"),
                        "gfs_risk": model_risks.get("gfs_seamless"),
                        "ecmwf_probability": model_probabilities.get("ecmwf_ifs025"),
                        "ecmwf_risk": model_risks.get("ecmwf_ifs025"),
                        "jma_probability": result.get("probability"),
                        "jma_risk": deterministic_risk_summary(result),
                        "confidence": confidence,
                        "wind_direction_label": wind_direction_label(weather["wind_direction"]),
                    }
                )
            )
        if flights:
            confidence_values = [
                flight["confidence"]
                for flight in flights
                if flight["confidence"] and flight["confidence"].get("grade")
            ]
            if confidence_values:
                day_confidence = max(
                    confidence_values,
                    key=lambda confidence: "ABCDE".index(confidence["grade"]),
                )
                day_confidence = dict(day_confidence)
                day_confidence["summary_basis"] = "worst_flight"
                day_confidence["flight_count"] = len(flights)
                day_confidence["evaluated_flight_count"] = len(confidence_values)
                day_confidence["unevaluated_flight_count"] = len(flights) - len(confidence_values)
            else:
                day_confidence = fallback_confidence(date.date(), reference_date)
            days.append(
                {
                    "date": date_string,
                    "date_label": f"{date.month}/{date.day}",
                    "weekday": "月火水木金土日"[date.weekday()],
                    "flights": flights,
                    "confidence": day_confidence,
                }
            )
    return days


def create_app():
    app = Flask(__name__)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    def index():
        error = None
        days = []
        updated_at = None
        try:
            bundle = load_forecast_bundle(app.logger)
            days = build_daily_forecasts(
                bundle["weather"],
                bundle["ensembles"],
                typhoon_impacts_by_date=bundle["typhoon_impacts"],
            )
            notices = bundle["notices"]
            updated_at = format_forecast_timestamp(bundle.get("data_updated_at"))
        except (requests.RequestException, ValueError, OSError) as exc:
            app.logger.warning("Forecast could not be loaded: %s", exc)
            error = "現在、予報を取得できません。時間をおいてもう一度お試しください。"
            notices = []

        return render_template(
            "index.html",
            days=days,
            error=error,
            updated_at=updated_at or "取得できません",
            notices=notices,
            low_probability_threshold=LOW_PROBABILITY_THRESHOLD,
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run()

