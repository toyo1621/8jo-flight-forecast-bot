from datetime import date, datetime, timedelta, timezone

from collection_monitor import (
    aggregate_collection_runs,
    coverage_summary,
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


def test_coverage_summary_reports_last_success_consecutive_gap_and_latest_run():
    records = [
        {
            "target_date": "2026-08-22",
            "status": "succeeded",
            "rows_written": 3,
            "run_id": "run-old",
            "attempt": 1,
            "started_at": "2026-08-22T12:00:00+00:00",
        },
        {
            "target_date": "2026-08-24",
            "status": "failed",
            "rows_written": None,
            "run_id": "run-latest",
            "attempt": 2,
            "started_at": "2026-08-24T12:00:00+00:00",
        },
    ]

    summary = coverage_summary(records, today=date(2026, 8, 24), days=3)

    assert summary["last_success_date"] == "2026-08-22"
    assert summary["consecutive_missing_days"] == 2
    assert summary["latest_run"]["run_id"] == "run-latest"


def test_collection_monitor_collapses_started_and_succeeded_without_input_order():
    records = [
        {
            "run_id": "run-1",
            "attempt": 1,
            "target_date": "2026-08-24",
            "status": "succeeded",
            "rows_written": 3,
            "started_at": "2026-08-24T00:00:00+00:00",
            "completed_at": "2026-08-24T00:05:00+00:00",
            "created_at": "2026-08-24T00:05:01+00:00",
        },
        {
            "run_id": "run-1",
            "attempt": 1,
            "target_date": "2026-08-24",
            "status": "started",
            "rows_written": None,
            "started_at": "2026-08-24T00:00:00+00:00",
            "created_at": "2026-08-24T00:00:01+00:00",
        },
    ]

    states = aggregate_collection_runs(list(reversed(records)))

    assert states[date(2026, 8, 24)]["state"] == "succeeded"
    assert states[date(2026, 8, 24)]["final_run"]["status"] == "succeeded"


def test_collection_monitor_tie_break_is_stable_without_input_order():
    records = [
        {
            "run_id": "run-1",
            "attempt": 1,
            "target_date": "2026-08-24",
            "status": "failed",
            "rows_written": 0,
            "started_at": "2026-08-24T00:00:00+00:00",
            "completed_at": "2026-08-24T00:05:00+00:00",
        },
        {
            "run_id": "run-1",
            "attempt": 1,
            "target_date": "2026-08-24",
            "status": "succeeded",
            "rows_written": 3,
            "started_at": "2026-08-24T00:00:00+00:00",
            "completed_at": "2026-08-24T00:05:00+00:00",
        },
    ]

    forward = aggregate_collection_runs(records)
    reverse = aggregate_collection_runs(list(reversed(records)))
    assert forward[date(2026, 8, 24)]["state"] == reverse[date(2026, 8, 24)]["state"]


def test_collection_monitor_separates_missing_success_record_from_missing_data():
    target = date(2026, 8, 24)
    states = aggregate_collection_runs(
        [], observed_flight_counts={target.isoformat(): 3}
    )
    assert states == {}

    states = aggregate_collection_runs(
        [
            {
                "run_id": "run-1",
                "attempt": 1,
                "target_date": target.isoformat(),
                "status": "succeeded",
                "rows_written": 3,
            }
        ],
        observed_flight_counts={target.isoformat(): 2},
    )
    assert states[target]["state"] == "data_missing"

    summary = coverage_summary(
        [], today=target, days=1, observed_flight_counts={target.isoformat(): 3}
    )
    assert summary["collection_states"][target.isoformat()] == "success_record_missing"


def test_collection_monitor_does_not_fail_before_due_time_or_before_monitoring_start():
    target = date(2026, 8, 24)
    before_due = coverage_summary(
        [],
        today=target,
        days=1,
        current_time=datetime(2026, 8, 24, 20, 59, tzinfo=timezone(timedelta(hours=9))),
    )
    assert before_due["collection_states"][target.isoformat()] == "not_due"
    assert before_due["consecutive_missing_days"] == 0
    assert find_missing_collection_days(
        [],
        today=target,
        days=1,
        current_time=datetime(2026, 8, 25, 10, 0, tzinfo=timezone(timedelta(hours=9))),
        monitoring_start_date="2026-08-25",
    ) == []


def test_one_run_tracks_two_dates_independently():
    records = [{'run_id': 'one', 'attempt': 1, 'target_date': day, 'status': 'partial', 'rows_written': 0}
               for day in ['2026-09-07', '2026-09-08']]
    states = aggregate_collection_runs(records, observed_flight_counts={'2026-09-07': 3, '2026-09-08': 1})
    assert states[date(2026, 9, 7)]['state'] == 'succeeded'
    assert states[date(2026, 9, 8)]['state'] == 'data_incomplete'


def test_partial_before_due_does_not_hide_actual_run_failure():
    now = datetime(2026, 9, 8, 14, tzinfo=timezone(timedelta(hours=9)))
    for status, expected in [('partial', 'not_due'), ('failed', 'run_failed')]:
        result = coverage_summary([{'run_id': 'x', 'target_date': '2026-09-08', 'status': status}],
                                  today=now.date(), days=1, current_time=now)
        assert result['collection_states']['2026-09-08'] == expected


def test_existing_results_do_not_hide_failed_fetch():
    states = aggregate_collection_runs([
        {'run_id': 'x', 'target_date': '2026-09-08', 'status': 'failed'}
    ], observed_flight_counts={'2026-09-08': 3})
    assert states[date(2026, 9, 8)]['state'] == 'run_failed'
