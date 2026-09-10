from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from flight_forecast.build_static import add_brand_assets, build_site
from flight_forecast.validate_static_site import validate_site


def test_completed_today_routes_to_saved_prediction_and_result(tmp_path):
    from flight_forecast.web_app import build_daily_forecasts

    now = datetime(2026, 9, 6, 22, tzinfo=timezone(timedelta(hours=9)))
    days = build_daily_forecasts({"2026-09-06T08:00": {}}, current_time=now)
    archive_rows = [{
        "forecast_target_date": "2026-09-06", "flight_number": number,
        "model": "jma_seamless", "probability": 75,
        "publication_status": "published", "calculation_status": "available",
        "prediction_generated_at": "2026-09-06T06:00:00+09:00",
        "outcome_status": "運航" if number == "ANA1891" else None,
    } for number in ("ANA1891", "ANA1893", "ANA1895")]
    with (
        patch("flight_forecast.build_static.load_forecast_bundle", return_value={
            "weather": {}, "ensembles": {}, "typhoon_impacts": {}, "notices": [],
        }),
        patch("flight_forecast.build_static.build_daily_forecasts", return_value=days),
        patch("flight_forecast.build_static.build_prediction_snapshot_rows", return_value=[{"snapshot_id": "s"}]),
        patch("flight_forecast.build_static.save_prediction_snapshots", return_value=1),
        patch("flight_forecast.build_static.save_prediction_publication_candidates", return_value=1),
        patch("flight_forecast.build_static.fetch_published_forecast_archive", return_value=archive_rows),
        patch("flight_forecast.build_static.load_access_stats", return_value={"days": []}),
    ):
        build_site(tmp_path, current_time=now)
    home = (tmp_path / "index.html").read_text()
    result = (tmp_path / "forecast/2026-09-06/index.html").read_text()
    history = (tmp_path / "history/index.html").read_text()
    assert 'id="date-2026-09-06"' not in home
    assert "予測表示終了" not in home
    assert "実際の運航結果" in result
    assert "公開時の予測" in result
    assert "結果未取得" in result
    assert "運航" in result
    assert "forecast/2026-09-06/" in history


def test_add_brand_assets_recognizes_current_site_title():
    html = "<head>\n  <title>八丈島の飛行機運航目安｜羽田便の天気・過去実績</title>\n</head>"

    branded = add_brand_assets(html)

    assert 'href="static/favicon-32.png?' in branded
    assert 'href="static/favicon-16.png?' in branded
    assert 'href="static/apple-touch-icon.png?' in branded
    assert "<title>八丈島の飛行機運航目安｜羽田便の天気・過去実績</title>" in branded


def test_build_site_persists_prediction_snapshots_before_rendering(tmp_path):
    bundle = {
        "weather": {},
        "ensembles": {},
        "typhoon_impacts": {},
        "notices": [],
        "data_updated_at": "2026-08-24T00:00:00+09:00",
    }
    rows = [{"snapshot_id": "snapshot-1", "code_version": "sha", "config_version": "cfg"}]
    with (
        patch("flight_forecast.build_static.load_forecast_bundle", return_value=bundle),
        patch(
            "flight_forecast.build_static.build_daily_forecasts",
            return_value=[
                {
                    "date": "2026-08-24",
                    "date_label": "8/24",
                    "weekday": "月",
                    "flights": [],
                    "confidence": {"grade": None, "label": "評価不可", "lead_days": 0},
                }
            ],
        ),
        patch("flight_forecast.build_static.build_prediction_snapshot_rows", return_value=rows),
        patch("flight_forecast.build_static.save_prediction_snapshots", return_value=1) as save_snapshots,
        patch("flight_forecast.build_static.save_prediction_publication_candidates", return_value=1),
        patch("flight_forecast.build_static.fetch_published_forecast_archive", return_value=[]),
        patch("flight_forecast.build_static.load_access_stats", return_value={"days": []}),
    ):
        build_site(tmp_path)

    save_snapshots.assert_called_once_with(rows)
    assert (tmp_path / "index.html").exists()
    assert "Sitemap: https://toyo1621.github.io/8jo-flight-forecast-bot/sitemap.xml" in (
        tmp_path / "robots.txt"
    ).read_text(encoding="utf-8")
    assert "<loc>https://toyo1621.github.io/8jo-flight-forecast-bot/</loc>" in (
        tmp_path / "sitemap.xml"
    ).read_text(encoding="utf-8")
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'rel="canonical"' in html
    assert (
        '<meta name="google-site-verification" '
        'content="uqqq0NUofXd_HvVElpSmwF8uGZWy70xdrY71bj7hq6U">'
    ) in html
    assert '"@type": "WebApplication"' in html
    assert (tmp_path / "guide" / "index.html").exists()
    assert (tmp_path / "history" / "index.html").exists()
    assert (tmp_path / "about" / "index.html").exists()
    assert (tmp_path / "privacy" / "index.html").exists()
    assert (tmp_path / "404.html").exists()
    guide_html = (tmp_path / "guide" / "index.html").read_text(encoding="utf-8")
    assert "https://toyo1621.github.io/8jo-flight-forecast-bot/guide/" in (
        tmp_path / "sitemap.xml"
    ).read_text(encoding="utf-8")
    assert "旅行者向けの使い方" in guide_html
    assert "画面に出てくる天候用語" in guide_html
    assert "よくある質問" in guide_html
    assert "例えば、基礎値80に0.9の補正を適用すると72です。" in guide_html
    assert "スコアも上がったり下がったりします" in guide_html
    assert '<p class="page-nav"><a href="../">トップページへ｜今日の八丈島便の運航目安を見る</a></p>' in guide_html
    assert '<p class="eyebrow">GUIDE / HACHIJIMA</p>' not in guide_html
    assert '<h1 class="visually-hidden">八丈島便の欠航リスク・運航目安の見方</h1>' in guide_html
    contact_link = 'href="https://forms.gle/7m2JsHjdi2dNe4Rk6"'
    assert contact_link in html
    assert contact_link in guide_html
    assert "お問い合わせフォーム（Googleフォーム）を開く" in html
    assert "お問い合わせフォーム（Googleフォーム）を開く" in guide_html
    assert 'href="https://x.com/toyo1621"' in html
    assert 'href="https://www.instagram.com/toyo1621/"' in html
    assert 'href="https://x.com/toyo1621"' in guide_html
    assert 'href="https://www.instagram.com/toyo1621/"' in guide_html
    assert html.index('class="contact-section"') < html.index('class="access-stats"')
    assert (tmp_path / "build-manifest.json").exists()
    assert 'name="forecast-artifact-id"' in html


def test_build_site_rejects_nonempty_weather_that_produces_no_forecast_days(tmp_path):
    bundle = {
        "weather": {"2026-08-24T08:00": {"wind_speed": 4.0}},
        "ensembles": {},
        "typhoon_impacts": {},
        "notices": [],
    }
    with (
        patch("flight_forecast.build_static.load_forecast_bundle", return_value=bundle),
        patch("flight_forecast.build_static.build_daily_forecasts", return_value=[]),
        patch("flight_forecast.build_static.save_prediction_snapshots") as save_snapshots,
        pytest.raises(RuntimeError, match="空の成果物"),
    ):
        build_site(tmp_path)

    save_snapshots.assert_not_called()


def test_build_site_rejects_empty_forecast_days_even_when_archive_exists(tmp_path):
    bundle = {"weather": {}, "ensembles": {}, "typhoon_impacts": {}, "notices": []}
    with (
        patch("flight_forecast.build_static.load_forecast_bundle", return_value=bundle),
        patch("flight_forecast.build_static.build_daily_forecasts", return_value=[]),
        patch("flight_forecast.build_static.fetch_published_forecast_archive", return_value=[{"date": "2026-08-23"}]),
        pytest.raises(RuntimeError, match="空の成果物"),
    ):
        build_site(tmp_path)

    assert not (tmp_path / "index.html").exists()


def test_build_site_aborts_before_writing_html_when_archive_read_fails(tmp_path):
    bundle = {
        "weather": {"2026-08-24T08:00": {"wind_speed": 4.0}},
        "ensembles": {},
        "typhoon_impacts": {},
        "notices": [],
    }
    day = {"date": "2026-08-24", "flights": []}
    rows = [{"snapshot_id": "snapshot-1", "code_version": "sha", "config_version": "cfg"}]
    with (
        patch("flight_forecast.build_static.load_forecast_bundle", return_value=bundle),
        patch("flight_forecast.build_static.build_daily_forecasts", return_value=[day]),
        patch("flight_forecast.build_static.build_prediction_snapshot_rows", return_value=rows),
        patch("flight_forecast.build_static.save_prediction_snapshots", return_value=1),
        patch("flight_forecast.build_static.save_prediction_publication_candidates", return_value=1),
        patch(
            "flight_forecast.build_static.fetch_published_forecast_archive",
            side_effect=RuntimeError("BigQuery unavailable"),
        ),
        pytest.raises(RuntimeError, match="BigQuery unavailable"),
    ):
        build_site(tmp_path, current_time=datetime(2026, 8, 24, 12, tzinfo=timezone(timedelta(hours=9))))

    assert not (tmp_path / "index.html").exists()


def test_build_site_records_candidates_separately_from_snapshot_save(tmp_path):
    bundle = {
        "weather": {},
        "ensembles": {},
        "typhoon_impacts": {},
        "notices": [],
    }
    rows = [{"snapshot_id": "snapshot-1", "code_version": "sha", "config_version": "cfg"}]
    with (
        patch("flight_forecast.build_static.load_forecast_bundle", return_value=bundle),
        patch(
            "flight_forecast.build_static.build_daily_forecasts",
            return_value=[
                {
                    "date": "2026-08-24",
                    "date_label": "8/24",
                    "weekday": "月",
                    "flights": [],
                    "confidence": {"grade": None, "label": "評価不可", "lead_days": 0},
                }
            ],
        ),
        patch("flight_forecast.build_static.build_prediction_snapshot_rows", return_value=rows),
        patch("flight_forecast.build_static.save_prediction_snapshots", return_value=1) as save_snapshots,
        patch(
            "flight_forecast.build_static.save_prediction_publication_candidates", return_value=1
        ) as save_candidates,
        patch("flight_forecast.build_static.fetch_published_forecast_archive", return_value=[]),
        patch("flight_forecast.build_static.load_access_stats", return_value={"days": []}),
    ):
        build_site(tmp_path)

    save_snapshots.assert_called_once_with(rows)
    save_candidates.assert_called_once()
    assert save_candidates.call_args.args[0] == rows


def test_build_site_writes_shareable_date_pages(tmp_path):
    bundle = {
        "weather": {},
        "ensembles": {},
        "typhoon_impacts": {},
        "notices": [],
        "data_updated_at": "2026-08-24T00:00:00+09:00",
    }
    day = {
        "date": "2026-08-25",
        "date_label": "8/25",
        "weekday": "火",
        "flights": [],
        "confidence": {
            "grade": None,
            "label": "評価不可",
            "source": "lead_time_caution",
            "lead_days": 1,
            "caution": "アンサンブル予報が不足しています。",
        },
    }
    rows = [{"snapshot_id": "snapshot-1", "code_version": "sha", "config_version": "cfg"}]
    with (
        patch("flight_forecast.build_static.load_forecast_bundle", return_value=bundle),
        patch("flight_forecast.build_static.build_daily_forecasts", return_value=[day]),
        patch("flight_forecast.build_static.build_prediction_snapshot_rows", return_value=rows),
        patch("flight_forecast.build_static.save_prediction_snapshots", return_value=1),
        patch("flight_forecast.build_static.save_prediction_publication_candidates", return_value=1),
        patch("flight_forecast.build_static.fetch_published_forecast_archive", return_value=[]),
        patch("flight_forecast.build_static.load_access_stats", return_value={"days": []}),
    ):
        build_site(tmp_path)

    date_page = tmp_path / "forecast" / "2026-08-25" / "index.html"
    assert date_page.exists()
    date_html = date_page.read_text(encoding="utf-8")
    assert '<link rel="canonical" href="https://toyo1621.github.io/8jo-flight-forecast-bot/forecast/2026-08-25/">' in date_html
    assert 'href="../../static/styles.css?' in date_html
    assert 'href="../../"' in date_html
    assert '<p class="page-nav"><a href="../../">トップページへ｜今日の八丈島便の運航目安を見る</a></p>' in date_html
    assert '<p class="eyebrow">HND / HAC</p>' not in date_html
    assert '<h1 class="visually-hidden">8/25の八丈島便 運航目安</h1>' in date_html
    assert 'href="https://forms.gle/7m2JsHjdi2dNe4Rk6"' in date_html
    assert "お問い合わせフォーム（Googleフォーム）を開く" in date_html
    assert 'href="https://x.com/toyo1621"' in date_html
    assert 'href="https://www.instagram.com/toyo1621/"' in date_html
    assert date_html.index('class="contact-section"') < date_html.index('class="access-stats"')
    assert 'href="forecast/2026-08-25/"' in (
        tmp_path / "index.html"
    ).read_text(encoding="utf-8")
    assert "https://toyo1621.github.io/8jo-flight-forecast-bot/forecast/2026-08-25/" in (
        tmp_path / "sitemap.xml"
    ).read_text(encoding="utf-8")


def test_build_site_keeps_historical_date_with_prediction_outcome_and_reflection(tmp_path):
    bundle = {
        "weather": {},
        "ensembles": {},
        "typhoon_impacts": {},
        "notices": [],
        "data_updated_at": "2026-09-05T00:00:00+09:00",
    }
    archive_rows = []
    for flight_number in ("ANA1891", "ANA1893", "ANA1895"):
        for model, score in (
            ("jma_seamless", 82),
            ("gfs_seamless", 76),
            ("ecmwf_ifs025", 79),
        ):
            archive_rows.append(
                {
                    "snapshot_id": f"{flight_number}-{model}",
                    "forecast_target_date": "2026-09-04",
                    "flight_number": flight_number,
                    "model": model,
                    "calculation_status": "available",
                    "probability": score,
                    "prediction_generated_at": "2026-09-04T05:00:00+09:00",
                    "outcome_status": "運航",
                    "status_reason": None,
                }
            )
    rows = [{"snapshot_id": "snapshot-1", "code_version": "sha", "config_version": "cfg"}]
    with (
        patch("flight_forecast.build_static.load_forecast_bundle", return_value=bundle),
        patch(
            "flight_forecast.build_static.build_daily_forecasts",
            return_value=[
                {
                    "date": "2026-09-05",
                    "date_label": "9/5",
                    "weekday": "土",
                    "flights": [],
                    "confidence": {"grade": None, "label": "評価不可", "lead_days": 0},
                }
            ],
        ),
        patch("flight_forecast.build_static.build_prediction_snapshot_rows", return_value=rows),
        patch("flight_forecast.build_static.save_prediction_snapshots", return_value=1),
        patch("flight_forecast.build_static.save_prediction_publication_candidates", return_value=1),
        patch(
            "flight_forecast.build_static.fetch_published_forecast_archive", return_value=archive_rows
        ),
        patch("flight_forecast.build_static.load_access_stats", return_value={"days": []}),
    ):
        build_site(tmp_path)

    page = tmp_path / "forecast" / "2026-09-04" / "index.html"
    html = page.read_text(encoding="utf-8")
    assert page.exists()
    assert html.count('class="archive-flight"') == 3
    assert "公開時の予測" in html
    assert "実際の運航結果" in html
    assert "振り返り" in html
    assert "参考スコアは高めで、実際も運航しました。" in html
    assert "forecast/2026-09-04/" in (
        tmp_path / "history" / "index.html"
    ).read_text(encoding="utf-8")
    assert validate_site(tmp_path) == []
