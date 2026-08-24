import json
from datetime import date
from unittest.mock import Mock

import pytest

from access_stats import (
    DAILY_PAGEVIEWS_QUERY,
    _utc_window_for_jst_day,
    build_access_stats,
    fetch_daily_pageviews,
    load_access_stats,
)


def test_utc_window_for_jst_day_is_exactly_one_day():
    start, end = _utc_window_for_jst_day(date(2026, 8, 24))

    assert start == "2026-08-23T15:00:00Z"
    assert end == "2026-08-24T15:00:00Z"


def test_fetch_daily_pageviews_sums_cloudflare_minute_groups():
    response = Mock()
    response.json.return_value = {
        "data": {
            "viewer": {
                "accounts": [
                    {
                        "rumPageloadEventsAdaptiveGroups": [
                            {"count": 2, "dimensions": {"datetimeMinute": "2026-08-23T15:00:00Z"}},
                            {"count": 3, "dimensions": {"datetimeMinute": "2026-08-23T15:01:00Z"}},
                        ]
                    }
                ]
            }
        },
        "errors": None,
    }
    session = Mock()
    session.post.return_value = response

    result = fetch_daily_pageviews(
        date(2026, 8, 24),
        account_id="account-id",
        api_token="token",
        session=session,
    )

    assert result == 5
    response.raise_for_status.assert_called_once()
    request = session.post.call_args.kwargs["json"]
    assert request["query"] == DAILY_PAGEVIEWS_QUERY
    assert request["variables"]["start"] == "2026-08-23T15:00:00Z"
    assert request["variables"]["end"] == "2026-08-24T15:00:00Z"


def test_fetch_daily_pageviews_rejects_graphql_errors():
    response = Mock()
    response.json.return_value = {"errors": [{"message": "unauthorized"}]}
    session = Mock()
    session.post.return_value = response

    with pytest.raises(ValueError, match="クエリを拒否"):
        fetch_daily_pageviews(
            date(2026, 8, 24),
            account_id="account-id",
            api_token="token",
            session=session,
        )


def test_build_access_stats_marks_pre_tracking_days_unmeasured():
    requested_days = []

    def fake_fetcher(day, **kwargs):
        requested_days.append(day)
        return 7

    result = build_access_stats(
        today=date(2026, 8, 24),
        tracking_started_on="2026-08-24",
        fetcher=fake_fetcher,
    )

    assert [item["pageviews"] for item in result["days"]] == [None, None, None, None, None, None, 7]
    assert requested_days == [date(2026, 8, 24)]


def test_load_access_stats_returns_default_for_invalid_json(tmp_path):
    path = tmp_path / "access_stats.json"
    path.write_text(json.dumps({"days": "invalid"}), encoding="utf-8")

    assert load_access_stats(path) == {"days": [], "generated_at": None, "source": None}
