from datetime import date, time

from backfill_bigquery_visibility import _date_chunks, nearest_hour


def test_date_chunks_cover_range_without_overlap():
    chunks = list(_date_chunks(date(2025, 12, 1), date(2026, 3, 1), days=60))

    assert chunks == [
        (date(2025, 12, 1), date(2026, 1, 29)),
        (date(2026, 1, 30), date(2026, 3, 1)),
    ]


def test_nearest_hour_rounds_arrival_time():
    assert nearest_hour(time(8, 29)) == 8
    assert nearest_hour(time(8, 30)) == 9
    assert nearest_hour(time(16, 40)) == 17

