import os
import sqlite3
import requests
from datetime import datetime, timedelta
import random

# プロジェクトの定数定義
DB_FILE = "flights.db"
HACHIJOJIMA_LAT = 33.115
HACHIJOJIMA_LON = 139.782

FLIGHTS_SCHEDULE = [
    {"flight_number": "ANA1891", "scheduled_time": "08:30", "target_hour": 8},
    {"flight_number": "ANA1893", "scheduled_time": "13:10", "target_hour": 13},
    {"flight_number": "ANA1895", "scheduled_time": "16:40", "target_hour": 17}, # 16:40は17:00に近い
]

def init_db():
    """SQLite データベースとテーブルの初期化"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS flight_weather_logs (
        date TEXT NOT NULL,
        flight_number TEXT NOT NULL,
        scheduled_time TEXT,
        status TEXT,
        wind_direction REAL,
        wind_speed REAL,
        wind_gusts REAL,
        cloud_cover_low REAL,
        visibility REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(date, flight_number)
    )
    """)
    conn.commit()
    return conn

def fetch_archive_weather(start_date, end_date):
    """Open-Meteo Archive APIから過去半年分の1時間ごとの気象データを一括取得"""
    print(f"Open-Meteo Archive APIから {start_date} から {end_date} の気象データを取得しています...")
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": HACHIJOJIMA_LAT,
        "longitude": HACHIJOJIMA_LON,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover_low,visibility",
        "timezone": "Asia/Tokyo",
        "start_date": start_date,
        "end_date": end_date
    }
    
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    return data.get("hourly", {})

def generate_status(wind_speed, wind_gusts, visibility, cloud_cover_low):
    """気象条件に基づき、擬似的な運航結果(status)を生成する"""
    # 欠航判定
    if (wind_speed is not None and wind_speed > 13.0) or \
       (wind_gusts is not None and wind_gusts > 22.0) or \
       (visibility is not None and visibility < 1.0):
        return "欠航"
        
    # 機材繰り等のランダム欠航 (約2.5%)
    if random.random() < 0.025:
        return "欠航"
        
    # 条件付き運航判定
    if (wind_speed is not None and wind_speed > 9.0) or \
       (wind_gusts is not None and wind_gusts > 15.0) or \
       (visibility is not None and visibility < 3.0) or \
       (cloud_cover_low is not None and cloud_cover_low > 85.0):
        return "条件付き→就航"
        
    # ランダムな条件付き運航 (約3.5%)
    if random.random() < 0.035:
        return "条件付き→就航"
        
    return "通常"

def main():
    conn = init_db()
    
    # 期間の設定 (過去180日前から昨日まで)
    end_date_dt = datetime.today() - timedelta(days=1)
    start_date_dt = end_date_dt - timedelta(days=180)
    
    start_date_str = start_date_dt.strftime("%Y-%m-%d")
    end_date_str = end_date_dt.strftime("%Y-%m-%d")
    
    try:
        hourly_data = fetch_archive_weather(start_date_str, end_date_str)
        if not hourly_data:
            print("気象データの取得に失敗しました。")
            return
            
        times = hourly_data.get("time", [])
        wind_speeds = hourly_data.get("wind_speed_10m", [])
        wind_dirs = hourly_data.get("wind_direction_10m", [])
        wind_gusts = hourly_data.get("wind_gusts_10m", [])
        cloud_covers = hourly_data.get("cloud_cover_low", [])
        visibilities = hourly_data.get("visibility", [])
        
        # タイムスタンプ(YYYY-MM-DDTHH:MM)をインデックス可能な辞書にマッピング
        # 例: {"2026-06-03 08:00": {wind_speed, wind_dir, ...}}
        weather_map = {}
        for i, t in enumerate(times):
            # t は "2026-05-30T08:00" のような形式
            dt_parsed = datetime.strptime(t, "%Y-%m-%dT%H:%M")
            date_key = dt_parsed.strftime("%Y-%m-%d")
            hour_key = dt_parsed.hour
            
            # 風速と突風は km/h から m/s に変換
            ws_kmh = wind_speeds[i]
            wg_kmh = wind_gusts[i]
            ws_ms = round(ws_kmh / 3.6, 2) if ws_kmh is not None else None
            wg_ms = round(wg_kmh / 3.6, 2) if wg_kmh is not None else None
            
            # 視程はメートルからキロメートルに変換
            vis_m = visibilities[i]
            vis_km = round(vis_m / 1000.0, 2) if vis_m is not None else None
            
            if date_key not in weather_map:
                weather_map[date_key] = {}
                
            weather_map[date_key][hour_key] = {
                "wind_direction": wind_dirs[i],
                "wind_speed": ws_ms,
                "wind_gusts": wg_ms,
                "cloud_cover_low": cloud_covers[i],
                "visibility": vis_km
            }
            
        print("気象データのパースが完了しました。フライトデータを生成してDBへ挿入します...")
        
        cursor = conn.cursor()
        total_inserted = 0
        current_date = start_date_dt
        
        while current_date <= end_date_dt:
            date_str = current_date.strftime("%Y-%m-%d")
            
            if date_str in weather_map:
                day_weather = weather_map[date_str]
                
                for f in FLIGHTS_SCHEDULE:
                    target_hour = f["target_hour"]
                    
                    if target_hour in day_weather:
                        w = day_weather[target_hour]
                        
                        # 運航ステータスの決定
                        status = generate_status(
                            w["wind_speed"],
                            w["wind_gusts"],
                            w["visibility"],
                            w["cloud_cover_low"]
                        )
                        
                        # SQLiteに挿入/更新
                        cursor.execute("""
                        INSERT INTO flight_weather_logs (
                            date, flight_number, scheduled_time, status,
                            wind_direction, wind_speed, wind_gusts, cloud_cover_low, visibility
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(date, flight_number) DO UPDATE SET
                            scheduled_time=excluded.scheduled_time,
                            status=excluded.status,
                            wind_direction=excluded.wind_direction,
                            wind_speed=excluded.wind_speed,
                            wind_gusts=excluded.wind_gusts,
                            cloud_cover_low=excluded.cloud_cover_low,
                            visibility=excluded.visibility,
                            created_at=CURRENT_TIMESTAMP
                        """, (
                            date_str,
                            f["flight_number"],
                            f["scheduled_time"],
                            status,
                            w["wind_direction"],
                            w["wind_speed"],
                            w["wind_gusts"],
                            w["cloud_cover_low"],
                            w["visibility"]
                        ))
                        total_inserted += 1
                        
            current_date += timedelta(days=1)
            
        conn.commit()
        print(f"過去データの生成が完了しました。合計 {total_inserted} 件のフライトログを保存/更新しました。")
        
    except Exception as e:
        print(f"エラーが発生しました: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()

