from datetime import date

from collection_monitor import (
    expected_collection_dates,
    find_missing_collection_days,
    format_report,
)


def test_missing_collection_days_uses_only_completed_three_flight_runs():
    records = [
        {"target_date": "2026-08-24", "status": "succeeded", "rows_written": 3},
        {"target_date": "2026-08-23", "status": "failed", "rows_written": None},
        {"target_date": "2026-08-22", "status": "succeeded", "rows_written": 2},
    ]

    missing = find_missing_collection_days(
        records, today=date(2026, 8, 24), days=3
    )

    assert missing == [date(2026, 8, 22), date(2026, 8, 23)]


def test_collection_coverage_report_explains_raw_replay_for_missing_days():
    missing = [date(2026, 8, 23)]

    report = format_report(missing, today=date(2026, 8, 24), days=2)

    assert "2026-08-23" in report
    assert "--replay-run-id" in report


def test_expected_collection_dates_are_jst_calendar_days():
    assert expected_collection_dates(date(2026, 8, 24), days=3) == [
        date(2026, 8, 22),
        date(2026, 8, 23),
        date(2026, 8, 24),
    ]
