import sqlite3
import os
from pathlib import Path

from bigquery_storage import fetch_history

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
        # 重み付け: 通常・遅延=1.0, 条件付き運航=0.75, 欠航・引き返し=0.0
        total = len(matching_rows)
        score_sum = 0.0
        for (status,) in matching_rows:
            if status in ["通常", "遅延"]:
                score_sum += 1.0
            elif status == "条件付き運航":
                score_sum += 0.75
            else:
                score_sum += 0.0
                
        base_prob = (score_sum / total) * 100.0
        
    prob = base_prob
    warnings = []
    alert_required = False
    
    # 2. 霧・雲量による減算補正
    if visibility is not None and visibility < 5.0:
        prob *= 0.6
        warnings.append(f"視程不良リスク ({visibility} km)")
    
    if cloud_cover_low is not None and cloud_cover_low > 90.0:
        prob *= 0.8
        warnings.append(f"低い雲の影響あり (低層雲量 {cloud_cover_low}%)")
        
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
