"""One strict candidate set for scoring and the ten-row explanation."""
import math

from flight_metadata import VALID_STORED_STATUSES, normalize_status

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


def select_history(history, flight_number, weather):
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
        if angle <= ANGLE_LIMIT and speed <= SPEED_LIMIT and gust <= GUST_LIMIT:
            candidates.append({**row, "status": status, "wind_differences": (angle, speed, gust)})
    candidates.sort(key=lambda row: str(row.get("date", "")), reverse=True)
    candidates.sort(key=lambda row: row["wind_differences"])
    return candidates
