import argparse
import sqlite3
from datetime import date, datetime, timedelta

import requests

from db_snapshot import export_dump, restore_db

DB_FILE = "flights.db"
HACHIJOJIMA_LAT = 33.115
HACHIJOJIMA_LON = 139.782
HOURLY_FIELDS = "wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover_low,visibility"
WEATHER_COLUMNS = (
    "wind_direction",
    "wind_speed",
    "wind_gusts",
    "cloud_cover_low",
    "visibility",
)


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").date()


def nearest_hour(scheduled_time):
    try:
        time_obj = datetime.strptime(scheduled_time, "%H:%M")
    except (TypeError, ValueError):
        return 12

    hour = time_obj.hour
    if time_obj.minute >= 30:
        hour = (hour + 1) % 24
    return hour


def date_chunks(dates, chunk_size=31):
    if not dates:
        return

    sorted_dates = sorted(dates)
    chunk_start = sorted_dates[0]
    previous = sorted_dates[0]

    for current in sorted_dates[1:]:
        too_many_days = (current - chunk_start).days >= chunk_size
        not_contiguous = (current - previous).days > 1
        if too_many_days or not_contiguous:
            yield chunk_start, previous
            chunk_start = current
        previous = current

    yield chunk_start, previous


def endpoint_for_range(start_date, end_date):
    archive_cutoff = date.today() - timedelta(days=5)
    if end_date <= archive_cutoff:
        return "https://archive-api.open-meteo.com/v1/archive"
    return "https://api.open-meteo.com/v1/forecast"


def fetch_weather_range(start_date, end_date):
    url = endpoint_for_range(start_date, end_date)
    params = {
        "latitude": HACHIJOJIMA_LAT,
        "longitude": HACHIJOJIMA_LON,
        "hourly": HOURLY_FIELDS,
        "timezone": "Asia/Tokyo",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json().get("hourly", {})


def build_weather_map(hourly_data):
    weather_map = {}
    times = hourly_data.get("time", [])
    wind_speeds = hourly_data.get("wind_speed_10m", [])
    wind_dirs = hourly_data.get("wind_direction_10m", [])
    wind_gusts = hourly_data.get("wind_gusts_10m", [])
    cloud_covers = hourly_data.get("cloud_cover_low", [])
    visibilities = hourly_data.get("visibility", [])

    for i, time_value in enumerate(times):
        parsed = datetime.strptime(time_value, "%Y-%m-%dT%H:%M")
        wind_speed_kmh = wind_speeds[i]
        wind_gusts_kmh = wind_gusts[i]
        visibility_m = visibilities[i]
        weather_map[(parsed.date().isoformat(), parsed.hour)] = {
            "wind_direction": wind_dirs[i],
            "wind_speed": round(wind_speed_kmh / 3.6, 2) if wind_speed_kmh is not None else None,
            "wind_gusts": round(wind_gusts_kmh / 3.6, 2) if wind_gusts_kmh is not None else None,
            "cloud_cover_low": cloud_covers[i],
            "visibility": round(visibility_m / 1000.0, 2) if visibility_m is not None else None,
        }

    return weather_map


def get_missing_rows(conn):
    cursor = conn.cursor()
    missing_condition = " OR ".join(f"{column} IS NULL" for column in WEATHER_COLUMNS)
    cursor.execute(f"""
        SELECT id, date, flight_number, scheduled_time
        FROM flight_weather_logs
        WHERE {missing_condition}
        ORDER BY date, scheduled_time, flight_number
    """)
    return cursor.fetchall()


def update_weather(conn, rows, weather_map):
    cursor = conn.cursor()
    updated = 0
    missing_weather = 0

    for row_id, date_str, flight_number, scheduled_time in rows:
        hour = nearest_hour(scheduled_time)
        weather = weather_map.get((date_str, hour))
        if weather is None:
            print(f"気象データなし: {date_str} {flight_number} {scheduled_time} -> {hour}:00")
            missing_weather += 1
            continue

        cursor.execute("""
            UPDATE flight_weather_logs
            SET wind_direction = ?,
                wind_speed = ?,
                wind_gusts = ?,
                cloud_cover_low = ?,
                visibility = ?,
                created_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            weather["wind_direction"],
            weather["wind_speed"],
            weather["wind_gusts"],
            weather["cloud_cover_low"],
            weather["visibility"],
            row_id,
        ))
        updated += 1

    conn.commit()
    return updated, missing_weather


def backfill_missing_weather(export_snapshot=False):
    restore_db()
    conn = sqlite3.connect(DB_FILE)
    try:
        rows = get_missing_rows(conn)
        if not rows:
            print("気象データがNULLの行はありません。")
            return 0

        dates = {parse_date(row[1]) for row in rows}
        weather_map = {}
        fetch_errors = 0
        for start_date, end_date in date_chunks(dates):
            print(f"Open-Meteoから {start_date} 〜 {end_date} の気象データを取得中...")
            try:
                hourly_data = fetch_weather_range(start_date, end_date)
            except requests.RequestException as e:
                fetch_errors += 1
                print(f"警告: {start_date} 〜 {end_date} の気象データ取得に失敗しました: {e}")
                continue
            weather_map.update(build_weather_map(hourly_data))

        if not weather_map:
            print(f"気象データを取得できませんでした。取得失敗チャンク数: {fetch_errors}")
            return 0

        updated, missing_weather = update_weather(conn, rows, weather_map)
        print(f"更新完了: {updated} 件更新 / 気象データなし {missing_weather} 件")
    finally:
        conn.close()

    if export_snapshot:
        export_dump()

    return updated


def main():
    parser = argparse.ArgumentParser(description="Backfill NULL weather columns in flights.db from Open-Meteo.")
    parser.add_argument("--export-snapshot", action="store_true", help="更新後に data/flights_dump.sql へエクスポートする")
    args = parser.parse_args()
    backfill_missing_weather(export_snapshot=args.export_snapshot)


if __name__ == "__main__":
    main()
