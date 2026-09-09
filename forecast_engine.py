import hashlib
import json

from app_config import (
    EXTREME_VISIBILITY_PROBABILITY_MULTIPLIER,
    EXTREME_VISIBILITY_RISK_KM,
    GUST_RISK_MS,
    LOW_CLOUD_PROBABILITY_MULTIPLIER,
    LOW_CLOUD_RISK_PERCENT,
    MAX_PROBABILITY,
    MODERATE_VISIBILITY_PROBABILITY_MULTIPLIER,
    MODERATE_VISIBILITY_RISK_KM,
    PRECIPITATION_PROBABILITY_MULTIPLIER,
    PRECIPITATION_RISK_MM,
    SEVERE_GUST_PROBABILITY_MULTIPLIER,
    SEVERE_GUST_RISK_MS,
    SEVERE_LOW_CLOUD_PROBABILITY_MULTIPLIER,
    SEVERE_LOW_CLOUD_RISK_PERCENT,
    SEVERE_PRECIPITATION_PROBABILITY_MULTIPLIER,
    SEVERE_PRECIPITATION_RISK_MM,
    SEVERE_VISIBILITY_PROBABILITY_MULTIPLIER,
    SEVERE_VISIBILITY_RISK_KM,
    SOUTHERLY_CAUTION_WIND_MS,
    SOUTHERLY_HIGH_WIND_MS,
    SOUTHERLY_MEDIUM_WIND_MS,
    SOUTHERLY_MULTIPLIERS,
    SOUTHERLY_UPGRADE_MAX_DEGREES,
    SOUTHERLY_WIND_MAX_DEGREES,
    SOUTHERLY_WIND_MIN_DEGREES,
    STRONG_WIND_RISK_MS,
    VISIBILITY_PROBABILITY_MULTIPLIER,
    VISIBILITY_RISK_KM,
    WIND_PROBABILITY_MULTIPLIER,
)
from bigquery_storage import fetch_detailed_history
from flight_metadata import OPERATED_STATUSES
from history_selection import select_history, select_history_with_metadata, valid_wind


def load_history():
    return fetch_detailed_history()


def load_detailed_history():
    return fetch_detailed_history()


def _history_fingerprint(history):
    payload = json.dumps(
        sorted(history, key=lambda row: json.dumps(row, sort_keys=True, default=str)),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _weather_similarity_score(row, weather):
    """Return a weather distance, emphasizing the forecast's adverse conditions."""
    angle_diff = abs(row["wind_direction"] - weather["wind_direction"])
    angle_diff = min(angle_diff, 360 - angle_diff)

    is_strong_wind = weather.get("wind_speed", 0) >= STRONG_WIND_RISK_MS
    is_strong_gust = weather.get("wind_gusts") is not None and weather["wind_gusts"] >= GUST_RISK_MS
    is_cloudy = weather.get("cloud_cover_low") is not None and weather["cloud_cover_low"] >= 70
    is_low_visibility = weather.get("visibility") is not None and weather["visibility"] <= 10

    components = [
        (angle_diff / 45, 2.5 if is_strong_wind or is_strong_gust else 1.5),
        (abs(row["wind_speed"] - weather["wind_speed"]) / 5, 3.0 if is_strong_wind else 1.0),
    ]
    optional_fields = (
        ("wind_gusts", 7.5, 3.0 if is_strong_gust else 0.75),
        ("cloud_cover_low", 25, 3.0 if is_cloudy else 0.75),
        ("visibility", 5, 4.0 if is_low_visibility else 0.75),
    )
    missing_penalty = 0.0
    for field, scale, weight in optional_fields:
        if row.get(field) is None or weather.get(field) is None:
            if weather.get(field) is not None:
                missing_penalty += weight
            continue
        components.append((abs(row[field] - weather[field]) / scale, weight))

    score = sum(distance * weight for distance, weight in components) / sum(
        weight for _, weight in components
    )

    # Keep records on the same side of operationally meaningful thresholds.
    threshold_checks = (
        (is_strong_wind, row["wind_speed"] >= STRONG_WIND_RISK_MS, 2.0),
        (is_strong_gust, row.get("wind_gusts") is not None and row["wind_gusts"] >= GUST_RISK_MS, 2.0),
        (is_cloudy, row.get("cloud_cover_low") is not None and row["cloud_cover_low"] >= 70, 2.0),
        (is_low_visibility, row.get("visibility") is not None and row["visibility"] <= 10, 3.0),
    )
    mismatch_penalty = sum(penalty for active, matches, penalty in threshold_checks if active and not matches)
    return score + missing_penalty + mismatch_penalty


def find_similar_flights(flight_number, weather, limit=10, history=None):
    history_rows = load_detailed_history() if history is None else history
    similar = []
    for row in select_history(history_rows, flight_number, weather)[:limit]:
        similar.append(
            {
                **row,
                "date_label": str(row.get("date", "")).replace("-", "/"),
                "similarity_score": row["wind_differences"][0],
            }
        )
    return similar

def predict_flight_probability(
    wind_direction,
    wind_speed,
    wind_gusts,
    cloud_cover_low,
    visibility,
    precipitation=None,
    flight_number=None,
    history=None,
):
    """
    入力された気象条件から、八丈島便の運航確率を予測する。
    
    Args:
        wind_direction (float): 風向 (0 - 360 度)
        wind_speed (float): 風速 (m/s)
        wind_gusts (float): 突風 (m/s)
        cloud_cover_low (float): 低層雲量 (%)
        visibility (float): 視程 (km)
        precipitation (float): 降水量 (mm/h)
        
    Returns:
        dict: 予測結果。履歴不足時は`probability`を`None`にする。
    """
    source_history = load_history() if history is None else history
    weather = {"wind_direction": wind_direction, "wind_speed": wind_speed, "wind_gusts": wind_gusts}
    history_rows, selection = select_history_with_metadata(source_history, flight_number, weather)
    history_fingerprint = _history_fingerprint(history_rows)
    if len(history_rows) < selection["minimum"]:
        scope = f"{flight_number}の" if flight_number else ""
        reason_code = "similar_history_below_minimum" if valid_wind(weather) else "required_wind_missing_or_invalid"
        return {
            "probability": None,
            "base_probability": None,
            "weather_factor": None,
            "weather_factors": {},
            "calculation_status": "insufficient_history" if valid_wind(weather) else "weather_missing",
            "reason_code": reason_code,
            "alert_required": False,
            "warning_msg": (f"{scope}条件に合う過去実績が{len(history_rows)}件のため、算出できません。"
                            if valid_wind(weather) else "風向・平均風速・最大瞬間風速が欠測または不正のため算出できません。"),
            "data_count": len(history_rows),
            "step_used": 0,
            "history_selection": selection,
            "history_flight_number": flight_number,
            "history_fingerprint": history_fingerprint,
        }
        
    matching_rows = [(row["status"],) for row in history_rows]
    step_used = selection["step"]
        
    # ベース確率の算出
    if not matching_rows:
        base_prob = MAX_PROBABILITY
    else:
        # 重み付け: 運航した便=1.0、欠航・引返欠航=0.0
        total = len(matching_rows)
        score_sum = 0.0
        for (status,) in matching_rows:
            if status in OPERATED_STATUSES:
                score_sum += 1.0
            else:
                score_sum += 0.0
                
        base_prob = (score_sum / total) * 100.0
        
    prob = base_prob
    weather_factor = 1.0
    weather_factors = {}
    warnings = []
    alert_required = False
    wind_factors = {}

    if (
        SOUTHERLY_WIND_MIN_DEGREES <= wind_direction <= SOUTHERLY_WIND_MAX_DEGREES
        and wind_speed >= SOUTHERLY_CAUTION_WIND_MS
    ):
        level = 2 if wind_speed >= SOUTHERLY_HIGH_WIND_MS else (
            1 if wind_speed >= SOUTHERLY_MEDIUM_WIND_MS else 0
        )
        if wind_direction <= SOUTHERLY_UPGRADE_MAX_DEGREES:
            level += 1
        wind_factors["southerly"] = SOUTHERLY_MULTIPLIERS[level]
        warnings.append(f"南風リスク{('小', '中', '大', '特大')[level]}")
        alert_required = True
    
    # 2. 霧・低層雲量による減算補正
    if visibility is not None and visibility < VISIBILITY_RISK_KM:
        if visibility < EXTREME_VISIBILITY_RISK_KM:
            factor = EXTREME_VISIBILITY_PROBABILITY_MULTIPLIER
            label = "特大"
        elif visibility < SEVERE_VISIBILITY_RISK_KM:
            factor = SEVERE_VISIBILITY_PROBABILITY_MULTIPLIER
            label = "大"
        elif visibility < MODERATE_VISIBILITY_RISK_KM:
            factor = MODERATE_VISIBILITY_PROBABILITY_MULTIPLIER
            label = "中"
        else:
            factor = VISIBILITY_PROBABILITY_MULTIPLIER
            label = "小"
        prob *= factor
        weather_factor *= factor
        weather_factors["visibility"] = factor
        warnings.append(f"視程不良リスク{label}（{visibility:g}km）")

    if precipitation is not None and precipitation >= PRECIPITATION_RISK_MM:
        if precipitation >= SEVERE_PRECIPITATION_RISK_MM:
            factor = SEVERE_PRECIPITATION_PROBABILITY_MULTIPLIER
        else:
            factor = PRECIPITATION_PROBABILITY_MULTIPLIER
        prob *= factor
        weather_factor *= factor
        weather_factors["precipitation"] = factor
        warnings.append(f"降水注意 (予報降水量: {precipitation} mm/h)")

    if cloud_cover_low is not None and cloud_cover_low > LOW_CLOUD_RISK_PERCENT:
        if cloud_cover_low >= SEVERE_LOW_CLOUD_RISK_PERCENT:
            factor = SEVERE_LOW_CLOUD_PROBABILITY_MULTIPLIER
        else:
            factor = LOW_CLOUD_PROBABILITY_MULTIPLIER
        prob *= factor
        weather_factor *= factor
        weather_factors["low_cloud"] = factor
        warnings.append(f"低層雲の影響注意 (低層雲量 {cloud_cover_low}%)")
        
    # 3. 台風・強風による補正
    is_windy = False
    if wind_gusts is not None and wind_gusts >= GUST_RISK_MS:
        if wind_gusts >= SEVERE_GUST_RISK_MS:
            factor = SEVERE_GUST_PROBABILITY_MULTIPLIER
        else:
            factor = WIND_PROBABILITY_MULTIPLIER
        wind_factors["gust"] = factor
        is_windy = True
        warnings.append(f"突風注意 (予報突風: {wind_gusts} m/s)")
    elif wind_speed is not None and wind_speed >= STRONG_WIND_RISK_MS:
        factor = WIND_PROBABILITY_MULTIPLIER
        wind_factors["wind"] = factor
        is_windy = True
        warnings.append(f"強風注意 (予報風速: {wind_speed} m/s)")
        
    if is_windy:
        alert_required = True

    # Wind hazards overlap: apply only the strongest reduction once.
    if wind_factors:
        key = min(wind_factors, key=wind_factors.get)
        factor = wind_factors[key]
        prob *= factor
        weather_factor *= factor
        weather_factors[key] = factor
        
    # 4. 上限キャップと下限の設定
    final_prob = min(prob, MAX_PROBABILITY)
    final_prob = max(final_prob, 0.0)
    
    warning_msg = "、".join(warnings) if warnings else "特になし"
    
    return {
        "probability": round(final_prob, 1),
        "base_probability": round(base_prob, 1),
        "weather_factor": round(weather_factor, 6),
        "weather_factors": weather_factors,
        "calculation_status": "available",
        "reason_code": None,
        "alert_required": alert_required,
        "warning_msg": warning_msg,
        "data_count": len(matching_rows),
        "step_used": step_used,
        "history_flight_number": flight_number,
        "history_selection": selection,
        "history_fingerprint": history_fingerprint,
    }

