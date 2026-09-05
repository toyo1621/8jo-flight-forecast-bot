"""Verify a deployed Pages artifact before marking its snapshots public."""

import argparse
import time
from html.parser import HTMLParser
from urllib.parse import quote
from urllib.request import Request, urlopen

from bigquery_storage import publish_prediction_artifact


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


def verify_public_artifact(public_url, artifact_id, timeout=30, attempts=5, sleep_fn=time.sleep):
    if attempts < 1:
        raise ValueError("公開成果物の確認回数は1回以上にしてください。")
    url = public_url.rstrip("/") + "/?verify=" + quote(artifact_id)
    last_error = None
    for attempt in range(attempts):
        request = Request(url, headers={"User-Agent": "8jo-flight-forecast-publisher/1"})
        try:
            with urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError(f"公開URLのHTTPステータスが{response.status}です。")
                body = response.read().decode("utf-8")
            parser = ArtifactParser()
            parser.feed(body)
            if parser.artifact_ids == [artifact_id]:
                return url
            last_error = RuntimeError(
                "公開HTMLのforecast-artifact-idが期待値と一致しません。"
            )
        except (OSError, UnicodeDecodeError, RuntimeError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            sleep_fn(min(2**attempt, 16))
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
