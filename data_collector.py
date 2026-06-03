import os
import sqlite3
from datetime import datetime
import argparse
import requests
from dotenv import load_dotenv

# 環境変数の読み込み
load_dotenv()

# 定数定義
DB_FILE = "flights.db"
HACHIJOJIMA_LAT = 33.115
HACHIJOJIMA_LON = 139.782

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
    
    # flight_weather_logs テーブルの作成
    # 重複防止のため (date, flight_number) に UNIQUE 制約を設ける
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
    
    # 既存のDBがある場合の自動マイグレーション (visibilityカラムの追加)
    try:
        cursor.execute("ALTER TABLE flight_weather_logs ADD COLUMN visibility REAL")
        conn.commit()
        print("既存のデータベースに visibility (視程) カラムを追加しました。")
    except sqlite3.OperationalError:
        # すでにカラムが存在する場合は無視
        pass
    
    conn.commit()
    return conn

def get_weather_data(date_str, scheduled_time_str):
    """
    Open-Meteo API から指定された日付・時間の八丈島空港の気象データを取得
    
    Args:
        date_str (str): 日付 (YYYY-MM-DD)
        scheduled_time_str (str): 定刻 (HH:MM)
        
    Returns:
        dict: 気象データ (wind_direction, wind_speed, wind_gusts, cloud_cover_low)
    """
    print(f"Open-Meteo APIから {date_str} {scheduled_time_str} の気象データを取得中...")
    
    # 定刻 (HH:MM) から最も近い「時」を算出
    try:
        time_obj = datetime.strptime(scheduled_time_str, "%H:%M")
        # 30分以上は切り上げ、それ以外は切り捨て
        hour = time_obj.hour
        if time_obj.minute >= 30:
            hour = (hour + 1) % 24
    except ValueError:
        hour = 12  # パース失敗時はデフォルトで正午とする
        
    # Open-Meteo APIのエンドポイント (予報・直近過去データ用)
    # 過去データおよび予測データをカバーするため、start_date/end_date を明示指定
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": HACHIJOJIMA_LAT,
        "longitude": HACHIJOJIMA_LON,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover_low,visibility",
        "timezone": "Asia/Tokyo",
        "start_date": date_str,
        "end_date": date_str
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        
        # もしリクエストがエラー (400系など、過去すぎるデータの場合) ならアーカイブAPIにフォールバック
        if response.status_code != 200:
            print("予測APIでエラーが発生したため、アーカイブAPIにフォールバックします...")
            archive_url = "https://archive-api.open-meteo.com/v1/archive"
            response = requests.get(archive_url, params=params, timeout=10)
            
        response.raise_for_status()
        data = response.json()
        
        if "hourly" not in data:
            raise ValueError("気象データに hourly 項目が含まれていません。")
            
        hourly_data = data["hourly"]
        
        # 該当する時間のインデックスを取得 (0-23)
        idx = hour
        
        # パースされたデータの抽出 (風速は km/h から m/s に変換: 3.6 で割る)
        # ※Open-Meteoはデフォルトで風速が km/h なので、気象情報の一般的な単位 m/s に変換しておくと扱いやすい
        wind_speed_kmh = hourly_data["wind_speed_10m"][idx]
        wind_gusts_kmh = hourly_data["wind_gusts_10m"][idx]
        visibility_m = hourly_data["visibility"][idx] if "visibility" in hourly_data else None
        
        wind_speed_ms = round(wind_speed_kmh / 3.6, 2) if wind_speed_kmh is not None else None
        wind_gusts_ms = round(wind_gusts_kmh / 3.6, 2) if wind_gusts_kmh is not None else None
        
        # 視程をメートル (m) からキロメートル (km) に変換して丸める
        visibility_km = round(visibility_m / 1000.0, 2) if visibility_m is not None else None
        
        return {
            "wind_direction": hourly_data["wind_direction_10m"][idx],
            "wind_speed": wind_speed_ms,
            "wind_gusts": wind_gusts_ms,
            "cloud_cover_low": hourly_data["cloud_cover_low"][idx],
            "visibility": visibility_km
        }
        
    except Exception as e:
        print(f"気象データの取得に失敗しました: {e}")
        # 取得失敗時は None を格納する辞書を返す
        return {
            "wind_direction": None,
            "wind_speed": None,
            "wind_gusts": None,
            "cloud_cover_low": None,
            "visibility": None
        }

def get_flight_data_odpt(api_key):
    """
    ODPT API から ANA の八丈島着便の運航情報を取得
    
    Args:
        api_key (str): ODPT APIキー
        
    Returns:
        list: フライト情報のリスト
    """
    print("ODPT APIから運航実績データを取得中...")
    url = "https://api.odpt.org/api/v4/odpt:FlightInformationArrival"
    params = {
        "odpt:operator": "odpt.Operator:ANA",
        "odpt:arrivalAirport": "odpt.Airport:HAC",  # 八丈島空港 (IATAコード)
        "acl:consumerKey": api_key
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        flights = response.json()
        
        result_flights = []
        for f in flights:
            # 羽田発 (HND) のみに対象を絞る (APIのフィールド名は odpt:originAirport)
            dep_airport = f.get("odpt:originAirport")
            if dep_airport != "odpt.Airport:HND":
                continue
                
            # 各種情報のパース
            # 便名 (例: ["NH1891"] -> "ANA1891")
            flight_nums = f.get("odpt:flightNumber", [])
            flight_num_raw = flight_nums[0] if isinstance(flight_nums, list) and flight_nums else ""
            if not flight_num_raw and isinstance(flight_nums, str):
                flight_num_raw = flight_nums
                
            if flight_num_raw.startswith("NH"):
                flight_number = flight_num_raw.replace("NH", "ANA", 1)
            else:
                flight_number = f"ANA{flight_num_raw}" if flight_num_raw else "ANA-Unknown"
            
            # 定刻 (HH:MM)
            scheduled_time = f.get("odpt:scheduledArrivalTime", "")
            
            # 運航日 (YYYY-MM-DD)
            # ODPTのフライト日付、もしくはAPIレスポンスの作成日時から日付を取得
            flight_date_raw = f.get("odpt:flightDate")
            if flight_date_raw:
                date_str = flight_date_raw
            else:
                # 代替として作成日時を利用、なければ今日の日付
                date_created = f.get("dc:date", "")
                if date_created:
                    date_str = date_created.split("T")[0]
                else:
                    date_str = datetime.today().strftime("%Y-%m-%d")
            
            # 運航結果ステータス
            status_raw = f.get("odpt:flightStatus", "odpt.FlightStatus:Normal")
            status = STATUS_MAPPING.get(status_raw, status_raw)
            
            result_flights.append({
                "date": date_str,
                "flight_number": flight_number,
                "scheduled_time": scheduled_time,
                "status": status
            })
            
        print(f"ODPT APIから {len(result_flights)} 件のANA羽田発・八丈島着便を取得しました。")
        return result_flights
        
    except Exception as e:
        print(f"ODPT APIからのデータ取得に失敗しました: {e}")
        return []

def get_demo_flight_data():
    """APIキーがない場合のデモ用ダミーデータを生成"""
    print("APIキーが設定されていないため、デモ用ダミーデータを生成します...")
    today = datetime.today().strftime("%Y-%m-%d")
    
    # 羽田→八丈島便の代表的な3便をシミュレート
    # (通常、ANAの羽田-八丈島便は1日3往復)
    return [
        {"date": today, "flight_number": "ANA1891", "scheduled_time": "08:50", "status": "通常"},
        {"date": today, "flight_number": "ANA1893", "scheduled_time": "13:40", "status": "条件付き運航"},
        {"date": today, "flight_number": "ANA1895", "scheduled_time": "16:55", "status": "通常"},
    ]

def save_flight_weather_logs(conn, flights_with_weather):
    """
    SQLite データベースにフライト & 気象データを保存
    重複時は ON CONFLICT (date, flight_number) DO UPDATE (UPSERT)
    """
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
                item.get("visibility")
            ))
            saved_count += 1
        except sqlite3.Error as e:
            print(f"データベース保存エラー ({item['flight_number']}): {e}")
            
    conn.commit()
    print(f"データベースに {saved_count} 件のデータを保存・更新しました。")

def main():
    parser = argparse.ArgumentParser(description="羽田→八丈島便の運航・気象データ自動収集スクリプト")
    parser.add_argument("--demo", action="store_true", help="APIキーの有無にかかわらずデモ用データを使用する")
    args = parser.parse_args()
    
    # データベースの初期化
    conn = init_db()
    
    # APIキーの取得
    api_key = os.getenv("ODPT_API_KEY")
    
    # フライト情報の取得
    if args.demo or not api_key or api_key == "your_odpt_api_key_here":
        if not args.demo and (not api_key or api_key == "your_odpt_api_key_here"):
            print("警告: .env に ODPT_API_KEY が設定されていません。")
        flights = get_demo_flight_data()
    else:
        flights = get_flight_data_odpt(api_key)
        
    if not flights:
        print("フライトデータが取得できなかったため、処理を終了します。")
        conn.close()
        return
        
    # 各フライトデータに対応する気象データを取得して結合
    completed_data = []
    for f in flights:
        weather = get_weather_data(f["date"], f["scheduled_time"])
        
        # データをマージ
        merged_item = {**f, **weather}
        completed_data.append(merged_item)
        
    # SQLiteへ保存
    save_flight_weather_logs(conn, completed_data)
    
    # 保存データの確認表示 (最新の5件)
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
