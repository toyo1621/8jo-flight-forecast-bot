"""Offline comparison: exported BigQuery JSON only, no writes to production."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from forecast_engine import find_similar_flights, predict_flight_probability
from history_selection import valid_wind


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("history")
    parser.add_argument("snapshots")
    args = parser.parse_args()
    history = json.loads(Path(args.history).read_text())
    for row in history:
        for key in ("wind_direction", "wind_speed", "wind_gusts", "visibility", "cloud_cover_low"):
            if row.get(key) is not None:
                row[key] = float(row[key])
    rows = json.loads(Path(args.snapshots).read_text())
    print(json.dumps({"history_rows": len(history), "valid_wind_rows": sum(valid_wind(r) for r in history)}, ensure_ascii=False))
    for snapshot in rows:
        number = snapshot["flight_number"]
        inputs = json.loads(snapshot["weather_json"])
        weather = inputs.get("weather", inputs)
        kwargs = {key: weather.get(key) for key in (
            "wind_direction", "wind_speed", "wind_gusts", "visibility", "cloud_cover_low", "precipitation")}
        result = predict_flight_probability(**kwargs, flight_number=number, history=history)
        similar = find_similar_flights(number, weather, history=history)
        typhoon = float(snapshot["typhoon_factor"]) if snapshot.get("typhoon_factor") is not None else 1.0
        score = result["probability"]
        def jst(value):
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=9))).isoformat()
        print(json.dumps({"flight": number, "target_jst": jst(snapshot["weather_valid_at"]),
            "generated_jst": jst(snapshot["prediction_generated_at"]), "weather": kwargs,
            "published_score": snapshot["probability"], "new_score": round(score * typhoon, 1) if score is not None else None,
            "base": result["base_probability"], "count": result["data_count"],
            "status": result["calculation_status"], "factors": result["weather_factors"],
            "typhoon": typhoon, "similar_dates": [r["date"] for r in similar]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
