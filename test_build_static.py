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
