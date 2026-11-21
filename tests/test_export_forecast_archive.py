from unittest.mock import patch

from flight_forecast.export_forecast_archive import export_archive


def test_private_archive_includes_older_dates_and_uses_local_links(tmp_path):
    row = {
        "forecast_target_date": "2025-01-12",
        "flight_number": "ANA1891",
        "model": "jma_seamless",
        "probability": 75,
        "outcome_status": "運航",
        "publication_status": "published",
    }
    with (
        patch("flight_forecast.export_forecast_archive.mkdtemp", return_value=str(tmp_path)),
        patch("flight_forecast.export_forecast_archive.fetch_published_forecast_archive",
              return_value=[row]),
    ):
        index = export_archive()

    listing = index.read_text(encoding="utf-8")
    detail_url = "../forecast/2025-01-12/index.html"
    assert f'href="{detail_url}"' in listing
    detail = (index.parent / detail_url).read_text(encoding="utf-8")
    assert "実際の運航結果" in detail
    assert "75" in detail
    assert 'href="../../history/index.html"' in detail
    for html in (listing, detail):
        assert 'content="noindex,nofollow"' in html
        assert 'rel="canonical"' not in html
    assert (tmp_path / "static/styles.css").is_file()
