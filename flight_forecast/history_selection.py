"""One strict candidate set for scoring and the ten-row explanation."""
import math

from flight_forecast.flight_metadata import VALID_STORED_STATUSES, normalize_status

ANGLE_LIMIT = 20.0
SPEED_LIMIT = 3.0
GUST_LIMIT = 5.0


def valid_number(value, maximum=None):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0
            and (maximum is None or value <= maximum))


def valid_wind(weather):
    return (valid_number(weather.get("wind_direction"), 360)
            and valid_number(weather.get("wind_speed"))
            and valid_number(weather.get("wind_gusts")))


def select_candidates(history, flight_number, weather, limits=(ANGLE_LIMIT, SPEED_LIMIT, GUST_LIMIT)):
    if not valid_wind(weather):
        return []
    candidates = []
    for row in history:
        if not isinstance(row, dict) or not valid_wind(row):
            continue
        if flight_number is not None and row.get("flight_number") != flight_number:
            continue
        status = normalize_status(row.get("status"))
        if status not in VALID_STORED_STATUSES:
            continue
        angle = abs(row["wind_direction"] - weather["wind_direction"]) % 360
        angle = min(angle, 360 - angle)
        speed = abs(row["wind_speed"] - weather["wind_speed"])
        gust = abs(row["wind_gusts"] - weather["wind_gusts"])
        if angle <= limits[0] and speed <= limits[1] and gust <= limits[2]:
            candidates.append({**row, "status": status, "wind_differences": (angle, speed, gust)})
    candidates.sort(key=lambda row: str(row.get("date", "")), reverse=True)
    candidates.sort(key=lambda row: row["wind_differences"])
    return candidates


def select_history_with_metadata(history, flight_number, weather):
    history = list(history)
    stages = ((20, 3, 5), (20, 5, 8), (30, 5, 8), (45, 5, 8))
    for index, limits in enumerate(stages):
        rows = select_candidates(history, flight_number, weather, limits)
        required = 5 if index == 0 else 6
        if len(rows) >= required or not valid_wind(weather):
            break
    return rows, {
        "step": index + 1, "minimum": required, "expanded": index > 0,
        "angle": limits[0], "speed": limits[1], "gust": limits[2],
        "label": f"風向差±{limits[0]}°・平均風速差±{limits[1]}m/s・最大瞬間風速差±{limits[2]}m/s",
    }


def select_history(history, flight_number, weather):
    return select_history_with_metadata(history, flight_number, weather)[0]
