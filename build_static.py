import shutil

from flask import render_template

from access_stats import load_access_stats
from app_config import LOW_PROBABILITY_THRESHOLD
from bigquery_storage import save_prediction_snapshots
from forecast_cache import format_forecast_timestamp
from prediction_provenance import build_prediction_snapshot_rows
from web_app import (
    BASE_DIR,
    app,
    build_daily_forecasts,
    load_forecast_bundle,
)

DIST_DIR = BASE_DIR / "dist"
FAVICON_VERSION = "20260713-2"
SITE_URL = "https://toyo1621.github.io/8jo-flight-forecast-bot/"
GUIDE_URL = f"{SITE_URL}guide/"
DEFAULT_TITLE = "八丈島の飛行機運航目安｜羽田便の天気・過去実績"
DEFAULT_DESCRIPTION = (
    "羽田空港から八丈島空港へ向かうANA便の運航目安を、JMA主予報、"
    "GFS・ECMWF、台風影響度、過去実績から確認できます。"
)


def write_search_assets(output_dir, page_urls=None):
    page_urls = page_urls or [SITE_URL, GUIDE_URL]
    sitemap_urls = "".join(f"  <url><loc>{url}</loc></url>\n" for url in page_urls)
    (output_dir / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n",
        encoding="utf-8",
    )
    (output_dir / "sitemap.xml").write_text(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
        f"{sitemap_urls}"
        "</urlset>\n",
        encoding="utf-8",
    )


def add_brand_assets(html, asset_prefix=""):
    favicon_path = f"{asset_prefix}static/favicon.svg"
    if favicon_path not in html:
        html = html.replace(
            "</title>",
            (
                "</title>\n"
                f"  <link rel=\"icon\" type=\"image/svg+xml\" href=\"{asset_prefix}static/favicon.svg?v={FAVICON_VERSION}\">\n"
                f"  <link rel=\"apple-touch-icon\" href=\"{asset_prefix}static/logo.svg?v={FAVICON_VERSION}\">\n"
                f"  <link rel=\"stylesheet\" href=\"{asset_prefix}static/favicon-brand.css?v={FAVICON_VERSION}\">"
            ),
            1,
        )
    if "class=\"footer-logo\"" not in html:
        html = html.replace(
            "    <footer>\n",
            (
                "    <footer>\n"
                f"      <img class=\"footer-logo\" src=\"{asset_prefix}static/logo.svg?v={FAVICON_VERSION}\" "
                "alt=\"\" aria-hidden=\"true\">\n"
            ),
            1,
        )
    return html


def build_site(output_dir=DIST_DIR):
    bundle = load_forecast_bundle(print)
    days = build_daily_forecasts(
        bundle["weather"],
        bundle["ensembles"],
        typhoon_impacts_by_date=bundle["typhoon_impacts"],
    )
    snapshot_count = save_prediction_snapshots(
        build_prediction_snapshot_rows(days, bundle)
    )
    print(f"BigQueryに予測スナップショットを {snapshot_count} 件保存しました。")
    access_stats = load_access_stats()
    updated_at = format_forecast_timestamp(bundle.get("data_updated_at")) or "取得時刻不明"
    date_pages = [
        (
            output_dir / "forecast" / day["date"] / "index.html",
            f"{SITE_URL}forecast/{day['date']}/",
            day,
        )
        for day in days
    ]
    page_urls = [SITE_URL, GUIDE_URL, *(page_url for _, page_url, _ in date_pages)]
    with app.app_context():
        html = render_template(
            "index.html",
            days=days,
            error=None,
            updated_at=updated_at,
            notices=bundle["notices"],
            low_probability_threshold=LOW_PROBABILITY_THRESHOLD,
            access_stats=access_stats,
            site_url=SITE_URL,
            page_url=SITE_URL,
            page_title=DEFAULT_TITLE,
            page_description=DEFAULT_DESCRIPTION,
            page_heading="八丈島便 運航の目安",
            page_variant="home",
            asset_prefix="",
        )
        guide_html = render_template(
            "guide.html",
            site_url=SITE_URL,
            page_url=GUIDE_URL,
        )
        rendered_date_pages = [
            (
                page_path,
                add_brand_assets(
                    render_template(
                        "index.html",
                        days=[day],
                        error=None,
                        updated_at=updated_at,
                        notices=bundle["notices"],
                        low_probability_threshold=LOW_PROBABILITY_THRESHOLD,
                        access_stats=access_stats,
                        site_url=SITE_URL,
                        page_url=page_url,
                        page_title=(
                            f"{day['date_label']}の八丈島便運航目安｜羽田便の天気・過去実績"
                        ),
                        page_description=(
                            f"{day['date_label']}の羽田空港→八丈島空港ANA便について、"
                            "JMA主予報、GFS・ECMWF、台風影響度、過去実績から運航目安を確認できます。"
                        ),
                        page_heading=f"{day['date_label']}の八丈島便 運航目安",
                        page_variant="date",
                        asset_prefix="../../",
                    ),
                    asset_prefix="../../",
                ),
            )
            for page_path, page_url, day in date_pages
        ]

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / ".nojekyll").touch()
    write_search_assets(output_dir, page_urls)
    (output_dir / "index.html").write_text(add_brand_assets(html), encoding="utf-8")
    guide_dir = output_dir / "guide"
    guide_dir.mkdir(parents=True, exist_ok=True)
    guide_dir.joinpath("index.html").write_text(
        add_brand_assets(guide_html, asset_prefix="../"),
        encoding="utf-8",
    )
    for page_path, page_html in rendered_date_pages:
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(page_html, encoding="utf-8")
    shutil.copytree(BASE_DIR / "static", output_dir / "static", dirs_exist_ok=True)
    print(
        f"Built {output_dir / 'index.html'} with {len(days)} forecast days, "
        f"{len(rendered_date_pages)} date pages, and guide page."
    )


if __name__ == "__main__":
    build_site()
