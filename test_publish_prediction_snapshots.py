from unittest.mock import Mock, patch

import pytest

from publish_prediction_snapshots import verify_public_artifact


def test_verify_public_artifact_requires_the_expected_artifact_id():
    response = Mock(status=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = (
        b'<meta name="forecast-artifact-id" content="artifact-1">'
    )

    with patch("publish_prediction_snapshots.urlopen", return_value=response):
        verified = verify_public_artifact(
            "https://toyo1621.github.io/8jo-flight-forecast-bot/", "artifact-1"
        )

    assert "verify=artifact-1" in verified


def test_verify_public_artifact_rejects_stale_html():
    response = Mock(status=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = (
        b'<meta name="forecast-artifact-id" content="old-artifact">'
    )

    with (
        patch("publish_prediction_snapshots.urlopen", return_value=response),
        pytest.raises(RuntimeError, match="一致しません"),
    ):
        verify_public_artifact("https://example.test/", "artifact-1", attempts=1)


def test_verify_public_artifact_retries_until_pages_propagates():
    stale = Mock(status=200)
    stale.__enter__ = Mock(return_value=stale)
    stale.__exit__ = Mock(return_value=False)
    stale.read.return_value = b'<meta name="forecast-artifact-id" content="old-artifact">'
    current = Mock(status=200)
    current.__enter__ = Mock(return_value=current)
    current.__exit__ = Mock(return_value=False)
    current.read.return_value = b'<meta name="forecast-artifact-id" content="artifact-1">'

    with (
        patch("publish_prediction_snapshots.urlopen", side_effect=[stale, current]),
        patch("publish_prediction_snapshots.time.sleep") as sleep,
    ):
        verified = verify_public_artifact(
            "https://example.test/", "artifact-1", attempts=2, sleep_fn=sleep
        )

    assert verified.endswith("?verify=artifact-1")
    sleep.assert_called_once_with(1)
