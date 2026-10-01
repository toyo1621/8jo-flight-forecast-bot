from unittest.mock import Mock, patch

import pytest

from flight_forecast.publish_prediction_snapshots import verify_public_artifact


def response(body, status=200):
    result = Mock(status=status)
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = body
    return result


def test_verify_public_artifact_requires_the_expected_artifact_id():
    html = response(b'<meta name="forecast-artifact-id" content="artifact-1">')
    manifest = response(b'{"artifact_id": "artifact-1"}')

    with patch(
        "flight_forecast.publish_prediction_snapshots.urlopen",
        side_effect=[html, manifest],
    ) as open_url:
        verified = verify_public_artifact(
            "https://toyo1621.github.io/8jo-flight-forecast-bot/",
            "artifact-1",
            cache_token="test-token",
        )

    assert "verify=artifact-1" in verified
    requested_urls = [call.args[0].full_url for call in open_url.call_args_list]
    assert all("attempt=1" in url and "nonce=test-token" in url for url in requested_urls)
    assert requested_urls[1].startswith(
        "https://toyo1621.github.io/8jo-flight-forecast-bot/build-manifest.json?"
    )


def test_verify_public_artifact_rejects_stale_html():
    html = response(b'<meta name="forecast-artifact-id" content="old-artifact">')
    manifest = response(b'{"artifact_id": "old-artifact"}')

    with (
        patch(
            "flight_forecast.publish_prediction_snapshots.urlopen",
            side_effect=[html, manifest],
        ),
        pytest.raises(RuntimeError, match="一致しません"),
    ):
        verify_public_artifact("https://example.test/", "artifact-1", attempts=1)


def test_verify_public_artifact_retries_until_pages_propagates():
    stale_html = response(
        b'<meta name="forecast-artifact-id" content="old-artifact">'
    )
    stale_manifest = response(b'{"artifact_id": "old-artifact"}')
    current_html = response(
        b'<meta name="forecast-artifact-id" content="artifact-1">'
    )
    current_manifest = response(b'{"artifact_id": "artifact-1"}')

    with (
        patch(
            "flight_forecast.publish_prediction_snapshots.urlopen",
            side_effect=[
                stale_html,
                stale_manifest,
                current_html,
                current_manifest,
            ],
        ) as open_url,
        patch("flight_forecast.publish_prediction_snapshots.time.sleep") as sleep,
    ):
        verified = verify_public_artifact(
            "https://example.test/",
            "artifact-1",
            attempts=2,
            sleep_fn=sleep,
            cache_token="test-token",
        )

    assert verified.endswith("?verify=artifact-1")
    sleep.assert_called_once_with(1)
    requested_urls = [call.args[0].full_url for call in open_url.call_args_list]
    assert any("attempt=1" in url for url in requested_urls)
    assert any("attempt=2" in url for url in requested_urls)


def test_verify_public_artifact_requires_html_and_manifest_to_match():
    html = response(b'<meta name="forecast-artifact-id" content="artifact-1">')
    stale_manifest = response(b'{"artifact_id": "old-artifact"}')

    with (
        patch(
            "flight_forecast.publish_prediction_snapshots.urlopen",
            side_effect=[html, stale_manifest],
        ),
        pytest.raises(RuntimeError, match="manifest='old-artifact'"),
    ):
        verify_public_artifact("https://example.test/", "artifact-1", attempts=1)


def test_default_retry_window_allows_pages_cache_to_propagate():
    stale_html = response(
        b'<meta name="forecast-artifact-id" content="old-artifact">'
    )
    stale_manifest = response(b'{"artifact_id": "old-artifact"}')
    sleep = Mock()

    with (
        patch(
            "flight_forecast.publish_prediction_snapshots.urlopen",
            side_effect=[stale_html, stale_manifest] * 10,
        ),
        pytest.raises(RuntimeError, match="一致しません"),
    ):
        verify_public_artifact(
            "https://example.test/",
            "artifact-1",
            sleep_fn=sleep,
            cache_token="test-token",
        )

    total_wait = sum(call.args[0] for call in sleep.call_args_list)
    assert 120 <= total_wait <= 180
