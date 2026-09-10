import time

import requests

RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
MAX_REQUEST_ATTEMPTS = 3
BACKOFF_SECONDS = (1, 2)


def request_with_retries(request_get, endpoint, params, timeout):
    """Perform one bounded HTTP request with connect/read timeout and backoff."""
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        status_code = None
        try:
            response = request_get(endpoint, params=params, timeout=timeout)
            status_code = getattr(response, "status_code", None)
            if status_code in RETRYABLE_STATUS_CODES and attempt + 1 < MAX_REQUEST_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS[attempt])
                continue
            response.raise_for_status()
            return response
        except requests.HTTPError as exc:
            response_status = getattr(getattr(exc, "response", None), "status_code", status_code)
            if response_status not in RETRYABLE_STATUS_CODES:
                raise
            if attempt + 1 >= MAX_REQUEST_ATTEMPTS:
                raise
            time.sleep(BACKOFF_SECONDS[attempt])
        except requests.RequestException:
            if attempt + 1 >= MAX_REQUEST_ATTEMPTS:
                raise
            time.sleep(BACKOFF_SECONDS[attempt])
    raise RuntimeError("HTTPリクエストの再試行が想定外に終了しました。")
