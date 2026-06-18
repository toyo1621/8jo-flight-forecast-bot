import argparse
import sqlite3
from pathlib import Path

DB_FILE = Path("flights.db")
DUMP_FILE = Path("data/flights_dump.sql")


def restore_db(db_file=DB_FILE, dump_file=DUMP_FILE):
    """Restore flights.db from the text SQL dump when the DB file is missing."""
    if db_file.exists():
        print(f"{db_file} already exists. Restore skipped.")
        return

    if not dump_file.exists():
        print(f"{dump_file} does not exist. Restore skipped.")
        return

    db_file.parent.mkdir(parents=True, exist_ok=True)
    sql = dump_file.read_text(encoding="utf-8")
    conn = sqlite3.connect(db_file)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()
    print(f"Restored {db_file} from {dump_file}.")


def export_dump(db_file=DB_FILE, dump_file=DUMP_FILE):
    """Export flights.db to a text SQL dump that can be reviewed in PRs."""
    if not db_file.exists():
        raise FileNotFoundError(f"{db_file} does not exist; nothing to export.")

    dump_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    try:
        dump_file.write_text("\n".join(conn.iterdump()) + "\n", encoding="utf-8")
    finally:
        conn.close()
    print(f"Exported {db_file} to {dump_file}.")


def main():
    parser = argparse.ArgumentParser(description="Restore/export the SQLite database snapshot as reviewable SQL text.")
    parser.add_argument("command", choices=("restore", "export"))
    args = parser.parse_args()

    if args.command == "restore":
        restore_db()
    else:
        export_dump()


if __name__ == "__main__":
    main()
