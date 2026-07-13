import shutil
import os
from datetime import datetime
from pathlib import Path

from flask import render_template

from app_config import LOW_PROBABILITY_THRESHOLD
from db_snapshot import restore_db
from web_app import (
    BASE_DIR,
    JST,
    app,
    build_daily_forecasts,
    load_forecast_bundle,
)


DIST_DIR = BASE_DIR / "dist"
FAVICON_VERSION = "20260713-1"


def add_brand_assets(html):
    if "static/favicon.svg" not in html:
        html = html.replace(
            "  <title>八丈島運航統計予測</title>",
            (
                "  <title>八丈島運航統計予測</title>\n"
                f"  <link rel=\"icon\" type=\"image/svg+xml\" href=\"static/favicon.svg?v={FAVICON_VERSION}\">\n"
                f"  <link rel=\"apple-touch-icon\" href=\"static/logo.svg?v={FAVICON_VERSION}\">\n"
                f"  <link rel=\"stylesheet\" href=\"static/favicon-brand.css?v={FAVICON_VERSION}\">"
            ),
            1,
        )
    if "class=\"site-logo\"" not in html:
        html = html.replace(
            "      <p class=\"eyebrow\">HND / HAC</p>",
            (
                f"      <img class=\"site-logo\" src=\"static/logo.svg?v={FAVICON_VERSION}\" "
                "alt=\"\" aria-hidden=\"true\">\n"
                "      <p class=\"eyebrow\">HND / HAC</p>"
            ),
            1,
        )
    return html


def build_site(output_dir=DIST_DIR):
    if os.getenv("FORECAST_DATA_BACKEND", "sqlite").lower() != "bigquery":
        restore_db(BASE_DIR / "flights.db", BASE_DIR / "data" / "flights_dump.sql")
    bundle = load_forecast_bundle(print)
    days = build_daily_forecasts(
        bundle["weather"],
        bundle["ensembles"],
        jma_by_time=bundle["jma"],
    )
    updated_at = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
    with app.app_context():
        html = render_template(
            "index.html",
            days=days,
            error=None,
            updated_at=updated_at,
            notices=bundle["notices"],
            low_probability_threshold=LOW_PROBABILITY_THRESHOLD,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / ".nojekyll").touch()
    (output_dir / "index.html").write_text(add_brand_assets(html), encoding="utf-8")
    shutil.copytree(BASE_DIR / "static", output_dir / "static", dirs_exist_ok=True)
    print(f"Built {output_dir / 'index.html'} with {len(days)} forecast days.")


if __name__ == "__main__":
    build_site()
