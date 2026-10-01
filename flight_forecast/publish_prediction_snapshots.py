"""Verify a deployed Pages artifact before marking its snapshots public."""

import argparse
import json
import time
from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flight_forecast.bigquery_storage import publish_prediction_artifact

DEFAULT_ATTEMPTS = 10
MAX_RETRY_DELAY_SECONDS = 30


class ArtifactParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.artifact_ids = []

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        values = dict(attrs)
        if values.get("name") == "forecast-artifact-id":
            self.artifact_ids.append(values.get("content"))


def _read_public_text(url, timeout):
    request = Request(
        url,
        headers={
            "User-Agent": "8jo-flight-forecast-publisher/1",
            "Cache-Control": "no-cache, no-store",
            "Pragma": "no-cache",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"公開URLのHTTPステータスが{response.status}です。")
        return response.read().decode("utf-8")


def verify_public_artifact(
    public_url,
    artifact_id,
    timeout=15,
    attempts=DEFAULT_ATTEMPTS,
    sleep_fn=time.sleep,
    cache_token=None,
):
    if attempts < 1:
        raise ValueError("公開成果物の確認回数は1回以上にしてください。")
    base_url = public_url.rstrip("/")
    verified_url = base_url + "/?" + urlencode({"verify": artifact_id})
    cache_token = cache_token or str(time.time_ns())
    last_error = None
    for attempt in range(attempts):
        attempt_number = attempt + 1
        query = urlencode(
            {
                "verify": artifact_id,
                "attempt": attempt_number,
                "nonce": cache_token,
            }
        )
        html_url = f"{base_url}/?{query}"
        manifest_url = f"{base_url}/build-manifest.json?{query}"
        try:
            body = _read_public_text(html_url, timeout)
            manifest_body = _read_public_text(manifest_url, timeout)
            parser = ArtifactParser()
            parser.feed(body)
            manifest = json.loads(manifest_body)
            if not isinstance(manifest, dict):
                raise TypeError("公開build-manifest.jsonがJSONオブジェクトではありません。")
            manifest_artifact_id = manifest.get("artifact_id")
            print(
                f"公開確認 {attempt_number}/{attempts}: "
                f"HTML={parser.artifact_ids!r}, "
                f"manifest={manifest_artifact_id!r}, expected={artifact_id!r}"
            )
            if (
                parser.artifact_ids == [artifact_id]
                and manifest_artifact_id == artifact_id
            ):
                return verified_url
            last_error = RuntimeError(
                "公開成果物のartifact IDが期待値と一致しません。"
                f" HTML={parser.artifact_ids!r},"
                f" manifest={manifest_artifact_id!r}, expected={artifact_id!r}"
            )
        except (
            OSError,
            TypeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            RuntimeError,
        ) as exc:
            last_error = exc
            print(f"公開確認 {attempt_number}/{attempts} 失敗: {exc}")
        if attempt_number < attempts:
            sleep_fn(min(2**attempt, MAX_RETRY_DELAY_SECONDS))
    raise last_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--public-url", required=True)
    args = parser.parse_args()
    verified_url = verify_public_artifact(args.public_url, args.artifact_id)
    updated = publish_prediction_artifact(args.artifact_id, verified_url)
    if updated <= 0:
        raise RuntimeError("artifact_idに対応する公開候補がBigQueryにありません。")
    print(f"公開確認済みスナップショットを{updated}件に反映しました。")


if __name__ == "__main__":
    main()
