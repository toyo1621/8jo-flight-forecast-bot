from unittest.mock import patch

from build_static import add_brand_assets, build_site


def test_add_brand_assets_recognizes_current_site_title():
    html = "<head>\n  <title>八丈島の飛行機運航目安｜羽田便の天気・過去実績</title>\n</head>"

    branded = add_brand_assets(html)

    assert 'href="static/favicon.svg?' in branded
    assert "<title>八丈島の飛行機運航目安｜羽田便の天気・過去実績</title>" in branded


def test_build_site_persists_prediction_snapshots_before_rendering(tmp_path):
    bundle = {
        "weather": {},
        "ensembles": {},
        "typhoon_impacts": {},
        "notices": [],
        "data_updated_at": "2026-08-24T00:00:00+09:00",
    }
    with (
        patch("build_static.load_forecast_bundle", return_value=bundle),
        patch("build_static.build_daily_forecasts", return_value=[]),
        patch("build_static.save_prediction_snapshots", return_value=0) as save_snapshots,
        patch("build_static.load_access_stats", return_value={"days": []}),
    ):
        build_site(tmp_path)

    save_snapshots.assert_called_once_with([])
    assert (tmp_path / "index.html").exists()
    assert "Sitemap: https://toyo1621.github.io/8jo-flight-forecast-bot/sitemap.xml" in (
        tmp_path / "robots.txt"
    ).read_text(encoding="utf-8")
    assert "<loc>https://toyo1621.github.io/8jo-flight-forecast-bot/</loc>" in (
        tmp_path / "sitemap.xml"
    ).read_text(encoding="utf-8")
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'rel="canonical"' in html
    assert '"@type": "WebApplication"' in html
    assert (tmp_path / "guide" / "index.html").exists()
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
    with (
        patch("build_static.load_forecast_bundle", return_value=bundle),
        patch("build_static.build_daily_forecasts", return_value=[day]),
        patch("build_static.save_prediction_snapshots", return_value=0),
        patch("build_static.load_access_stats", return_value={"days": []}),
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
