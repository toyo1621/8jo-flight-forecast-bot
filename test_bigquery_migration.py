import sqlite3
from datetime import datetime, timezone

from migrate_sqlite_to_bigquery import normalize_row, read_sqlite_rows


def test_read_and_normalize_sqlite_rows(tmp_path):
    db_file = tmp_path / "flights.db"
    conn = sqlite3.connect(db_file)
    conn.execute(
        """
        CREATE TABLE flight_weather_logs (
            id INTEGER, date TEXT, flight_number TEXT, scheduled_time TEXT,
            status TEXT, wind_direction REAL, wind_speed REAL,
            wind_gusts REAL, cloud_cover_low REAL, visibility REAL,
            created_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO flight_weather_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "2026-06-18", "ANA1891", "08:30", "通常", 204.0, 7.44, 14.61, 69.0, 13.2, "2026-06-18 14:48:45"),
    )
    conn.commit()
    conn.close()

    rows = read_sqlite_rows(db_file)
    migrated_at = datetime(2026, 6, 19, tzinfo=timezone.utc)
    result = normalize_row(rows[0], migrated_at)

    assert result["date"] == "2026-06-18"
    assert result["flight_number"] == "ANA1891"
    assert result["scheduled_time"] == "08:30:00"
    assert result["visibility"] == 13.2
    assert result["status_reason"] is None
    assert result["migrated_at"] == "2026-06-19T00:00:00+00:00"
