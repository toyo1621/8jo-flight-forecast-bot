import shutil
from datetime import datetime
from xml.sax.saxutils import escape

from flask import render_template

from access_stats import load_access_stats
from app_config import LOW_PROBABILITY_THRESHOLD
from bigquery_storage import fetch_published_forecast_archive, save_prediction_snapshots
from forecast_archive import build_archive_days
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
HISTORY_URL = f"{SITE_URL}history/"
ABOUT_URL = f"{SITE_URL}about/"
PRIVACY_URL = f"{SITE_URL}privacy/"
DEFAULT_TITLE = "八丈島の飛行機運航目安｜羽田便の天気・過去実績"
DEFAULT_DESCRIPTION = (
    "羽田空港から八丈島空港へ向かうANA便の運航目安を、JMA主予報、"
    "GFS・ECMWF、台風影響度、過去実績から確認できます。"
)


def _sitemap_entry(entry):
    if isinstance(entry, str):
        entry = {"url": entry}
    last_modified = entry.get("last_modified")
    if isinstance(last_modified, datetime):
        last_modified = last_modified.date().isoformat()
    elif last_modified and hasattr(last_modified, "isoformat"):
        last_modified = last_modified.isoformat()
    lastmod = f"<lastmod>{escape(str(last_modified)[:10])}</lastmod>" if last_modified else ""
    return f"  <url><loc>{escape(entry['url'])}</loc>{lastmod}</url>\n"


def write_search_assets(output_dir, page_urls=None):
    page_urls = page_urls or [SITE_URL, GUIDE_URL]
    sitemap_urls = "".join(_sitemap_entry(entry) for entry in page_urls)
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
    archive_days = build_archive_days(fetch_published_forecast_archive())
    print(f"BigQueryから過去日の公開スナップショットを {len(archive_days)} 日分取得しました。")
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
    current_dates = {day["date"] for day in days}
    historical_days = [day for day in archive_days if day["date"] not in current_dates]
    archive_date_pages = [
        (
            output_dir / "forecast" / day["date"] / "index.html",
            f"{SITE_URL}forecast/{day['date']}/",
            day,
        )
        for day in historical_days
    ]
    page_urls = [
        {"url": SITE_URL, "last_modified": bundle.get("data_updated_at")},
        {"url": GUIDE_URL},
        {"url": HISTORY_URL, "last_modified": historical_days[0]["date"] if historical_days else None},
        {"url": ABOUT_URL},
        {"url": PRIVACY_URL},
        *(
            {"url": page_url, "last_modified": bundle.get("data_updated_at")}
            for _, page_url, _ in date_pages
        ),
        *(
            {"url": page_url, "last_modified": day.get("last_modified")}
            for _, page_url, day in archive_date_pages
        ),
    ]
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
        archive_structured_data = {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "過去の予測と運航結果",
            "url": HISTORY_URL,
            "inLanguage": "ja-JP",
        }
        history_html = render_template(
            "history.html",
            archive_days=historical_days,
            access_stats=access_stats,
            page_url=HISTORY_URL,
            structured_data=archive_structured_data,
        )
        info_pages = [
            (
                "about",
                ABOUT_URL,
                {
                    "eyebrow": "ABOUT",
                    "title": "このサイトについて",
                    "description": "八丈島便の運航目安の目的、運営者、使用データ、更新・訂正方針を説明します。",
                    "lead": "旅行前の判断材料を増やすために、天気と過去実績を整理して公開している個人運営の参考サイトです。",
                    "sections": [
                        {"title": "目的と運営者", "wide": True, "paragraphs": ["八丈島を訪れる人が、羽田発のANA3便について天候上の注意点をまとめて確認できるよう、開発者 toyo1621 が個人で運営しています。航空会社、空港、気象庁、公共交通オープンデータセンターの公式サービスではありません。", "運航可否の最終確認はANA公式情報を優先してください。"], "links": [{"label": "開発者のX", "url": "https://x.com/toyo1621", "external": True}, {"label": "開発者のInstagram", "url": "https://www.instagram.com/toyo1621/", "external": True}]},
                        {"title": "使用データ", "wide": False, "paragraphs": ["気象庁(JMA)モデルを主予報とし、GFS・ECMWFを比較表示します。運航実績はBigQueryに保存した対象3便の記録を使います。公開ページから生データのダンプは配布しません。"], "links": []},
                        {"title": "更新と訂正", "wide": False, "paragraphs": ["予報ページは原則6時間ごとに再生成します。取得障害時は古いデータを最新と表示せず、公開済みサイトを維持します。誤りを確認した場合は、元の公開予測を改変せず、実績または説明を訂正します。"], "links": []},
                    ],
                },
            ),
            (
                "privacy",
                PRIVACY_URL,
                {
                    "eyebrow": "PRIVACY",
                    "title": "プライバシーについて",
                    "description": "八丈島便の運航目安におけるアクセス解析、外部リンク、お問い合わせの取り扱いを説明します。",
                    "lead": "このサイトで利用するアクセス解析と、外部サービスへ移動した後の情報の扱いをまとめています。",
                    "sections": [
                        {"title": "アクセス解析", "wide": True, "paragraphs": ["利用状況を把握するためCloudflare Web Analyticsでページビューを集計しています。サイト上では過去7日間の集計値だけを表示し、個人を識別するアクセス履歴は公開しません。"], "links": [{"label": "Cloudflare Web Analyticsの説明", "url": "https://www.cloudflare.com/web-analytics/", "external": True}]},
                        {"title": "お問い合わせ", "wide": False, "paragraphs": ["お問い合わせフォームはGoogleフォームを使用します。フォームへ移動後に入力した情報はGoogleのサービス上で扱われます。必要以上の個人情報を入力しないでください。"], "links": []},
                        {"title": "外部リンク", "wide": False, "paragraphs": ["ANA、東京都、気象庁、Open-Meteo、X、Instagramなどの外部サイトには、それぞれのプライバシーポリシーが適用されます。"], "links": []},
                    ],
                },
            ),
        ]
        rendered_info_pages = [
            (
                output_dir / slug / "index.html",
                render_template(
                    "info_page.html",
                    page_url=page_url,
                    access_stats=access_stats,
                    structured_data={
                        "@context": "https://schema.org",
                        "@type": "AboutPage" if slug == "about" else "WebPage",
                        "name": context["title"],
                        "url": page_url,
                        "inLanguage": "ja-JP",
                    },
                    **context,
                ),
            )
            for slug, page_url, context in info_pages
        ]
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
        rendered_archive_pages = [
            (
                page_path,
                add_brand_assets(
                    render_template(
                        "archive_date.html",
                        day=day,
                        page_url=page_url,
                        access_stats=access_stats,
                        structured_data={
                            "@context": "https://schema.org",
                            "@type": "Article",
                            "headline": f"{day['long_date_label']}の八丈島便 公開時予測と運航結果",
                            "datePublished": day["date"],
                            "dateModified": (
                                day["last_modified"].isoformat()
                                if hasattr(day.get("last_modified"), "isoformat")
                                else str(day.get("last_modified") or day["date"])
                            ),
                            "url": page_url,
                            "inLanguage": "ja-JP",
                        },
                    ),
                    asset_prefix="../../",
                ),
            )
            for page_path, page_url, day in archive_date_pages
        ]
        not_found_html = render_template("404.html")

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
    history_dir = output_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_dir.joinpath("index.html").write_text(
        add_brand_assets(history_html, asset_prefix="../"), encoding="utf-8"
    )
    for page_path, page_html in rendered_info_pages:
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(
            add_brand_assets(page_html, asset_prefix="../"), encoding="utf-8"
        )
    output_dir.joinpath("404.html").write_text(
        add_brand_assets(not_found_html), encoding="utf-8"
    )
    for page_path, page_html in [*rendered_date_pages, *rendered_archive_pages]:
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(page_html, encoding="utf-8")
    shutil.copytree(BASE_DIR / "static", output_dir / "static", dirs_exist_ok=True)
    print(
        f"Built {output_dir / 'index.html'} with {len(days)} forecast days, "
        f"{len(rendered_date_pages)} current date pages, "
        f"{len(rendered_archive_pages)} archive pages, and supporting pages."
    )


if __name__ == "__main__":
    build_site()
