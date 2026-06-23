from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

import requests
from flask import Flask, render_template

from app_config import (
    CONFIDENCE_GRADES,
    ENSEMBLE_FORECAST_URL,
    FLIGHTS,
    FORECAST_DAYS,
    HACHIJO_AIRPORT_LATITUDE,
    HACHIJO_AIRPORT_LONGITUDE,
    JMA_MODEL_NAME,
    JST,
    LOW_PROBABILITY_THRESHOLD,
    MAIN_FORECAST_URL,
    MODEL_DIFFERENCE_WARNING_POINTS,
)
from db_snapshot import restore_db
from forecast_cache import load_cached_forecast_bundle, save_forecast_bundle
from flight_metadata import flight_display_name
from forecast_engine import find_similar_flights, predict_flight_probability
from presentation import decorate_flight_for_display


BASE_DIR = Path(__file__).resolve().parent


def _fetch_deterministic_forecast(model=None):
    params = {
        "latitude": HACHIJO_AIRPORT_LATITUDE,
        "longitude": HACHIJO_AIRPORT_LONGITUDE,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover_low,visibility",
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
        "wind_gusts_10m",
        "cloud_cover_low",
        "visibility",
    }
    if not times or any(len(hourly.get(key, [])) != len(times) for key in required):
        raise ValueError("気象データの構造が正しくありません。")

    weather_by_time = {}
    for index, timestamp in enumerate(times):
        weather_by_time[timestamp] = {
            "wind_speed": hourly["wind_speed_10m"][index],
            "wind_direction": hourly["wind_direction_10m"][index],
            "wind_gusts": hourly["wind_gusts_10m"][index],
            "cloud_cover_low": hourly["cloud_cover_low"][index],
            "visibility": _meters_to_km(hourly["visibility"][index]),
        }
    return weather_by_time


def fetch_forecast():
    return _fetch_deterministic_forecast()


def fetch_jma_forecast():
    return _fetch_deterministic_forecast(JMA_MODEL_NAME)


def fetch_ensemble_forecast():
    model_variables = (
        (
            "ecmwf_ifs025",
            ("wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "cloud_cover_low"),
            31,
        ),
        (
            "gfs_seamless",
            ("wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "visibility"),
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


def _prepare_reference_weather(candidate, fallback):
    if candidate is None:
        return None
    prepared = dict(candidate)
    for field in ("wind_direction", "wind_speed"):
        if prepared.get(field) is None:
            prepared[field] = fallback.get(field)
    if prepared.get("wind_direction") is None or prepared.get("wind_speed") is None:
        return None
    return prepared


def calculate_confidence(ensemble_members, baseline_weather=None):
    baseline_weather = baseline_weather or {}
    probabilities = sorted(
        predict_flight_probability(**{**baseline_weather, **{key: value for key, value in weather.items() if key != "_model"}})["probability"]
        for weather in ensemble_members
    )
    if len(probabilities) < 10:
        return None

    low = probabilities[round((len(probabilities) - 1) * 0.1)]
    high = probabilities[round((len(probabilities) - 1) * 0.9)]
    spread = round(high - low, 1)
    for threshold, grade, label in CONFIDENCE_GRADES:
        if spread <= threshold:
            break
    else:
        grade, label = "E", "40ポイント超"
    return {
        "grade": grade,
        "label": label,
        "spread": spread,
        "member_count": len(probabilities),
        "source": "ensemble",
    }


def calculate_model_reference_probabilities(ensemble_members, baseline_weather=None):
    baseline_weather = baseline_weather or {}
    probabilities = {}
    for member in ensemble_members:
        model = member.get("_model")
        if not model:
            continue
        weather = {key: value for key, value in member.items() if key != "_model"}
        probability = predict_flight_probability(**{**baseline_weather, **weather})["probability"]
        probabilities.setdefault(model, []).append(probability)
    return {
        model: round(median(values), 1)
        for model, values in probabilities.items()
        if values
    }


RISK_LABELS = (
    "南風注意",
    "視程不良リスク",
    "低層雲の影響注意",
    "突風注意",
    "強風注意",
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


def calculate_model_reference_risks(ensemble_members, baseline_weather=None):
    baseline_weather = baseline_weather or {}
    risk_counts = {}
    totals = Counter()
    for member in ensemble_members:
        model = member.get("_model")
        if not model:
            continue
        weather = {key: value for key, value in member.items() if key != "_model"}
        result = predict_flight_probability(**{**baseline_weather, **weather})
        totals[model] += 1
        risk_counts.setdefault(model, Counter()).update(_risk_labels(result.get("warning_msg")))

    return {
        model: _format_risk_summary(risk_counts.get(model, Counter()), total)
        for model, total in totals.items()
        if total
    }


def deterministic_risk_summary(result):
    labels = _risk_labels(result.get("warning_msg"))
    if not labels:
        return "特になし"
    counts = Counter(labels)
    return "、".join(label for label in RISK_LABELS if counts.get(label))


def fallback_confidence(target_date, reference_date):
    lead_days = max((target_date - reference_date).days, 0)
    if lead_days == 0:
        grade, label = "A", "10ポイント以内"
    elif lead_days == 1:
        grade, label = "B", "20ポイント以内"
    elif lead_days <= 3:
        grade, label = "C", "30ポイント以内"
    elif lead_days <= 5:
        grade, label = "D", "40ポイント以内"
    else:
        grade, label = "E", "40ポイント超"
    return {
        "grade": grade,
        "label": label,
        "lead_days": lead_days,
        "source": "lead_time",
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


def _with_model_difference_warning(result, jma_probability):
    result = dict(result)
    if jma_probability is None:
        return result
    difference = round(abs(result["probability"] - jma_probability), 1)
    result["model_difference"] = difference
    if difference >= MODEL_DIFFERENCE_WARNING_POINTS:
        warning = "気象モデル差に注意"
        current = result.get("warning_msg")
        result["warning_msg"] = warning if current in {None, "なし", "特になし"} else f"{current}、{warning}"
        result["alert_required"] = True
    return result


def _log_or_print(logger, message, exc):
    if logger is None:
        return
    if hasattr(logger, "warning"):
        logger.warning("%s: %s", message, exc)
    else:
        logger(f"{message}: {exc}")


def load_forecast_bundle(logger=None):
    cached = load_cached_forecast_bundle()
    notices = []
    try:
        weather = fetch_forecast()
    except (requests.RequestException, ValueError) as exc:
        if cached:
            _log_or_print(logger, "Main forecast unavailable; using cached forecast", exc)
            notices.append("予報APIに接続できないため、前回取得した予報データを表示しています。")
            return {
                "weather": cached["weather"],
                "jma": cached.get("jma", {}),
                "ensembles": cached.get("ensembles", {}),
                "notices": notices,
                "source": "cache",
            }
        raise

    try:
        jma = fetch_jma_forecast()
    except (requests.RequestException, ValueError) as exc:
        _log_or_print(logger, "JMA forecast could not be loaded", exc)
        jma = cached.get("jma", {}) if cached else {}
        if jma:
            notices.append("JMA予報は前回取得データを使用しています。")

    try:
        ensembles = fetch_ensemble_forecast()
    except (requests.RequestException, ValueError) as exc:
        _log_or_print(logger, "Ensemble forecast could not be loaded", exc)
        ensembles = cached.get("ensembles", {}) if cached else {}
        if ensembles:
            notices.append("アンサンブル予報は前回取得データを使用しています。")

    save_forecast_bundle(weather, jma, ensembles)
    return {
        "weather": weather,
        "jma": jma,
        "ensembles": ensembles,
        "notices": notices,
        "source": "live",
    }


def build_daily_forecasts(weather_by_time, ensembles_by_time=None, reference_date=None, current_time=None, jma_by_time=None):
    ensembles_by_time = ensembles_by_time or {}
    jma_by_time = jma_by_time or {}
    current_time = current_time or datetime.now(JST)
    reference_date = reference_date or current_time.date()
    dates = sorted({timestamp[:10] for timestamp in weather_by_time})
    days = []
    for date_string in dates:
        date = datetime.strptime(date_string, "%Y-%m-%d")
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
            result = predict_flight_probability(**weather)
            jma_weather = _prepare_reference_weather(jma_by_time.get(timestamp), weather)
            jma_result = predict_flight_probability(**jma_weather) if jma_weather is not None else None
            jma_probability = jma_result["probability"] if jma_result is not None else None
            result = _with_model_difference_warning(result, jma_probability)
            confidence = calculate_confidence(ensembles_by_time.get(timestamp, []), weather)
            model_probabilities = calculate_model_reference_probabilities(
                ensembles_by_time.get(timestamp, []), weather
            )
            model_risks = calculate_model_reference_risks(
                ensembles_by_time.get(timestamp, []), weather
            )
            flights.append(
                decorate_flight_for_display(
                    {
                        **flight,
                        **weather,
                        **result,
                        "number": flight_display_name(flight["number"]),
                        "raw_number": flight["number"],
                        "similar_history": find_similar_flights(flight["number"], weather),
                        "jma_weather": jma_weather,
                        "jma_probability": jma_probability,
                        "jma_risk": deterministic_risk_summary(jma_result) if jma_result is not None else None,
                        "gfs_probability": model_probabilities.get("gfs_seamless"),
                        "gfs_risk": model_risks.get("gfs_seamless"),
                        "ecmwf_probability": model_probabilities.get("ecmwf_ifs025"),
                        "ecmwf_risk": model_risks.get("ecmwf_ifs025"),
                        "confidence": confidence,
                        "wind_direction_label": wind_direction_label(weather["wind_direction"]),
                    }
                )
            )
        if flights:
            confidence_values = [flight["confidence"] for flight in flights if flight["confidence"]]
            if confidence_values:
                day_confidence = max(
                    confidence_values,
                    key=lambda confidence: "ABCDE".index(confidence["grade"]),
                )
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
        try:
            restore_db(BASE_DIR / "flights.db", BASE_DIR / "data" / "flights_dump.sql")
            bundle = load_forecast_bundle(app.logger)
            days = build_daily_forecasts(
                bundle["weather"],
                bundle["ensembles"],
                jma_by_time=bundle["jma"],
            )
            notices = bundle["notices"]
        except (requests.RequestException, ValueError, OSError) as exc:
            app.logger.warning("Forecast could not be loaded: %s", exc)
            error = "現在、予報を取得できません。時間をおいてもう一度お試しください。"
            notices = []

        updated_at = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
        return render_template(
            "index.html",
            days=days,
            error=error,
            updated_at=updated_at,
            notices=notices,
            low_probability_threshold=LOW_PROBABILITY_THRESHOLD,
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)

