import os
import sqlite3
from datetime import datetime
import argparse
import requests
from dotenv import load_dotenv

from bigquery_storage import upsert_flight_weather_logs

# 環境変数の読み込み
load_dotenv()

# 定数定義
DB_FILE = "flights.db"
HACHIJOJIMA_LAT = 33.115
HACHIJOJIMA_LON = 139.782

FLIGHTS_SCHEDULE = [
    {"flight_number": "ANA1891", "scheduled_time": "08:30", "target_hour": 8},
    {"flight_number": "ANA1893", "scheduled_time": "13:10", "target_hour": 13},
    {"flight_number": "ANA1895", "scheduled_time": "16:40", "target_hour": 17},
]

# ODPT APIのフライトステータスマッピング
STATUS_MAPPING = {
    "odpt.FlightStatus:Normal": "通常",
    "odpt.FlightStatus:Cancelled": "欠航",
    "odpt.FlightStatus:Delayed": "遅延",
    "odpt.FlightStatus:Diverted": "引き返し(他空港着)",
    "odpt.FlightStatus:Returned": "引き返し(出発空港着)",
    "odpt.FlightStatus:Conditional": "条件付き運航",
}


def init_db():
    """SQLite データベースとテーブルの初期化"""
    print(f"データベース {DB_FILE} を初期化しています...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS flight_weather_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
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

    conn.commit()
    return conn


def get_weather_data(date_str, scheduled_time_str):
    """Open-Meteo API から指定された日付・時間の八丈島空港の気象データを取得"""
    print(f"Open-Meteo APIから {date_str} {scheduled_time_str} の気象データを取得中...")

    try:
        time_obj = datetime.strptime(scheduled_time_str, "%H:%M")
        hour = time_obj.hour
        if time_obj.minute >= 30:
            hour = (hour + 1) % 24
    except ValueError:
        hour = 12

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": HACHIJOJIMA_LAT,
        "longitude": HACHIJOJIMA_LON,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover_low,visibility",
        "timezone": "Asia/Tokyo",
        "start_date": date_str,
        "end_date": date_str,
    }

    try:
        response = requests.get(url, params=params, timeout=10)

        if response.status_code != 200:
            print("予測APIでエラーが発生したため、アーカイブAPIにフォールバックします...")
            archive_url = "https://archive-api.open-meteo.com/v1/archive"
            response = requests.get(archive_url, params=params, timeout=10)

        response.raise_for_status()
        data = response.json()

        if "hourly" not in data:
            raise ValueError("気象データに hourly 項目が含まれていません。")

        hourly_data = data["hourly"]
        idx = hour

        wind_speed_kmh = hourly_data["wind_speed_10m"][idx]
        wind_gusts_kmh = hourly_data["wind_gusts_10m"][idx]
        visibility_m = hourly_data["visibility"][idx] if "visibility" in hourly_data else None

        wind_speed_ms = round(wind_speed_kmh / 3.6, 2) if wind_speed_kmh is not None else None
        wind_gusts_ms = round(wind_gusts_kmh / 3.6, 2) if wind_gusts_kmh is not None else None
        visibility_km = round(visibility_m / 1000.0, 2) if visibility_m is not None else None

        return {
            "wind_direction": hourly_data["wind_direction_10m"][idx],
            "wind_speed": wind_speed_ms,
            "wind_gusts": wind_gusts_ms,
            "cloud_cover_low": hourly_data["cloud_cover_low"][idx],
            "visibility": visibility_km,
        }

    except Exception as e:
        print(f"気象データの取得に失敗しました: {e}")
        return {
            "wind_direction": None,
            "wind_speed": None,
            "wind_gusts": None,
            "cloud_cover_low": None,
            "visibility": None,
        }


def get_scheduled_flights(date_str, default_status="未取得"):
    """1日3便分の保存枠を作るため、固定ダイヤから基礎レコードを生成する。"""
    return [
        {
            "date": date_str,
            "flight_number": flight["flight_number"],
            "scheduled_time": flight["scheduled_time"],
            "status": default_status,
        }
        for flight in FLIGHTS_SCHEDULE
    ]


def get_flight_data_odpt(api_key):
    """ODPT API から ANA の八丈島着便の運航情報を取得"""
    print("ODPT APIから運航実績データを取得中...")
    url = "https://api.odpt.org/api/v4/odpt:FlightInformationArrival"
    params = {
        "odpt:operator": "odpt.Operator:ANA",
        "odpt:arrivalAirport": "odpt.Airport:HAC",
        "acl:consumerKey": api_key,
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        flights = response.json()

        result_flights = []
        for f in flights:
            dep_airport = f.get("odpt:originAirport")
            if dep_airport != "odpt.Airport:HND":
                continue

            flight_nums = f.get("odpt:flightNumber", [])
            flight_num_raw = flight_nums[0] if isinstance(flight_nums, list) and flight_nums else ""
            if not flight_num_raw and isinstance(flight_nums, str):
                flight_num_raw = flight_nums

            if flight_num_raw.startswith("NH"):
                flight_number = flight_num_raw.replace("NH", "ANA", 1)
            else:
                flight_number = f"ANA{flight_num_raw}" if flight_num_raw else "ANA-Unknown"

            scheduled_time = f.get("odpt:scheduledArrivalTime", "")

            flight_date_raw = f.get("odpt:flightDate")
            if flight_date_raw:
                date_str = flight_date_raw
            else:
                date_created = f.get("dc:date", "")
                if date_created:
                    date_str = date_created.split("T")[0]
                else:
                    date_str = datetime.today().strftime("%Y-%m-%d")

            status_raw = f.get("odpt:flightStatus", "odpt.FlightStatus:Normal")
            status = STATUS_MAPPING.get(status_raw, status_raw)

            result_flights.append({
                "date": date_str,
                "flight_number": flight_number,
                "scheduled_time": scheduled_time,
                "status": status,
            })

        print(f"ODPT APIから {len(result_flights)} 件のANA羽田発・八丈島着便を取得しました。")
        return result_flights

    except Exception as e:
        print(f"ODPT APIからのデータ取得に失敗しました: {e}")
        return []


def merge_with_daily_schedule(date_str, actual_flights):
    """ODPTで取れた便だけでなく、固定ダイヤの3便を必ずDB保存対象に含める。"""
    merged = {flight["flight_number"]: flight for flight in get_scheduled_flights(date_str)}

    for flight in actual_flights:
        flight_number = flight.get("flight_number")
        if flight.get("date") != date_str or flight_number not in merged:
            continue

        merged[flight_number].update({
            "scheduled_time": flight.get("scheduled_time") or merged[flight_number]["scheduled_time"],
            "status": flight.get("status") or merged[flight_number]["status"],
        })

    return list(merged.values())


def get_demo_flight_data():
    """APIキーがない場合のデモ用ダミーデータを生成"""
    print("APIキーが設定されていないため、デモ用ダミーデータを生成します...")
    today = datetime.today().strftime("%Y-%m-%d")
    flights = get_scheduled_flights(today, default_status="通常")
    flights[1]["status"] = "条件付き運航"
    return flights


def save_flight_weather_logs(conn, flights_with_weather):
    """SQLite データベースにフライト & 気象データを保存"""
    cursor = conn.cursor()
    saved_count = 0

    for item in flights_with_weather:
        try:
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
                item["date"],
                item["flight_number"],
                item["scheduled_time"],
                item["status"],
                item["wind_direction"],
                item["wind_speed"],
                item["wind_gusts"],
                item["cloud_cover_low"],
                item.get("visibility"),
            ))
            saved_count += 1
        except sqlite3.Error as e:
            print(f"データベース保存エラー ({item['flight_number']}): {e}")

    conn.commit()
    print(f"データベースに {saved_count} 件のデータを保存・更新しました。")


def save_collected_data(conn, flights_with_weather):
    backend = os.getenv("FORECAST_DATA_BACKEND", "sqlite").lower()
    if backend == "bigquery":
        saved_count = upsert_flight_weather_logs(flights_with_weather)
        print(f"BigQueryに {saved_count} 件のデータを保存・更新しました。")
        return
    save_flight_weather_logs(conn, flights_with_weather)


def main():
    parser = argparse.ArgumentParser(description="羽田→八丈島便の運航・気象データ自動収集スクリプト")
    parser.add_argument("--demo", action="store_true", help="APIキーの有無にかかわらずデモ用データを使用する")
    args = parser.parse_args()

    backend = os.getenv("FORECAST_DATA_BACKEND", "sqlite").lower()
    conn = None if backend == "bigquery" else init_db()
    api_key = os.getenv("ODPT_API_KEY")
    today = datetime.today().strftime("%Y-%m-%d")

    if args.demo:
        flights = get_demo_flight_data()
    else:
        if not api_key or api_key == "your_odpt_api_key_here":
            raise RuntimeError("ODPT_API_KEYが未設定です。実績DBへのデモデータ保存を中止します。")
        actual_flights = get_flight_data_odpt(api_key)
        flights = merge_with_daily_schedule(today, actual_flights)

    completed_data = []
    for f in flights:
        weather = get_weather_data(f["date"], f["scheduled_time"])
        merged_item = {**f, **weather}
        completed_data.append(merged_item)

    save_collected_data(conn, completed_data)

    if conn is not None:
        print("\n--- 保存された最新のデータ (最大5件) ---")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, flight_number, scheduled_time, status, wind_direction, wind_speed, wind_gusts, cloud_cover_low, visibility
            FROM flight_weather_logs
            ORDER BY date DESC, scheduled_time DESC LIMIT 5
        """)
        rows = cursor.fetchall()
        for row in rows:
            print(f"日付: {row[0]} | 便名: {row[1]} | 定刻: {row[2]} | 結果: {row[3]} | 風向: {row[4]}° | 風速: {row[5]} m/s | 突風: {row[6]} m/s | 雲量: {row[7]}% | 視程: {row[8]} km")
        conn.close()
    print("データ自動収集処理が完了しました。")


if __name__ == "__main__":
    main()
