from datetime import timedelta, timezone


JST = timezone(timedelta(hours=9))

HACHIJO_AIRPORT_LATITUDE = 33.115
HACHIJO_AIRPORT_LONGITUDE = 139.782

FORECAST_DAYS = 11
MAIN_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_FORECAST_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
JMA_MODEL_NAME = "jma_seamless"

FLIGHTS = (
    {"number": "ANA1891", "time": "08:30", "forecast_hour": 8},
    {"number": "ANA1893", "time": "13:10", "forecast_hour": 13},
    {"number": "ANA1895", "time": "16:40", "forecast_hour": 17},
)

MAX_PROBABILITY = 97.0
LOW_PROBABILITY_THRESHOLD = 60.0
MODEL_DIFFERENCE_WARNING_POINTS = 20.0

VISIBILITY_RISK_KM = 5.0
VISIBILITY_PROBABILITY_MULTIPLIER = 0.6
LOW_CLOUD_RISK_PERCENT = 90.0
LOW_CLOUD_PROBABILITY_MULTIPLIER = 0.9
GUST_RISK_MS = 15.0
STRONG_WIND_RISK_MS = 10.0
WIND_PROBABILITY_MULTIPLIER = 0.9
SOUTHERLY_WIND_MIN_DEGREES = 120.0
SOUTHERLY_WIND_MAX_DEGREES = 240.0
SOUTHERLY_CAUTION_WIND_MS = 9.0

INITIAL_MATCH_ANGLE_DEGREES = 30.0
INITIAL_MATCH_WIND_SPEED_MS = 3.0
FALLBACK_MATCH_ANGLE_DEGREES = 45.0
FALLBACK_MATCH_WIND_SPEED_MS = 5.0
MIN_MATCHING_HISTORY_ROWS = 5

CONFIDENCE_GRADES = (
    (10.0, "A", "10ポイント以内"),
    (20.0, "B", "20ポイント以内"),
    (30.0, "C", "30ポイント以内"),
    (40.0, "D", "40ポイント以内"),
)

PROBABILITY_SYMBOL_THRESHOLDS = (
    (95.0, "◎"),
    (75.0, "〇"),
    (35.0, "△"),
)


def probability_symbol(value):
    for threshold, symbol in PROBABILITY_SYMBOL_THRESHOLDS:
        if value >= threshold:
            return symbol
    return "×"
