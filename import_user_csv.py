import argparse
import csv
import re
from datetime import datetime, timedelta
from pathlib import Path

import requests

from app_config import FLIGHTS, HACHIJO_AIRPORT_LATITUDE, HACHIJO_AIRPORT_LONGITUDE
from bigquery_storage import upsert_flight_weather_logs


DEFAULT_CSV_FILE = "user_raw_data.csv"
UNKNOWN_REASON = "未確認"
FLIGHT_MAPPING = tuple(
    {
        "col_idx": index,
        "flight_number": flight["number"],
        "scheduled_time": flight["time"],
        "target_hour": flight["forecast_hour"],
    }
    for index, flight in enumerate(FLIGHTS, start=1)
)
def parse_date_range(date_str):
    date_str = date_str.strip()
    if "〜" not in date_str:
        return [datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d")]

    start_part, end_part = date_str.split("〜", maxsplit=1)
    start_dt = datetime.strptime(start_part, "%Y-%m-%d")
    end_dt = datetime.strptime(f"{start_dt.year}-{end_part}", "%Y-%m-%d")
    if end_dt < start_dt:
        end_dt = end_dt.replace(year=end_dt.year + 1)

    dates = []
    current = start_dt
    while current <= end_dt:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates


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
    response = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": HACHIJO_AIRPORT_LATITUDE,
            "longitude": HACHIJO_AIRPORT_LONGITUDE,
            "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover_low,visibility",
            "timezone": "Asia/Tokyo",
            "start_date": start_date,
            "end_date": end_date,
        },
        timeout=15,
    )
    response.raise_for_status()
    hourly = response.json().get("hourly")
    if not isinstance(hourly, dict) or not hourly.get("time"):
        raise RuntimeError("Open-Meteo Archive APIから有効な時間別データを取得できませんでした。")
    return hourly


def build_weather_map(hourly):
    source_fields = {
        "wind_direction": "wind_direction_10m",
        "wind_speed": "wind_speed_10m",
        "wind_gusts": "wind_gusts_10m",
        "cloud_cover_low": "cloud_cover_low",
        "visibility": "visibility",
    }
    times = hourly.get("time", [])
    for source_field in source_fields.values():
        values = hourly.get(source_field)
        if not isinstance(values, list) or len(values) != len(times):
            raise RuntimeError(f"Open-Meteo Archive APIの {source_field} が欠損しています。")

    weather_map = {}
    for index, timestamp in enumerate(times):
        observed_at = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M")
        weather = {
            field: hourly[source_field][index]
            for field, source_field in source_fields.items()
        }
        missing = [field for field, value in weather.items() if value is None]
        if missing:
            continue
        weather["wind_speed"] = round(weather["wind_speed"] / 3.6, 2)
        weather["wind_gusts"] = round(weather["wind_gusts"] / 3.6, 2)
        weather["visibility"] = round(weather["visibility"] / 1000.0, 2)
        weather_map[(observed_at.strftime("%Y-%m-%d"), observed_at.hour)] = weather
    return weather_map


def read_csv_records(csv_path):
    raw_records = []
    all_dates = set()
    with csv_path.open("r", encoding="utf-8", newline="") as csv_file:
        reader = csv.reader(csv_file)
        next(reader, None)
        for row_number, row in enumerate(reader, start=2):
            if not row or len(row) < 4:
                continue
            try:
                dates = parse_date_range(row[0])
            except ValueError as exc:
                raise ValueError(f"CSV {row_number}行目の日付を解釈できません: {row[0]}") from exc
            all_dates.update(dates)
            raw_records.append({"dates": dates, "statuses": row[1:4]})
    if not all_dates:
        raise ValueError("CSVに有効な日付データがありません。")
    return raw_records, all_dates


def build_import_items(raw_records, weather_map):
    items = []
    for record in raw_records:
        for date_str in record["dates"]:
            for flight in FLIGHT_MAPPING:
                status, status_reason = parse_status(record["statuses"][flight["col_idx"] - 1])
                if status is None:
                    continue
                weather = weather_map.get((date_str, flight["target_hour"]))
                if weather is None:
                    raise RuntimeError(
                        f"{date_str} {flight['flight_number']}の気象データが欠測しているため、取り込みを中止します。"
                    )
                if status in {"欠航", "条件付き→引返欠航"}:
                    status_reason = status_reason or UNKNOWN_REASON
                items.append(
                    {
                        "date": date_str,
                        "flight_number": flight["flight_number"],
                        "scheduled_time": flight["scheduled_time"],
                        "status": status,
                        "status_reason": status_reason,
                        **weather,
                        "visibility_source": "open_meteo_archive",
                    }
                )
    if not items:
        raise ValueError("取り込み可能な運航実績がありません。")
    return items


def main():
    parser = argparse.ArgumentParser(
        description="CSVの過去運航実績にOpen-Meteoの過去気象データを付与してBigQueryへUPSERTします"
    )
    parser.add_argument("--csv", default=DEFAULT_CSV_FILE, help="取り込むCSVファイルのパス")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        parser.error(f"{csv_path} が見つかりません。")

    raw_records, all_dates = read_csv_records(csv_path)
    weather_map = build_weather_map(fetch_archive_weather(min(all_dates), max(all_dates)))
    items = build_import_items(raw_records, weather_map)
    inserted = upsert_flight_weather_logs(items)
    print(f"BigQuery登録完了: {inserted}件")


if __name__ == "__main__":
    main()
