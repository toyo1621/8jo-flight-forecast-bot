import sqlite3

from db_snapshot import export_dump, restore_db


def test_exported_snapshot_restores_without_internal_sequence_table(tmp_path):
    source = tmp_path / "source.db"
    dump = tmp_path / "snapshot.sql"
    restored = tmp_path / "restored.db"
    conn = sqlite3.connect(source)
    conn.execute(
        "CREATE TABLE records (date TEXT, flight_number TEXT, UNIQUE(date, flight_number))"
    )
    conn.execute("INSERT INTO records VALUES ('2026-06-19', 'ANA1891')")
    conn.commit()
    conn.close()

    export_dump(source, dump)
    restore_db(restored, dump)

    sql = dump.read_text(encoding="utf-8")
    assert "sqlite_sequence" not in sql
    conn = sqlite3.connect(restored)
    assert conn.execute("SELECT * FROM records").fetchall() == [("2026-06-19", "ANA1891")]
    conn.close()

