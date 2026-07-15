from app_config import (
    FALLBACK_MATCH_ANGLE_DEGREES,
    FALLBACK_MATCH_WIND_SPEED_MS,
    GUST_RISK_MS,
    INITIAL_MATCH_ANGLE_DEGREES,
    INITIAL_MATCH_WIND_SPEED_MS,
    LOW_CLOUD_PROBABILITY_MULTIPLIER,
    LOW_CLOUD_RISK_PERCENT,
    MAX_PROBABILITY,
    MIN_MATCHING_HISTORY_ROWS,
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
    SOUTHERLY_WIND_MAX_DEGREES,
    SOUTHERLY_WIND_MIN_DEGREES,
    STRONG_WIND_RISK_MS,
    VISIBILITY_PROBABILITY_MULTIPLIER,
    VISIBILITY_RISK_KM,
    WIND_PROBABILITY_MULTIPLIER,
)
from bigquery_storage import fetch_detailed_history, fetch_history
from flight_metadata import OPERATED_STATUSES, VALID_STORED_STATUSES, normalize_status


def load_history():
    return fetch_history()


def load_detailed_history():
    return fetch_detailed_history()


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


def find_similar_flights(flight_number, weather, limit=10):
    candidates = []
    for row in load_detailed_history():
        if row["flight_number"] != flight_number:
            continue
        score = _weather_similarity_score(row, weather)
        candidates.append((score, row))

    similar = []
    for score, row in sorted(candidates, key=lambda item: item[0])[:limit]:
        similar.append(
            {
                **row,
                "date_label": row["date"].replace("-", "/"),
                "similarity_score": round(score, 2),
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
        dict: 予測結果 (probability, alert_required, warning_msg, data_count, step_used)
    """
    history = []
    for row in load_history():
        if len(row) == 4:
            historical_flight, status, historical_direction, historical_speed = row
        else:
            historical_flight = None
            status, historical_direction, historical_speed = row
        normalized_status = normalize_status(status)
        if normalized_status not in VALID_STORED_STATUSES:
            continue
        if flight_number is not None and historical_flight != flight_number:
            continue
        history.append((normalized_status, historical_direction, historical_speed))
    if not history:
        scope = f"{flight_number}の" if flight_number else ""
        return {
            "probability": MAX_PROBABILITY,
            "alert_required": False,
            "warning_msg": f"{scope}過去データを取得できないため、デフォルト値を返します。",
            "data_count": 0,
            "step_used": 0,
            "history_flight_number": flight_number,
        }
        
    matching_rows = []
    step_used = 1

    def matches(angle_limit, speed_limit):
        result = []
        for status, historical_direction, historical_speed in history:
            angle_diff = abs(historical_direction - wind_direction)
            angle_diff = min(angle_diff, 360 - angle_diff)
            if angle_diff <= angle_limit and abs(historical_speed - wind_speed) <= speed_limit:
                result.append((status,))
        return result

    matching_rows = matches(INITIAL_MATCH_ANGLE_DEGREES, INITIAL_MATCH_WIND_SPEED_MS)
    if len(matching_rows) < MIN_MATCHING_HISTORY_ROWS:
        step_used = 2
        matching_rows = matches(FALLBACK_MATCH_ANGLE_DEGREES, FALLBACK_MATCH_WIND_SPEED_MS)
    if len(matching_rows) < MIN_MATCHING_HISTORY_ROWS:
        step_used = 3
        matching_rows = [(status,) for status, _, _ in history]
        
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
    warnings = []
    alert_required = False

    if (
        SOUTHERLY_WIND_MIN_DEGREES <= wind_direction <= SOUTHERLY_WIND_MAX_DEGREES
        and wind_speed >= SOUTHERLY_CAUTION_WIND_MS
    ):
        warnings.append("南風注意")
        alert_required = True
    
    # 2. 霧・低層雲量による減算補正
    if visibility is not None and visibility < VISIBILITY_RISK_KM:
        if visibility < SEVERE_VISIBILITY_RISK_KM:
            prob *= SEVERE_VISIBILITY_PROBABILITY_MULTIPLIER
        else:
            prob *= VISIBILITY_PROBABILITY_MULTIPLIER
        warnings.append(f"視程不良リスク ({visibility} km)")

    if precipitation is not None and precipitation >= PRECIPITATION_RISK_MM:
        if precipitation >= SEVERE_PRECIPITATION_RISK_MM:
            prob *= SEVERE_PRECIPITATION_PROBABILITY_MULTIPLIER
        else:
            prob *= PRECIPITATION_PROBABILITY_MULTIPLIER
        warnings.append(f"降水注意 (予報降水量: {precipitation} mm/h)")

    if cloud_cover_low is not None and cloud_cover_low > LOW_CLOUD_RISK_PERCENT:
        if cloud_cover_low >= SEVERE_LOW_CLOUD_RISK_PERCENT:
            prob *= SEVERE_LOW_CLOUD_PROBABILITY_MULTIPLIER
        else:
            prob *= LOW_CLOUD_PROBABILITY_MULTIPLIER
        warnings.append(f"低層雲の影響注意 (低層雲量 {cloud_cover_low}%)")
        
    # 3. 台風・強風による補正
    is_windy = False
    if wind_gusts is not None and wind_gusts >= GUST_RISK_MS:
        if wind_gusts >= SEVERE_GUST_RISK_MS:
            prob *= SEVERE_GUST_PROBABILITY_MULTIPLIER
        else:
            prob *= WIND_PROBABILITY_MULTIPLIER
        is_windy = True
        warnings.append(f"突風注意 (予報突風: {wind_gusts} m/s)")
    elif wind_speed is not None and wind_speed >= STRONG_WIND_RISK_MS:
        prob *= WIND_PROBABILITY_MULTIPLIER
        is_windy = True
        warnings.append(f"強風注意 (予報風速: {wind_speed} m/s)")
        
    if is_windy:
        alert_required = True
        
    # 4. 上限キャップと下限の設定
    final_prob = min(prob, MAX_PROBABILITY)
    final_prob = max(final_prob, 0.0)
    
    warning_msg = "、".join(warnings) if warnings else "特になし"
    
    return {
        "probability": round(final_prob, 1),
        "alert_required": alert_required,
        "warning_msg": warning_msg,
        "data_count": len(matching_rows),
        "step_used": step_used,
        "history_flight_number": flight_number,
    }

