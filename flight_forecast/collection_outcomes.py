"""Pure, conservative classification of ODPT arrival observations."""

from datetime import date, datetime, timedelta

from flight_forecast.app_config import JST

FINAL_STATUSES = {
    "odpt.FlightStatus:Arrived": "運航",
    "odpt.FlightStatus:Cancelled": "欠航",
    "odpt.FlightStatus:Diverted": "条件付き→引返欠航",
    "odpt.FlightStatus:Returned": "条件付き→引返欠航",
}
PENDING_STATUSES = {
    "odpt.FlightStatus:Normal", "odpt.FlightStatus:Delayed",
    "odpt.FlightStatus:Conditional", "odpt.FlightStatus:EstimatedArrival",
}


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(JST) if parsed.tzinfo else None
    except ValueError:
        return None


def resolve_date(flight, fetched_at):
    """Never relabel a stale response as today's flight or infer across midnight."""
    explicit = flight.get("odpt:flightDate")
    if explicit:
        try:
            day = date.fromisoformat(explicit)
        except (ValueError, TypeError):
            return None, "invalid_date"
        if day.isoformat() != explicit:
            return None, "invalid_date"
        return (day.isoformat(), "explicit") if day <= fetched_at.date() else (None, "future_date")
    updated = timestamp(flight.get("dc:date"))
    valid = timestamp(flight.get("dct:valid"))
    # The live feed has HH:MM times but no service date. Only accept its
    # same-day validity window; overnight carry-over requires explicit evidence.
    if (updated and valid and updated <= fetched_at <= valid
            and fetched_at - updated <= timedelta(hours=2)
            and updated.date() == valid.date() == fetched_at.date()
            and updated.hour >= 6):
        return updated.date().isoformat(), "same_day_validity_window"
    return None, "ambiguous_date"


def classify(flight, fetched_at):
    day, basis = resolve_date(flight, fetched_at)
    updated = timestamp(flight.get("dc:date"))
    raw = flight.get("odpt:flightStatus")
    result = {"date": day, "outcome_date_basis": basis, "outcome_raw_status": raw,
              "outcome_source": "odpt", "outcome_observed_at": updated.isoformat() if updated else None,
              "outcome_state": "pending", "status": None}
    if not day or not updated or updated > fetched_at or not isinstance(raw, str):
        result["outcome_state"] = "invalid"
        return result
    actual = flight.get("odpt:actualArrivalTime")
    arrived = False
    if actual:
        try:
            actual_at = datetime.combine(date.fromisoformat(day), datetime.strptime(actual, "%H:%M").replace(tzinfo=JST).time(), JST)
            arrived = actual_at <= updated
        except (ValueError, TypeError):
            result["outcome_state"] = "invalid"
            return result
        if not arrived or raw in {"odpt.FlightStatus:Cancelled", "odpt.FlightStatus:Returned", "odpt.FlightStatus:Diverted"}:
            result["outcome_state"] = "conflict"
            return result
    if raw in FINAL_STATUSES:
        result.update(status=FINAL_STATUSES[raw], outcome_state="confirmed")
    elif raw in PENDING_STATUSES and arrived:
        result.update(status="運航(条件付)" if raw.endswith(":Conditional") else "運航", outcome_state="confirmed")
    elif raw not in PENDING_STATUSES:
        result["outcome_state"] = "invalid"
    return result


def can_replace(old, new):
    """Mirror the BigQuery MERGE guard for offline policy tests."""
    if old.get("outcome_locked"):
        return False
    if new.get("outcome_state") != "confirmed":
        return old.get("outcome_state") is None and new.get("outcome_state") is None
    new_time = timestamp(new.get("outcome_observed_at"))
    old_time = timestamp(old.get("outcome_observed_at"))
    enrichment = new_time == old_time and new.get('status') == old.get('status') and any(
        old.get(field) is None and new.get(field) is not None
        for field in ('wind_direction', 'wind_speed', 'wind_gusts', 'cloud_cover_low', 'visibility'))
    return new_time is not None and (old_time is None or new_time > old_time or enrichment)
