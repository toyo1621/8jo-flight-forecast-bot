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
        self.forecast_artifact_ids = []
        self.forecast_code_versions = []
        self.forecast_config_versions = []
        self.forecast_snapshot_counts = []
        self._json_ld_parts = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "link" and values.get("rel") == "canonical":
            self.canonicals.append(values.get("href"))
        if tag == "meta" and values.get("name") == "description":
            self.descriptions.append(values.get("content"))
        if tag == "meta" and values.get("name") == "forecast-artifact-id":
            self.forecast_artifact_ids.append(values.get("content"))
        if tag == "meta" and values.get("name") == "forecast-code-version":
            self.forecast_code_versions.append(values.get("content"))
        if tag == "meta" and values.get("name") == "forecast-config-version":
            self.forecast_config_versions.append(values.get("content"))
        if tag == "meta" and values.get("name") == "forecast-snapshot-count":
            self.forecast_snapshot_counts.append(values.get("content"))
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
    manifest_path = output_dir / "build-manifest.json"
    if not manifest_path.is_file():
        errors.append("build-manifest.jsonがありません。公開成果物の追跡情報を確認できません。")
        manifest = {}
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"build-manifest.jsonが不正です: {exc}")
            manifest = {}
    artifact_id = manifest.get("artifact_id")
    snapshot_ids = manifest.get("snapshot_ids")
    if not isinstance(artifact_id, str) or not artifact_id:
        errors.append("build-manifest.jsonのartifact_idがありません。")
    if not isinstance(snapshot_ids, list) or any(
        not isinstance(value, str) or not value for value in snapshot_ids
    ):
        errors.append("build-manifest.jsonのsnapshot_idsが不正です。")
    for field in ("generated_at", "code_version", "config_version"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            errors.append(f"build-manifest.jsonの{field}がありません。")
    if (
        isinstance(manifest.get("snapshot_count"), int)
        and isinstance(snapshot_ids, list)
        and manifest["snapshot_count"] != len(snapshot_ids)
    ):
        errors.append("snapshot_countとsnapshot_idsの件数が一致しません。")

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
        if url == SITE_URL and parser.forecast_artifact_ids != [artifact_id]:
            errors.append(
                f"トップページのforecast-artifact-idがmanifestと一致しません: "
                f"{parser.forecast_artifact_ids} != {artifact_id}"
            )
        if url == SITE_URL and parser.forecast_code_versions != [manifest.get("code_version")]:
            errors.append("トップページのforecast-code-versionがmanifestと一致しません。")
        if url == SITE_URL and parser.forecast_config_versions != [manifest.get("config_version")]:
            errors.append("トップページのforecast-config-versionがmanifestと一致しません。")
        if url == SITE_URL and parser.forecast_snapshot_counts != [str(manifest.get("snapshot_count"))]:
            errors.append("トップページのforecast-snapshot-countがmanifestと一致しません。")
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
