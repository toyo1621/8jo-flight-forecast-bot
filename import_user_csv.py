import os
import sqlite3
import csv
import re
import requests
import argparse
from datetime import datetime, timedelta

DB_FILE = "flights.db"
DEFAULT_CSV_FILE = "user_raw_data.csv"
HACHIJOJIMA_LAT = 33.115
HACHIJOJIMA_LON = 139.782

FLIGHT_MAPPING = [
    {"col_idx": 1, "flight_number": "ANA1891", "scheduled_time": "08:30", "target_hour": 8},
    {"col_idx": 2, "flight_number": "ANA1893", "scheduled_time": "13:10", "target_hour": 13},
    {"col_idx": 3, "flight_number": "ANA1895", "scheduled_time": "16:40", "target_hour": 17},
]

def init_db():
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
    try:
        cursor.execute("ALTER TABLE flight_weather_logs ADD COLUMN visibility REAL")
        conn.commit()
        print("既存のデータベースに visibility (視程) カラムを追加しました。")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE flight_weather_logs ADD COLUMN status_reason TEXT")
        conn.commit()
        print("既存のデータベースに status_reason (運航理由) カラムを追加しました。")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn

def parse_date_range(date_str):
    date_str = date_str.strip()
    if '〜' in date_str:
        start_part, end_part = date_str.split('〜')
        start_dt = datetime.strptime(start_part, "%Y-%m-%d")
        
        # end_part は '01-07' のような MM-DD 形式を想定。年を補完
        year = start_dt.year
        end_str = f"{year}-{end_part}"
        end_dt = datetime.strptime(end_str, "%Y-%m-%d")
        
        dates = []
        curr = start_dt
        while curr <= end_dt:
            dates.append(curr.strftime("%Y-%m-%d"))
            curr += timedelta(days=1)
        return dates
    else:
        # '2025-6-12' のような日付もAPI用のゼロ埋め形式へ統一
        return [datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")]

def parse_status(status_str):
    status_str = status_str.strip().replace(" ", "").replace("　", "")

    if not status_str or "?" in status_str or "？" in status_str:
        return None, None

    reason_match = re.search(r"欠航[（(]([^）)]+)[）)]", status_str)
    reason = reason_match.group(1) if reason_match else None

    if "引返" in status_str and "欠航" in status_str:
        return "条件付き→引返欠航", reason
    if "欠航" in status_str or "全便欠航" in status_str:
        return "欠航", reason
    if "条件付" in status_str and "運航" in status_str:
        return "運航(条件付)", None
    if "遅延" in status_str:
        return "遅延", None
    if status_str == "運航" or "通常" in status_str or "到着" in status_str:
        return "運航", None

    return None, None


def map_status(status_str):
    return parse_status(status_str)[0]

def fetch_archive_weather(start_date, end_date):
    print(f"Open-Meteo Archive APIから {start_date} から {end_date} の気象データを取得中...")
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
    return response.json().get("hourly", {})

def main():
    parser = argparse.ArgumentParser(description="CSVの過去運航実績にOpen-Meteoの過去気象データを付与してDBへUPSERTします")
    parser.add_argument("--csv", default=DEFAULT_CSV_FILE, help="取り込むCSVファイルのパス")
    parser.add_argument(
        "--backend",
        choices=("sqlite", "bigquery", "both"),
        default="sqlite",
        help="保存先。bothでSQLiteとBigQueryの両方へ保存します。",
    )
    args = parser.parse_args()

    if not os.path.exists(args.csv):
        print(f"エラー: {args.csv} が見つかりません。")
        return
        
    conn = init_db()
    cursor = conn.cursor()
    
    # ユーザー提供CSVのパース
    raw_records = []
    all_dates = set()
    
    with open(args.csv, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader) # ヘッダーをスキップ
        
        for row in reader:
            if not row or len(row) < 4:
                continue
            
            date_range_str = row[0]
            try:
                dates = parse_date_range(date_range_str)
            except ValueError as e:
                print(f"日付パースエラー '{date_range_str}': {e}")
                continue
                
            for d in dates:
                all_dates.add(d)
                
            raw_records.append({
                "dates": dates,
                "statuses": [row[1], row[2], row[3]]
            })
            
    if not all_dates:
        print("有効な日付データがありませんでした。")
        return
        
    # 日付範囲の決定
    min_date = min(all_dates)
    max_date = max(all_dates)
    
    # 気象データの取得
    try:
        try:
            hourly_data = fetch_archive_weather(min_date, max_date)
        except requests.RequestException as e:
            print(f"警告: 気象データの取得に失敗しました。運航実績のみ先に登録します: {e}")
            hourly_data = {}
        
        # 気象データをマッピング
        times = hourly_data.get("time", [])
        wind_speeds = hourly_data.get("wind_speed_10m", [])
        wind_dirs = hourly_data.get("wind_direction_10m", [])
        wind_gusts = hourly_data.get("wind_gusts_10m", [])
        cloud_covers = hourly_data.get("cloud_cover_low", [])
        visibilities = hourly_data.get("visibility", [])
        
        weather_map = {}
        for i, t in enumerate(times):
            dt_parsed = datetime.strptime(t, "%Y-%m-%dT%H:%M")
            date_key = dt_parsed.strftime("%Y-%m-%d")
            hour_key = dt_parsed.hour
            
            # 風速と突風は m/s に変換
            ws_kmh = wind_speeds[i]
            wg_kmh = wind_gusts[i]
            ws_ms = round(ws_kmh / 3.6, 2) if ws_kmh is not None else None
            wg_ms = round(wg_kmh / 3.6, 2) if wg_kmh is not None else None
            
            # 視程は km に変換
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
            
        print("気象データの取得とパースが完了しました。データベースに保存しています...")
        
        items = []
        for rec in raw_records:
            for date_str in rec["dates"]:
                for f_map in FLIGHT_MAPPING:
                    col_idx = f_map["col_idx"]
                    status_raw = rec["statuses"][col_idx - 1]
                    status, status_reason = parse_status(status_raw)
                    
                    if status is None:
                        # 取得していない、または不明なステータスはスキップ
                        continue
                        
                    # 気象データを取得
                    w = None
                    if date_str in weather_map:
                        target_hour = f_map["target_hour"]
                        if target_hour in weather_map[date_str]:
                            w = weather_map[date_str][target_hour]
                            
                    if w is None:
                        # 気象データが見つからない場合はダミーまたはNoneで保存
                        w = {
                            "wind_direction": None,
                            "wind_speed": None,
                            "wind_gusts": None,
                            "cloud_cover_low": None,
                            "visibility": None
                        }
                        
                    item = {
                        "date": date_str,
                        "flight_number": f_map["flight_number"],
                        "scheduled_time": f_map["scheduled_time"],
                        "status": status,
                        "status_reason": status_reason,
                        "wind_direction": w["wind_direction"],
                        "wind_speed": w["wind_speed"],
                        "wind_gusts": w["wind_gusts"],
                        "cloud_cover_low": w["cloud_cover_low"],
                        "visibility": w["visibility"],
                        "visibility_source": "open_meteo_archive" if w["visibility"] is not None else None,
                    }
                    items.append(item)

                    if args.backend in {"sqlite", "both"}:
                        cursor.execute("""
                        INSERT INTO flight_weather_logs (
                            date, flight_number, scheduled_time, status, status_reason,
                            wind_direction, wind_speed, wind_gusts, cloud_cover_low, visibility
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(date, flight_number) DO UPDATE SET
                            scheduled_time=excluded.scheduled_time,
                            status=excluded.status,
                            status_reason=excluded.status_reason,
                            wind_direction=excluded.wind_direction,
                            wind_speed=excluded.wind_speed,
                            wind_gusts=excluded.wind_gusts,
                            cloud_cover_low=excluded.cloud_cover_low,
                            visibility=excluded.visibility,
                            created_at=CURRENT_TIMESTAMP
                        """, (
                            date_str, f_map["flight_number"], f_map["scheduled_time"],
                            status, status_reason, w["wind_direction"], w["wind_speed"],
                            w["wind_gusts"], w["cloud_cover_low"], w["visibility"]
                        ))

        if args.backend in {"sqlite", "both"}:
            conn.commit()
            print(f"SQLite登録完了: {len(items)}件")
        if args.backend in {"bigquery", "both"}:
            from bigquery_storage import upsert_flight_weather_logs

            inserted = upsert_flight_weather_logs(items)
            print(f"BigQuery登録完了: {inserted}件")

        print(f"インポート完了: 合計 {len(items)} 件のリアル運航実績データを登録しました。")
        
    except Exception as e:
        print(f"エラーが発生しました: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()

