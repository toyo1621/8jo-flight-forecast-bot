import sqlite3

from backfill_missing_weather import (
    LEGACY_VISIBILITY_SOURCE,
    UNKNOWN_REASON,
    ensure_columns,
    fill_legacy_visibility_sources,
    mark_unconfirmed_cancellation_reasons,
    nearest_hour,
)


def test_nearest_hour_accepts_seconds():
    assert nearest_hour("08:29:00") == 8
    assert nearest_hour("08:30:00") == 9
    assert nearest_hour("16:40") == 17


def test_quality_backfill_marks_sources_and_unconfirmed_reasons(tmp_path):
    db_file = tmp_path / "flights.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        """
        CREATE TABLE flight_weather_logs (
            date TEXT NOT NULL,
            flight_number TEXT NOT NULL,
            scheduled_time TEXT,
            status TEXT,
            wind_direction REAL,
            wind_speed REAL,
            wind_gusts REAL,
            cloud_cover_low REAL,
            visibility REAL,
            status_reason TEXT,
            UNIQUE(date, flight_number)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO flight_weather_logs
        (date, flight_number, scheduled_time, status, visibility)
        VALUES ('2026-06-20', 'ANA1891', '08:30', '欠航', 10.0)
        """
    )

    ensure_columns(conn)
    source_updates = fill_legacy_visibility_sources(conn)
    reason_updates = mark_unconfirmed_cancellation_reasons(conn)

    row = conn.execute(
        "SELECT visibility_source, status_reason FROM flight_weather_logs"
    ).fetchone()
    conn.close()

    assert source_updates == 1
    assert reason_updates == 1
    assert row == (LEGACY_VISIBILITY_SOURCE, UNKNOWN_REASON)
