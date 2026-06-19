import sqlite3
import os
from functools import lru_cache
from pathlib import Path

from bigquery_storage import fetch_detailed_history, fetch_history
from flight_metadata import flight_display_name, normalize_status

DB_FILE = Path(__file__).resolve().parent / "flights.db"


def load_history():
    backend = os.getenv("FORECAST_DATA_BACKEND", "sqlite").lower()
    if backend == "bigquery":
        return fetch_history()

    if not DB_FILE.exists():
        return []
    conn = sqlite3.connect(DB_FILE)
    try:
        return conn.execute(
            """
            SELECT status, wind_direction, wind_speed
            FROM flight_weather_logs
            WHERE status IS NOT NULL
              AND wind_direction IS NOT NULL
              AND wind_speed IS NOT NULL
            """
        ).fetchall()
    finally:
        conn.close()


@lru_cache(maxsize=1)
def load_detailed_history():
    backend = os.getenv("FORECAST_DATA_BACKEND", "sqlite").lower()
    if backend == "bigquery":
        return fetch_detailed_history()
    if not DB_FILE.exists():
        return []

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(flight_weather_logs)")}
        reason_column = "status_reason" if "status_reason" in columns else "NULL AS status_reason"
        rows = conn.execute(
            f"""
            SELECT date, flight_number, status, {reason_column}, wind_direction,
                   wind_speed, wind_gusts, cloud_cover_low, visibility
            FROM flight_weather_logs
            WHERE status IS NOT NULL AND wind_direction IS NOT NULL AND wind_speed IS NOT NULL
            """
        ).fetchall()
        return [
            {
                **dict(row),
                "status": normalize_status(row["status"]),
                "flight_display_name": flight_display_name(row["flight_number"]),
            }
            for row in rows
        ]
    finally:
        conn.close()


def _weather_similarity_score(row, weather):
    """Return a weather distance, emphasizing the forecast's adverse conditions."""
    angle_diff = abs(row["wind_direction"] - weather["wind_direction"])
    angle_diff = min(angle_diff, 360 - angle_diff)

    is_strong_wind = weather.get("wind_speed", 0) >= 10
    is_strong_gust = weather.get("wind_gusts") is not None and weather["wind_gusts"] >= 15
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
        (is_strong_wind, row["wind_speed"] >= 10, 2.0),
        (is_strong_gust, row.get("wind_gusts") is not None and row["wind_gusts"] >= 15, 2.0),
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

def predict_flight_probability(wind_direction, wind_speed, wind_gusts, cloud_cover_low, visibility):
    """
    入力された気象条件から、八丈島便の就航確率を予測する。
    
    Args:
        wind_direction (float): 風向 (0 - 360 度)
        wind_speed (float): 風速 (m/s)
        wind_gusts (float): 突風 (m/s)
        cloud_cover_low (float): 低層雲量 (%)
        visibility (float): 視程 (km)
        
    Returns:
        dict: 予測結果 (probability, alert_required, warning_msg, data_count, step_used)
    """
    history = load_history()
    if not history:
        return {
            "probability": 95.0,
            "alert_required": False,
            "warning_msg": "過去データを取得できないため、デフォルト値を返します。",
            "data_count": 0,
            "step_used": 0
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

    matching_rows = matches(30.0, 3.0)
    if len(matching_rows) < 5:
        step_used = 2
        matching_rows = matches(45.0, 5.0)
    if len(matching_rows) < 5:
        step_used = 3
        matching_rows = [(status,) for status, _, _ in history]
        
    # ベース確率の算出
    if not matching_rows:
        base_prob = 95.0
    else:
        # 重み付け: 就航した便=1.0、欠航・引返欠航=0.0
        total = len(matching_rows)
        score_sum = 0.0
        for (status,) in matching_rows:
            if normalize_status(status) in ["通常", "遅延", "条件付き→就航"]:
                score_sum += 1.0
            else:
                score_sum += 0.0
                
        base_prob = (score_sum / total) * 100.0
        
    prob = base_prob
    warnings = []
    alert_required = False
    
    # 2. 霧・低層雲量による減算補正
    if visibility is not None and visibility < 5.0:
        prob *= 0.6
        warnings.append(f"視程不良リスク ({visibility} km)")
    
    if cloud_cover_low is not None and cloud_cover_low > 90.0:
        prob *= 0.8
        warnings.append(f"低層雲の影響注意 (低層雲量 {cloud_cover_low}%)")
        
    # 3. 台風・強風による補正
    is_windy = False
    if wind_gusts is not None and wind_gusts >= 15.0:
        prob *= 0.7
        is_windy = True
        warnings.append(f"突風注意 (予報突風: {wind_gusts} m/s)")
    elif wind_speed is not None and wind_speed >= 10.0:
        prob *= 0.7
        is_windy = True
        warnings.append(f"強風注意 (予報風速: {wind_speed} m/s)")
        
    if is_windy:
        alert_required = True
        
    # 4. 上限キャップと下限の設定
    final_prob = min(prob, 95.0)
    final_prob = max(final_prob, 0.0)
    
    warning_msg = "、".join(warnings) if warnings else "特になし"
    
    return {
        "probability": round(final_prob, 1),
        "alert_required": alert_required,
        "warning_msg": warning_msg,
        "data_count": len(matching_rows),
        "step_used": step_used
    }

