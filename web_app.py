import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

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
from ensemble_quality import evaluate_ensemble_confidence
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
from typhoon_impact import (
    has_factor_breakdown,
    normalize_typhoon_impact,
    typhoon_risk_level,
)

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


def _fetch_deterministic_forecast(model=None, latitude=HACHIJO_AIRPORT_LATITUDE, longitude=HACHIJO_AIRPORT_LONGITUDE):
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover_low,visibility,precipitation,pressure_msl,surface_pressure",
        "wind_speed_unit": "ms",
        "timezone": "Asia/Tokyo",
        "forecast_days": FORECAST_DAYS,
    }
    if model:
        params["models"] = model
    response = requests.get(
        MAIN_FORECAST_URL,
        params=params,
        timeout=10,
    )
    response.raise_for_status()
    hourly = response.json().get("hourly", {})
    times = hourly.get("time", [])
    required = {
        "wind_speed_10m",
        "wind_direction_10m",
        "cloud_cover_low",
        "precipitation",
    }
    if not times or any(len(hourly.get(key, [])) != len(times) for key in required):
        raise ValueError("気象データの構造が正しくありません。")

    weather_by_time = {}
    for index, timestamp in enumerate(times):
        weather_by_time[timestamp] = {
            "wind_speed": hourly["wind_speed_10m"][index],
            "wind_direction": hourly["wind_direction_10m"][index],
            "wind_gusts": _optional_hourly_value(hourly, "wind_gusts_10m", index),
            "cloud_cover_low": hourly["cloud_cover_low"][index],
            "visibility": _meters_to_km(_optional_hourly_value(hourly, "visibility", index)),
            "precipitation": hourly["precipitation"][index],
            "pressure_msl": _optional_hourly_value(hourly, "pressure_msl", index),
            "surface_pressure": _optional_hourly_value(hourly, "surface_pressure", index),
        }
    return weather_by_time


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
        supplemental_weather = supplement.get(timestamp, {})
        missing = []
        for field in PRIMARY_SUPPLEMENT_FIELDS:
            if weather.get(field) is None:
                weather[field] = supplemental_weather.get(field)
            if weather.get(field) is None:
                missing.append(field)
        if not missing:
            status = "complete"
        elif len(missing) == len(PRIMARY_SUPPLEMENT_FIELDS):
            status = "unavailable"
        else:
            status = "partial"
        weather[PRIMARY_SUPPLEMENT_STATUS_KEY] = status
        merged[timestamp] = weather
    return merged


def fetch_typhoon_impacts():
    response = requests.get(
        TYPHOON_IMPACT_API_URL,
        params={"source": TYPHOON_IMPACT_SOURCE},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("source") != TYPHOON_IMPACT_SOURCE:
        raise ValueError("台風影響度APIのデータソースが正しくありません。")

    days = payload.get("days")
    if not isinstance(days, list) or not days:
        raise ValueError("台風影響度APIの日別データがありません。")

    valid_levels = {"low", *TYPHOON_IMPACT_MULTIPLIERS}
    source_details = payload.get("sourceDetails") or {}
    score_config = payload.get("scoreConfig") or {}
    factor_weights = (score_config.get("targetWeights") or {}).get("flight", {})
    factor_max_values = score_config.get("factorMaxValues", {})
    impacts = {}
    for day in days:
        if not isinstance(day, dict):
            continue
        targets = day.get("targets")
        target = targets.get("flight", {}) if isinstance(targets, dict) else {}
        if not isinstance(target, dict):
            target = {}
        level = target.get("riskLevel")
        date_string = day.get("date")
        if isinstance(date_string, str) and level in valid_levels:
            impacts[date_string] = normalize_typhoon_impact(
                {
                    "risk_level": level,
                    "score": target.get("score"),
                    "factors": target.get("factors", {}),
                    "inputs": target.get("inputs", {}),
                    "reasons": target.get("reasons", []),
                    "source_mode": source_details.get("mode"),
                    "source_provider": source_details.get("weatherProvider"),
                    "typhoon_provider": source_details.get("typhoonProvider"),
                    "score_config_version": score_config.get("version"),
                    "factor_weights": factor_weights,
                    "factor_max_values": factor_max_values,
                }
            )
    if not impacts:
        raise ValueError("台風影響度APIに利用可能な飛行機向け影響度がありません。")
    return impacts


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
    if limit is None or len(values) <= limit:
        return values
    if limit <= 1:
        return values[:limit]
    indices = [round(index * (len(values) - 1) / (limit - 1)) for index in range(limit)]
    return [values[index] for index in indices]


def _fetch_ensemble_model(model, variables, max_members=None):
    response = requests.get(
        ENSEMBLE_FORECAST_URL,
        params={
            "latitude": HACHIJO_AIRPORT_LATITUDE,
            "longitude": HACHIJO_AIRPORT_LONGITUDE,
            "hourly": ",".join(variables),
            "models": model,
            "wind_speed_unit": "ms",
            "timezone": "Asia/Tokyo",
            "forecast_days": FORECAST_DAYS,
        },
        timeout=20,
    )
    response.raise_for_status()
    hourly = response.json().get("hourly", {})
    times = hourly.get("time", [])
    member_key = variables[0]
    suffixes = [
        key.removeprefix(member_key)
        for key in hourly
        if key == member_key or key.startswith(f"{member_key}_member")
    ]
    suffixes = _select_evenly(suffixes, max_members)
    if not times or not suffixes:
        raise ValueError("アンサンブル予報の構造が正しくありません。")

    ensembles_by_time = {}
    for index, timestamp in enumerate(times):
        members = []
        for suffix in suffixes:
            keys = [f"{variable}{suffix}" for variable in variables]
            if any(key not in hourly or index >= len(hourly[key]) for key in keys):
                continue
            values = [hourly[key][index] for key in keys]
            if any(value is None for value in values):
                continue
            weather = {
                variable.removesuffix("_10m"): value
                for variable, value in zip(variables, values)
            }
            weather["_model"] = model
            if "visibility" in weather:
                weather["visibility"] = _meters_to_km(weather["visibility"])
            members.append(weather)
        ensembles_by_time[timestamp] = members
    return ensembles_by_time


def _meters_to_km(value):
    return round(value / 1000, 1) if value is not None else None


def _optional_hourly_value(hourly, key, index):
    values = hourly.get(key)
    if values is None or index >= len(values):
        return None
    return values[index]


def _prediction_weather(weather):
    return {
        key: weather.get(key)
        for key in PREDICTION_WEATHER_FIELDS
        if key in weather
    }


def calculate_confidence(ensemble_members, baseline_weather=None, flight_number=None):
    return evaluate_ensemble_confidence(
        ensemble_members,
        baseline_weather=baseline_weather,
        flight_number=flight_number,
        predictor=predict_flight_probability,
        prediction_fields=PREDICTION_WEATHER_FIELDS,
    )


def calculate_model_reference_probabilities(ensemble_members, baseline_weather=None, flight_number=None):
    baseline_weather = baseline_weather or {}
    probabilities = {}
    for member in ensemble_members:
        model = member.get("_model")
        if not model:
            continue
        weather = {key: value for key, value in member.items() if key != "_model"}
        result = predict_flight_probability(
            **_prediction_weather({**baseline_weather, **weather}),
            flight_number=flight_number,
        )
        if result.get("calculation_status", "available") != "available":
            continue
        probability = result.get("probability")
        if probability is not None:
            probabilities.setdefault(model, []).append(probability)
    return {
        model: round(median(values), 1)
        for model, values in probabilities.items()
        if values
    }


RISK_LABELS = (
    "南風注意",
    "視程不良リスク",
    "降水注意",
    "低層雲の影響注意",
    "突風注意",
    "強風注意",
    "台風接近リスク",
)


def _risk_labels(warning_msg):
    if not warning_msg or warning_msg in {"なし", "特になし"}:
        return []
    labels = []
    for warning in str(warning_msg).split("、"):
        for label in RISK_LABELS:
            if warning.startswith(label):
                labels.append(label)
                break
    return labels


def _format_risk_summary(counts, total):
    if not counts:
        return "特になし"
    return "、".join(
        f"{label} ({counts[label]}/{total}通り)"
        for label in RISK_LABELS
        if counts.get(label)
    )


def calculate_model_reference_risks(ensemble_members, baseline_weather=None, flight_number=None):
    baseline_weather = baseline_weather or {}
    risk_counts = {}
    totals = Counter()
    for member in ensemble_members:
        model = member.get("_model")
        if not model:
            continue
        weather = {key: value for key, value in member.items() if key != "_model"}
        result = predict_flight_probability(
            **_prediction_weather({**baseline_weather, **weather}),
            flight_number=flight_number,
        )
        totals[model] += 1
        risk_counts.setdefault(model, Counter()).update(_risk_labels(result.get("warning_msg")))

    return {
        model: _format_risk_summary(risk_counts.get(model, Counter()), total)
        for model, total in totals.items()
        if total
    }


def deterministic_risk_summary(result):
    labels = set(_risk_labels(result.get("warning_msg")))
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
            confidence = calculate_confidence(
                ensembles_by_time.get(timestamp, []),
                weather,
                flight_number=flight["number"],
            )
            result = _with_typhoon_impact(result, typhoon_impact)
            model_probabilities = calculate_model_reference_probabilities(
                ensembles_by_time.get(timestamp, []),
                weather,
                flight_number=flight["number"],
            )
            model_probabilities = {
                model: _with_typhoon_probability_adjustment(probability, typhoon_impact)
                for model, probability in model_probabilities.items()
            }
            model_risks = calculate_model_reference_risks(
                ensembles_by_time.get(timestamp, []),
                weather,
                flight_number=flight["number"],
            )
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

