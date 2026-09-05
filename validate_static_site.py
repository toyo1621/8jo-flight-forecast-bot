import argparse
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from build_static import SITE_URL


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonicals = []
        self.descriptions = []
        self.h1_count = 0
        self.links = []
        self.json_ld = []
        self._json_ld_parts = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "link" and values.get("rel") == "canonical":
            self.canonicals.append(values.get("href"))
        if tag == "meta" and values.get("name") == "description":
            self.descriptions.append(values.get("content"))
        if tag == "h1":
            self.h1_count += 1
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag == "script" and values.get("type") == "application/ld+json":
            self._json_ld_parts = []

    def handle_endtag(self, tag):
        if tag == "script" and self._json_ld_parts is not None:
            self.json_ld.append("".join(self._json_ld_parts).strip())
            self._json_ld_parts = None

    def handle_data(self, data):
        if self._json_ld_parts is not None:
            self._json_ld_parts.append(data)


def _output_path(output_dir, url):
    parsed = urlparse(url)
    base_path = urlparse(SITE_URL).path
    if not parsed.path.startswith(base_path):
        return None
    relative = parsed.path[len(base_path) :]
    return output_dir / relative / "index.html" if not Path(relative).suffix else output_dir / relative


def validate_site(output_dir):
    errors = []
    sitemap = ElementTree.parse(output_dir / "sitemap.xml")
    namespace = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    sitemap_urls = [node.text for node in sitemap.findall("s:url/s:loc", namespace)]
    for url in sitemap_urls:
        page_path = _output_path(output_dir, url)
        if page_path is None or not page_path.is_file():
            errors.append(f"sitemap URLに対応するファイルがありません: {url}")
            continue
        parser = PageParser()
        parser.feed(page_path.read_text(encoding="utf-8"))
        if parser.canonicals != [url]:
            errors.append(f"canonicalがURLと一致しません: {url} {parser.canonicals}")
        if len(parser.descriptions) != 1 or not parser.descriptions[0]:
            errors.append(f"meta descriptionが一つではありません: {url}")
        if parser.h1_count != 1:
            errors.append(f"h1が一つではありません: {url} ({parser.h1_count})")
        for value in parser.json_ld:
            try:
                json.loads(value)
            except json.JSONDecodeError as exc:
                errors.append(f"JSON-LDが不正です: {url} ({exc})")
        for href in parser.links:
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            resolved = urljoin(url, href)
            parsed = urlparse(resolved)
            if parsed.netloc != urlparse(SITE_URL).netloc or not parsed.path.startswith(
                urlparse(SITE_URL).path
            ):
                continue
            target = _output_path(output_dir, resolved)
            if target is not None and not target.is_file():
                errors.append(f"内部リンク切れ: {url} -> {href}")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    errors = validate_site(args.output_dir)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        raise SystemExit(1)
    print("Static SEO validation passed.")


if __name__ == "__main__":
    main()
