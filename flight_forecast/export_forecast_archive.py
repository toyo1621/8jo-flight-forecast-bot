"""Read the saved archive with BigQuery credentials and render it locally."""

import shutil
from pathlib import Path
from tempfile import mkdtemp

from flask import render_template

from flight_forecast.bigquery_storage import fetch_published_forecast_archive
from flight_forecast.forecast_archive import build_archive_days
from flight_forecast.web_app import BASE_DIR, app


def export_archive():
    days = build_archive_days(fetch_published_forecast_archive())
    # Keep private output out of dist and out of the repository.
    destination = Path(mkdtemp(prefix="hachijo-private-archive-"))
    index = destination / "history" / "index.html"
    with app.app_context():
        index.parent.mkdir()
        index.write_text(
            render_template("history.html", archive_days=days, private_archive=True),
            encoding="utf-8",
        )
        for day in days:
            detail = destination / "forecast" / day["date"] / "index.html"
            detail.parent.mkdir(parents=True)
            detail.write_text(
                render_template("archive_date.html", day=day, private_archive=True),
                encoding="utf-8",
            )
    static = destination / "static"
    static.mkdir()
    for name in ("styles.css", "history.js"):
        shutil.copyfile(BASE_DIR / "static" / name, static / name)
    return index


if __name__ == "__main__":
    print(export_archive().as_uri())
