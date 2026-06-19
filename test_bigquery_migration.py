import sqlite3
from datetime import datetime, timezone

from migrate_sqlite_to_bigquery import SCHEMA, normalize_row, read_sqlite_rows


BASE_SCHEMA = """CREATE TABLE flight_weather_logs (
    date TEXT, flight_number TEXT, scheduled_time TEXT,
    status TEXT, wind_direction REAL, wind_speed REAL,
    wind_gusts REAL, cloud_cover_low REAL, visibility REAL,
    created_at TEXT
)"""


def test_read_and_normalize_sqlite_rows(tmp_path):
    db_file = tmp_path / "flights.db"
    conn = sqlite3.connect(db_file)
    conn.execute(BASE_SCHEMA)
    conn.execute(
        "INSERT INTO flight_weather_logs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-06-18", "ANA1891", "08:30", "通常", 204.0, 7.44, 14.61, 69.0, 13.2, "2026-06-18 14:48:45"),
    )
    conn.commit()
    conn.close()

    rows = read_sqlite_rows(db_file)
    migrated_at = datetime(2026, 6, 19, tzinfo=timezone.utc)
    result = normalize_row(rows[0], migrated_at)

    assert result["date"] == "2026-06-18"
    assert result["flight_number"] == "ANA1891"
    assert result["flight_display_name"] == "ANA1891(1便)"
    assert result["scheduled_time"] == "08:30:00"
    assert result["visibility"] == 13.2
    assert result["status_reason"] is None
    assert result["migrated_at"] == "2026-06-19T00:00:00+00:00"
    assert "id" not in result
    assert "id" not in {field.name for field in SCHEMA}


def test_migration_treats_delay_as_normal(tmp_path):
    db_file = tmp_path / "flights.db"
    conn = sqlite3.connect(db_file)
    conn.execute(BASE_SCHEMA)
    conn.execute(
        "INSERT INTO flight_weather_logs VALUES ('2026-01-01', 'ANA1891', '08:30', '遅延', NULL, NULL, NULL, NULL, NULL, NULL)"
    )
    conn.commit()
    conn.close()

    result = normalize_row(read_sqlite_rows(db_file)[0])

    assert result["status"] == "通常"
    assert result["status_reason"] == "遅延"


def test_migration_preserves_status_reason(tmp_path):
    db_file = tmp_path / "flights.db"
    conn = sqlite3.connect(db_file)
    conn.execute(BASE_SCHEMA.replace("created_at TEXT", "created_at TEXT, status_reason TEXT"))
    conn.execute(
        "INSERT INTO flight_weather_logs VALUES ('2026-06-03', 'ANA1891', '08:30', '欠航', NULL, NULL, NULL, NULL, NULL, NULL, '台風')"
    )
    conn.commit()
    conn.close()

    result = normalize_row(read_sqlite_rows(db_file)[0])

    assert result["status"] == "欠航"
    assert result["status_reason"] == "台風"

