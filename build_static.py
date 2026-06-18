import shutil
from datetime import datetime
from pathlib import Path

import requests
from flask import render_template

from db_snapshot import restore_db
from web_app import (
    BASE_DIR,
    JST,
    app,
    build_daily_forecasts,
    fetch_ensemble_forecast,
    fetch_forecast,
)


DIST_DIR = BASE_DIR / "dist"


def build_site(output_dir=DIST_DIR):
    restore_db(BASE_DIR / "flights.db", BASE_DIR / "data" / "flights_dump.sql")
    weather = fetch_forecast()
    try:
        ensembles = fetch_ensemble_forecast()
    except (requests.RequestException, ValueError) as exc:
        print(f"Ensemble forecast unavailable; using lead-time confidence: {exc}")
        ensembles = {}

    days = build_daily_forecasts(weather, ensembles)
    updated_at = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
    with app.app_context():
        html = render_template(
            "index.html",
            days=days,
            error=None,
            updated_at=updated_at,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / ".nojekyll").touch()
    (output_dir / "index.html").write_text(html, encoding="utf-8")
    shutil.copytree(BASE_DIR / "static", output_dir / "static", dirs_exist_ok=True)
    print(f"Built {output_dir / 'index.html'} with {len(days)} forecast days.")


if __name__ == "__main__":
    build_site()
