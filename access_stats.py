import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import requests

from app_config import JST

GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"
DEFAULT_HOST = "toyo1621.github.io"
DEFAULT_PATH_PATTERN = "/8jo-flight-forecast-bot%"
ACCESS_STATS_DAYS = 7
DEFAULT_ACCESS_STATS_FILE = Path(__file__).resolve().parent / ".cache" / "access_stats.json"
UTC = timezone.utc
ACCESS_STATS_STATUSES = {"available", "stale", "unavailable"}

DAILY_PAGEVIEWS_QUERY = """
query DailyPageViews($accountTag: string, $start: Time, $end: Time, $host: string, $path: string) {
  viewer {
    accounts(filter: { accountTag: $accountTag }) {
      rumPageloadEventsAdaptiveGroups(
        filter: {
          datetime_geq: $start
          datetime_lt: $end
          requestHost: $host
          requestPath_like: $path
        }
        limit: 10000
        orderBy: [datetimeMinute_ASC]
      ) {
        count
        dimensions {
          datetimeMinute
        }
      }
    }
  }
}
"""


def _utc_window_for_jst_day(day):
    start = datetime.combine(day, time.min, tzinfo=JST).astimezone(UTC)
    end = start + timedelta(days=1)
    return start.isoformat(timespec="seconds").replace("+00:00", "Z"), end.isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


def _pageview_count(payload):
    if not isinstance(payload, dict):
        raise TypeError("Cloudflare Analyticsの応答形式が正しくありません。")
    if payload.get("errors"):
        raise ValueError("Cloudflare Analytics APIがクエリを拒否しました。")

    try:
        accounts = payload["data"]["viewer"]["accounts"]
    except (KeyError, TypeError):
        raise ValueError("Cloudflare Analyticsのデータが見つかりません。") from None
    if not isinstance(accounts, list) or len(accounts) != 1:
        raise ValueError("Cloudflare Analyticsのアカウント応答が正しくありません。")

    groups = accounts[0].get("rumPageloadEventsAdaptiveGroups", [])
    if not isinstance(groups, list):
        raise TypeError("Cloudflare Analyticsの日別データが正しくありません。")

    total = 0
    for group in groups:
        if not isinstance(group, dict):
            raise TypeError("Cloudflare Analyticsの集計行が正しくありません。")
        count = group.get("count", 0)
        if isinstance(count, bool) or not isinstance(count, (int, float)) or count < 0:
            raise ValueError("Cloudflare Analyticsのアクセス数が正しくありません。")
        total += int(count)
    return total


def fetch_daily_pageviews(
    day,
    account_id,
    api_token,
    host=DEFAULT_HOST,
    path_pattern=DEFAULT_PATH_PATTERN,
    session=None,
):
    start, end = _utc_window_for_jst_day(day)
    response = (session or requests).post(
        GRAPHQL_URL,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        json={
            "query": DAILY_PAGEVIEWS_QUERY,
            "variables": {
                "accountTag": account_id,
                "start": start,
                "end": end,
                "host": host,
                "path": path_pattern,
            },
        },
        timeout=20,
    )
    response.raise_for_status()
    return _pageview_count(response.json())


def _parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _date_label(day):
    return f"{day.month}/{day.day}"


def build_access_stats(
    today=None,
    days=ACCESS_STATS_DAYS,
    tracking_started_on=None,
    fetcher=fetch_daily_pageviews,
    **fetcher_kwargs,
):
    today = today or datetime.now(JST).date()
    if isinstance(today, datetime):
        today = today.date()
    if not isinstance(today, date) or days <= 0:
        raise ValueError("アクセス数の日付範囲が正しくありません。")

    started_on = _parse_date(tracking_started_on) if tracking_started_on else None
    result_days = []
    for offset in range(days - 1, -1, -1):
        current_day = today - timedelta(days=offset)
        pageviews = None
        if started_on is None or current_day >= started_on:
            pageviews = fetcher(current_day, **fetcher_kwargs)
        result_days.append(
            {
                "date": current_day.isoformat(),
                "label": _date_label(current_day),
                "pageviews": pageviews,
            }
        )
    return {
        "days": result_days,
        "generated_at": datetime.now(JST).isoformat(timespec="minutes"),
        "source": "Cloudflare Web Analytics",
        "status": "available",
        "status_reason": None,
    }


def _default_access_stats(status="unavailable", status_reason=None):
    return {
        "days": [],
        "generated_at": None,
        "source": None,
        "status": status,
        "status_reason": status_reason,
    }


def load_access_stats(path=None):
    path = Path(path) if path is not None else DEFAULT_ACCESS_STATS_FILE
    if not path.exists():
        return _default_access_stats()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_access_stats()
    if not isinstance(payload, dict) or not isinstance(payload.get("days"), list):
        return _default_access_stats()
    normalized = dict(payload)
    status = normalized.get("status")
    if status not in ACCESS_STATS_STATUSES:
        status = "available" if normalized["days"] else "unavailable"
    normalized["status"] = status
    normalized.setdefault("status_reason", None)
    return normalized


def write_access_stats(payload, path=None):
    path = Path(path) if path is not None else DEFAULT_ACCESS_STATS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def mark_access_stats_stale(path=None):
    """Keep the last successful counts visible while recording API failure."""
    payload = load_access_stats(path)
    if payload["days"]:
        payload["status"] = "stale"
        payload["status_reason"] = "cloudflare_unavailable"
        payload["stale_at"] = datetime.now(JST).isoformat(timespec="minutes")
    else:
        payload = _default_access_stats(
            status="unavailable", status_reason="cloudflare_unavailable"
        )
        payload["checked_at"] = datetime.now(JST).isoformat(timespec="minutes")
    write_access_stats(payload, path)
    return payload


def fetch_from_environment():
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.getenv("CLOUDFLARE_ANALYTICS_API_TOKEN")
    if not account_id or not api_token:
        raise ValueError("Cloudflare Analytics用の環境変数が設定されていません。")
    return build_access_stats(
        tracking_started_on=os.getenv("CLOUDFLARE_ANALYTICS_START_DATE"),
        account_id=account_id,
        api_token=api_token,
        host=os.getenv("CLOUDFLARE_ANALYTICS_HOST", DEFAULT_HOST),
        path_pattern=os.getenv("CLOUDFLARE_ANALYTICS_PATH", DEFAULT_PATH_PATTERN),
    )


if __name__ == "__main__":
    if sys.argv[1:] == ["--mark-stale"]:
        mark_access_stats_stale()
    elif sys.argv[1:]:
        raise SystemExit("usage: python access_stats.py [--mark-stale]")
    else:
        write_access_stats(fetch_from_environment())
