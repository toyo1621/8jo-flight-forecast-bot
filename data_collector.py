import argparse
import os
from datetime import datetime

import requests
from dotenv import load_dotenv

from app_config import FLIGHTS, HACHIJO_AIRPORT_LATITUDE, HACHIJO_AIRPORT_LONGITUDE, JST
from bigquery_storage import delete_unresolved_status_rows, upsert_flight_weather_logs
from flight_metadata import VALID_STORED_STATUSES


load_dotenv()

UNKNOWN_REASON = "未確認"
REQUIRED_WEATHER_FIELDS = (
    "wind_direction",
    "wind_speed",
    "wind_gusts",
    "cloud_cover_low",
    "visibility",
)
FLIGHTS_SCHEDULE = tuple(
    {
        "flight_number": flight["number"],
        "scheduled_time": flight["time"],
        "target_hour": flight["forecast_hour"],
    }
    for flight in FLIGHTS
)
SCHEDULE_BY_NUMBER = {flight["flight_number"]: flight for flight in FLIGHTS_SCHEDULE}

STATUS_MAPPING = {
    "odpt.FlightStatus:Normal": "運航",
    "odpt.FlightStatus:Cancelled": "欠航",
    "odpt.FlightStatus:Delayed": "運航",
    "odpt.FlightStatus:Diverted": "条件付き→引返欠航",
    "odpt.FlightStatus:Returned": "条件付き→引返欠航",
    "odpt.FlightStatus:Conditional": "運航(条件付)",
    "odpt.FlightStatus:Arrived": "運航",
    "odpt.FlightStatus:EstimatedArrival": "運航",
}
STORED_STATUSES = VALID_STORED_STATUSES


class CollectionError(RuntimeError):
    """Raised when a collection run cannot produce a complete, trustworthy day."""


def _safe_request_error(source, exc):
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    suffix = f" (HTTP {status_code})" if status_code else ""
    return CollectionError(f"{source}からのデータ取得に失敗しました{suffix}。")


def get_weather_data(date_str, scheduled_time_str, target_hour=None):
    """Fetch complete weather data for the configured forecast hour."""
    print(f"Open-Meteo APIから {date_str} {scheduled_time_str} の気象データを取得中...")
    if target_hour is None:
        try:
            target_hour = datetime.strptime(scheduled_time_str, "%H:%M").hour
        except ValueError as exc:
            raise CollectionError(f"定刻を解釈できません: {scheduled_time_str}") from exc
    if not isinstance(target_hour, int) or not 0 <= target_hour <= 23:
        raise CollectionError(f"対象時刻が不正です: {target_hour}")

    params = {
        "latitude": HACHIJO_AIRPORT_LATITUDE,
        "longitude": HACHIJO_AIRPORT_LONGITUDE,
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m,cloud_cover_low,visibility",
        "timezone": "Asia/Tokyo",
        "start_date": date_str,
        "end_date": date_str,
    }
    try:
        response = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=10)
        if response.status_code != 200:
            print("予測APIでエラーが発生したため、アーカイブAPIにフォールバックします...")
            response = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params=params,
                timeout=10,
            )
        response.raise_for_status()
        hourly = response.json().get("hourly")
        if not isinstance(hourly, dict):
            raise CollectionError("気象データにhourly項目がありません。")

        target_timestamp = f"{date_str}T{target_hour:02d}:00"
        try:
            target_index = hourly["time"].index(target_timestamp)
        except (KeyError, AttributeError, ValueError) as exc:
            raise CollectionError(f"気象データに対象時刻 {target_timestamp} がありません。") from exc

        weather = {
            "wind_direction": hourly["wind_direction_10m"][target_index],
            "wind_speed": hourly["wind_speed_10m"][target_index],
            "wind_gusts": hourly["wind_gusts_10m"][target_index],
            "cloud_cover_low": hourly["cloud_cover_low"][target_index],
            "visibility": hourly["visibility"][target_index],
        }
    except requests.RequestException as exc:
        raise _safe_request_error("Open-Meteo API", exc) from None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise CollectionError("Open-Meteo APIの応答構造が不正です。") from exc

    missing = [field for field, value in weather.items() if value is None]
    if missing:
        raise CollectionError(f"気象データが欠測しています: {', '.join(missing)}")

    return {
        "wind_direction": weather["wind_direction"],
        "wind_speed": round(weather["wind_speed"] / 3.6, 2),
        "wind_gusts": round(weather["wind_gusts"] / 3.6, 2),
        "cloud_cover_low": weather["cloud_cover_low"],
        "visibility": round(weather["visibility"] / 1000.0, 2),
        "visibility_source": "open_meteo_forecast",
    }


def get_scheduled_flights(date_str, default_status=None):
    return [
        {
            "date": date_str,
            **flight,
            **({"status": default_status} if default_status is not None else {}),
        }
        for flight in FLIGHTS_SCHEDULE
    ]


def get_flight_data_odpt(api_key):
    """Fetch ANA HND-to-HAC arrival outcomes without logging the secret URL."""
    print("ODPT APIから運航実績データを取得中...")
    params = {
        "odpt:operator": "odpt.Operator:ANA",
        "odpt:arrivalAirport": "odpt.Airport:HAC",
        "acl:consumerKey": api_key,
    }
    try:
        response = requests.get(
            "https://api.odpt.org/api/v4/odpt:FlightInformationArrival",
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        flights = response.json()
    except requests.RequestException as exc:
        raise _safe_request_error("ODPT API", exc) from None
    except ValueError as exc:
        raise CollectionError("ODPT APIのJSON応答を解釈できません。") from exc

    if not isinstance(flights, list):
        raise CollectionError("ODPT APIの応答構造が不正です。")

    result = []
    for flight in flights:
        if flight.get("odpt:originAirport") != "odpt.Airport:HND":
            continue
        raw_numbers = flight.get("odpt:flightNumber", [])
        raw_number = raw_numbers[0] if isinstance(raw_numbers, list) and raw_numbers else raw_numbers
        if not isinstance(raw_number, str):
            continue
        flight_number = raw_number.replace("NH", "ANA", 1) if raw_number.startswith("NH") else f"ANA{raw_number}"
        if flight_number not in SCHEDULE_BY_NUMBER:
            continue

        status_raw = flight.get("odpt:flightStatus")
        if status_raw not in STATUS_MAPPING:
            raise CollectionError(f"{flight_number}の運航ステータスが未対応です。")
        flight_date = flight.get("odpt:flightDate")
        if not flight_date:
            created_at = flight.get("dc:date", "")
            flight_date = created_at.split("T")[0] if created_at else datetime.now(JST).strftime("%Y-%m-%d")
        result.append(
            {
                "date": flight_date,
                "flight_number": flight_number,
                "scheduled_time": flight.get("odpt:scheduledArrivalTime", ""),
                "status": STATUS_MAPPING[status_raw],
            }
        )

    if not result:
        raise CollectionError("ODPT APIから対象3便を1件も取得できませんでした。")
    print(f"ODPT APIから {len(result)} 件の対象便を取得しました。")
    return result


def merge_with_daily_schedule(date_str, actual_flights):
    """Require one valid result for every configured flight before persisting."""
    actual_by_number = {}
    for flight in actual_flights:
        flight_number = flight.get("flight_number")
        if flight.get("date") != date_str or flight_number not in SCHEDULE_BY_NUMBER:
            continue
        if flight_number in actual_by_number:
            raise CollectionError(f"{flight_number}の運航情報が重複しています。")
        actual_by_number[flight_number] = flight

    missing = [number for number in SCHEDULE_BY_NUMBER if number not in actual_by_number]
    if missing:
        raise CollectionError(f"当日の運航情報が不足しています: {', '.join(missing)}")

    merged = []
    for scheduled in get_scheduled_flights(date_str):
        actual = actual_by_number[scheduled["flight_number"]]
        if actual.get("status") not in STORED_STATUSES:
            raise CollectionError(f"{scheduled['flight_number']}の運航ステータスが不正です。")
        merged.append(
            {
                **scheduled,
                "scheduled_time": actual.get("scheduled_time") or scheduled["scheduled_time"],
                "status": actual["status"],
            }
        )
    return merged


def get_demo_flight_data():
    today = datetime.now(JST).strftime("%Y-%m-%d")
    flights = get_scheduled_flights(today, default_status="運航")
    flights[1]["status"] = "運航(条件付)"
    return flights


def validate_collected_records(items):
    if len(items) != len(FLIGHTS_SCHEDULE):
        raise CollectionError(f"保存対象が{len(items)}件です。3便そろうまで保存しません。")
    for item in items:
        if item.get("status") not in STORED_STATUSES:
            raise CollectionError(f"{item.get('flight_number')}の運航ステータスが不正です。")
        missing = [field for field in REQUIRED_WEATHER_FIELDS if item.get(field) is None]
        if missing:
            raise CollectionError(f"{item.get('flight_number')}の気象データが欠測しています。")


def save_collected_data(flights_with_weather):
    validate_collected_records(flights_with_weather)
    saved_count = upsert_flight_weather_logs(flights_with_weather)
    print(f"BigQueryに {saved_count} 件のデータを保存・更新しました。")


def main():
    parser = argparse.ArgumentParser(description="羽田→八丈島便の運航・気象データ収集")
    parser.add_argument("--demo", action="store_true", help="保存せずにデモデータの収集処理だけを確認する")
    parser.add_argument(
        "--cleanup-only",
        action="store_true",
        help="BigQuery上の未取得・未対応ステータス行を削除して終了する",
    )
    args = parser.parse_args()

    if args.demo and args.cleanup_only:
        parser.error("--demo と --cleanup-only は同時に指定できません。")

    if args.cleanup_only:
        removed = delete_unresolved_status_rows()
        print(f"BigQueryから未取得・未対応ステータス {removed} 件を削除しました。")
        return

    api_key = os.getenv("ODPT_API_KEY")
    today = datetime.now(JST).strftime("%Y-%m-%d")

    if args.demo:
        flights = get_demo_flight_data()
    else:
        if not api_key or api_key == "your_odpt_api_key_here":
            raise RuntimeError("ODPT_API_KEYが未設定です。")
        removed = delete_unresolved_status_rows()
        if removed:
            print(f"BigQueryから未取得ステータス {removed} 件を削除しました。")
        flights = merge_with_daily_schedule(today, get_flight_data_odpt(api_key))

    completed = []
    for flight in flights:
        weather = get_weather_data(
            flight["date"],
            flight["scheduled_time"],
            target_hour=flight["target_hour"],
        )
        item = {**flight, **weather}
        if item["status"] in {"欠航", "条件付き→引返欠航"}:
            item["status_reason"] = item.get("status_reason") or UNKNOWN_REASON
        completed.append(item)

    validate_collected_records(completed)
    if args.demo:
        print("デモモードのためBigQueryへは保存しません。")
        return
    save_collected_data(completed)
    print("データ自動収集処理が完了しました。")


if __name__ == "__main__":
    main()
