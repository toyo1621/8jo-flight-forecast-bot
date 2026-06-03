import sqlite3
import os

DB_FILE = "flights.db"

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
    if not os.path.exists(DB_FILE):
        return {
            "probability": 95.0,
            "alert_required": False,
            "warning_msg": "データベースが存在しないため、デフォルト値を返します。",
            "data_count": 0,
            "step_used": 0
        }
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    matching_rows = []
    step_used = 1
    
    # 段階的な検索条件緩和
    # SQLiteで風向の角度差 (環状の差) を計算するためのSQL CASE文
    # ABS(angle1 - angle2) が 180 を超える場合は 360 - ABS(angle1 - angle2) を最小の角度差とする
    angle_diff_sql = """
        CASE 
            WHEN ABS(wind_direction - ?) > 180 THEN 360 - ABS(wind_direction - ?) 
            ELSE ABS(wind_direction - ?) 
        END
    """
    
    # ステップ1: 風向差 <= 30度、風速差 <= 3.0 m/s
    query = f"""
        SELECT status FROM flight_weather_logs
        WHERE ({angle_diff_sql}) <= ?
        AND ABS(wind_speed - ?) <= ?
    """
    
    try:
        cursor.execute(query, (wind_direction, wind_direction, wind_direction, 30.0, wind_speed, 3.0))
        matching_rows = cursor.fetchall()
        
        # データ数が少ない場合はステップ2に緩和 (風向差 <= 45度、風速差 <= 5.0 m/s)
        if len(matching_rows) < 5:
            step_used = 2
            cursor.execute(query, (wind_direction, wind_direction, wind_direction, 45.0, wind_speed, 5.0))
            matching_rows = cursor.fetchall()
            
        # それでもデータ数が少ない場合はステップ3 (全データ)
        if len(matching_rows) < 5:
            step_used = 3
            cursor.execute("SELECT status FROM flight_weather_logs")
            matching_rows = cursor.fetchall()
            
    except sqlite3.OperationalError as e:
        print(f"データベースクエリエラー: {e}")
        # visibilityカラムがまだ存在しない古いDBなどの場合のフォールバック
        cursor.execute("SELECT status FROM flight_weather_logs")
        matching_rows = cursor.fetchall()
        step_used = 3
    finally:
        conn.close()
        
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
