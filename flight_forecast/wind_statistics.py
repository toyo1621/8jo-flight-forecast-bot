"""Aggregate historical outcomes by wind direction and strength."""

import math

from flight_forecast.flight_metadata import (
    NON_OPERATED_STATUSES,
    VALID_STORED_STATUSES,
    normalize_status,
)

DIRECTION_LABELS = (
    "北",
    "北北東",
    "北東",
    "東北東",
    "東",
    "東南東",
    "南東",
    "南南東",
    "南",
    "南南西",
    "南西",
    "西南西",
    "西",
    "西北西",
    "北西",
    "北北西",
)

SPEED_BANDS = (
    ("under-4", "4m/s未満", 0.0, 4.0),
    ("4-to-6-5", "4〜6.5m/s未満", 4.0, 6.5),
    ("6-5-to-9", "6.5〜9m/s未満", 6.5, 9.0),
    ("9-plus", "9m/s以上", 9.0, None),
)

GUST_BANDS = (
    ("under-10", "10m/s未満", 0.0, 10.0),
    ("10-to-15", "10〜15m/s未満", 10.0, 15.0),
    ("15-to-20", "15〜20m/s未満", 15.0, 20.0),
    ("20-plus", "20m/s以上", 20.0, None),
)

MINIMUM_PUBLIC_SAMPLE = 5
LIMITED_SAMPLE = 20


def _valid_number(value, maximum=None):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        and (maximum is None or value <= maximum)
    )


def direction_index(degrees):
    if not _valid_number(degrees, 360):
        return None
    return int(((float(degrees) % 360) + 11.25) % 360 // 22.5)


def _band_index(value, bands):
    if not _valid_number(value):
        return None
    for index, (_, _, minimum, maximum) in enumerate(bands):
        if value >= minimum and (maximum is None or value < maximum):
            return index
    return None


def _empty_cells(bands):
    return [
        {
            "key": key,
            "label": label,
            "sample_count": 0,
            "cancelled_count": 0,
        }
        for key, label, _, _ in bands
    ]


def _finalize_cell(cell, minimum_sample):
    sample_count = cell["sample_count"]
    cancelled_count = cell["cancelled_count"]
    cell["rate"] = (
        round(cancelled_count * 100 / sample_count, 1) if sample_count else None
    )
    if sample_count < minimum_sample:
        cell["evidence"] = "insufficient"
        cell["tone"] = "insufficient"
    elif sample_count < LIMITED_SAMPLE:
        cell["evidence"] = "limited"
        cell["tone"] = _rate_tone(cell["rate"])
    else:
        cell["evidence"] = "available"
        cell["tone"] = _rate_tone(cell["rate"])
    return cell


def _rate_tone(rate):
    if rate >= 30:
        return "high"
    if rate >= 10:
        return "medium"
    return "low"


def _degrees_label(value):
    return f"{int(value)}" if value.is_integer() else f"{value:.1f}"


def build_wind_cancellation_summary(history, minimum_sample=MINIMUM_PUBLIC_SAMPLE):
    """Return transparent all-cause cancellation ratios for static display."""
    rows = [
        {
            "label": label,
            "center_degrees": index * 22.5,
            "center_degrees_label": _degrees_label(index * 22.5),
            "sample_count": 0,
            "cancelled_count": 0,
            "speed_cells": _empty_cells(SPEED_BANDS),
            "gust_cells": _empty_cells(GUST_BANDS),
        }
        for index, label in enumerate(DIRECTION_LABELS)
    ]
    included_dates = []
    included_count = 0
    cancelled_count = 0
    weather_reason_count = 0

    for item in history:
        if not isinstance(item, dict):
            continue
        status = normalize_status(item.get("status"))
        direction = direction_index(item.get("wind_direction"))
        speed_band = _band_index(item.get("wind_speed"), SPEED_BANDS)
        gust_band = _band_index(item.get("wind_gusts"), GUST_BANDS)
        if (
            status not in VALID_STORED_STATUSES
            or direction is None
            or speed_band is None
            or gust_band is None
        ):
            continue

        cancelled = status in NON_OPERATED_STATUSES
        row = rows[direction]
        row["sample_count"] += 1
        row["cancelled_count"] += int(cancelled)
        for cell_index, key in ((speed_band, "speed_cells"), (gust_band, "gust_cells")):
            row[key][cell_index]["sample_count"] += 1
            row[key][cell_index]["cancelled_count"] += int(cancelled)
        included_count += 1
        cancelled_count += int(cancelled)
        weather_reason_count += int(
            cancelled and item.get("status_reason_category") == "weather"
        )
        if item.get("date"):
            included_dates.append(str(item["date"])[:10])

    for row in rows:
        row["rate"] = (
            round(row["cancelled_count"] * 100 / row["sample_count"], 1)
            if row["sample_count"]
            else None
        )
        row["speed_cells"] = [
            _finalize_cell(cell, minimum_sample) for cell in row["speed_cells"]
        ]
        row["gust_cells"] = [
            _finalize_cell(cell, minimum_sample) for cell in row["gust_cells"]
        ]

    return {
        "rows": rows,
        "speed_bands": [label for _, label, _, _ in SPEED_BANDS],
        "gust_bands": [label for _, label, _, _ in GUST_BANDS],
        "sample_count": included_count,
        "cancelled_count": cancelled_count,
        "operated_count": included_count - cancelled_count,
        "weather_reason_count": weather_reason_count,
        "first_date": min(included_dates) if included_dates else None,
        "last_date": max(included_dates) if included_dates else None,
        "minimum_sample": minimum_sample,
    }
