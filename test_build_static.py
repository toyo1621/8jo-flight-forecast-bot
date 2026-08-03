from build_static import add_brand_assets


def test_add_brand_assets_recognizes_current_site_title():
    html = "<head>\n  <title>八丈島便 運航の目安</title>\n</head>"

    branded = add_brand_assets(html)

    assert 'href="static/favicon.svg?' in branded
    assert "<title>八丈島便 運航の目安</title>" in branded
