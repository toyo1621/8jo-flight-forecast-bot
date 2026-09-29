"""Summarize stored weather conditions alongside confirmed flight outcomes."""

import math

from flight_forecast.flight_metadata import (
    NON_OPERATED_STATUSES,
    VALID_STORED_STATUSES,
    normalize_status,
)
from flight_forecast.wind_statistics import (
    LIMITED_SAMPLE,
    MINIMUM_PUBLIC_SAMPLE,
    _finalize_cell,
)

METRICS = (
    (
        "visibility",
        "視程",
        "km",
        None,
        (
            ("under-1", "1km未満", 0.0, 1.0),
            ("1-to-2", "1〜2km未満", 1.0, 2.0),
            ("2-to-3", "2〜3km未満", 2.0, 3.0),
            ("3-to-5", "3〜5km未満", 3.0, 5.0),
            ("5-plus", "5km以上", 5.0, None),
        ),
    ),
    (
        "cloud_cover_low",
        "低層雲量",
        "%",
        100.0,
        (
            ("under-25", "25%未満", 0.0, 25.0),
            ("25-to-50", "25〜50%未満", 25.0, 50.0),
            ("50-to-75", "50〜75%未満", 50.0, 75.0),
            ("75-plus", "75%以上", 75.0, None),
        ),
    ),
    (
        "precipitation",
        "降水量",
        "mm/h",
        None,
        (
            ("zero", "0mm/h", 0.0, 0.0),
            ("under-1-5", "0超〜1.5mm/h未満", 0.0, 1.5),
            ("1-5-to-6", "1.5〜6mm/h未満", 1.5, 6.0),
            ("6-plus", "6mm/h以上", 6.0, None),
        ),
    ),
)


def _band_index(value, bands):
    if value == 0 and bands[0][0] == "zero":
        return 0
    for index, (_, _, minimum, maximum) in enumerate(bands):
        if value >= minimum and (maximum is None or value < maximum):
            return index
    return None


def build_visibility_cancellation_summary(
    history, minimum_sample=MINIMUM_PUBLIC_SAMPLE
):
    """Count all-cause disruptions for each metric's own valid sample set."""
    metrics = []
    for key, label, unit, maximum, bands in METRICS:
        metrics.append({
            "key": key,
            "label": label,
            "unit": unit,
            "bands": [
                {"key": band_key, "label": band_label,
                 "sample_count": 0, "cancelled_count": 0}
                for band_key, band_label, _, _ in bands
            ],
            "sample_count": 0,
            "cancelled_count": 0,
            "dates": [],
        })

    confirmed_count = 0
    for item in history:
        if not isinstance(item, dict):
            continue
        if item.get("flight_number") not in {"ANA1891", "ANA1893", "ANA1895"}:
            continue
        status = normalize_status(item.get("status"))
        if status not in VALID_STORED_STATUSES:
            continue
        confirmed_count += 1
        cancelled = status in NON_OPERATED_STATUSES
        for metric, (_, _, _, maximum, bands) in zip(metrics, METRICS):
            value = item.get(metric["key"])
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            try:
                number = float(value)
            except OverflowError:
                continue
            if not math.isfinite(number) or number < 0 or (
                maximum is not None and number > maximum
            ):
                continue
            band_index = _band_index(number, bands)
            if band_index is None:
                continue
            metric["sample_count"] += 1
            metric["cancelled_count"] += int(cancelled)
            cell = metric["bands"][band_index]
            cell["sample_count"] += 1
            cell["cancelled_count"] += int(cancelled)
            if item.get("date"):
                metric["dates"].append(str(item["date"])[:10])

    for metric in metrics:
        metric["bands"] = [
            _finalize_cell(cell, minimum_sample) for cell in metric["bands"]
        ]
        metric["excluded_count"] = confirmed_count - metric["sample_count"]
        metric["first_date"] = min(metric["dates"]) if metric["dates"] else None
        metric["last_date"] = max(metric["dates"]) if metric["dates"] else None
        metric.pop("dates")

    dates = [metric["last_date"] for metric in metrics if metric["last_date"]]
    return {
        "metrics": metrics,
        "confirmed_count": confirmed_count,
        "minimum_sample": minimum_sample,
        "limited_sample": LIMITED_SAMPLE,
        "last_date": max(dates) if dates else None,
    }
