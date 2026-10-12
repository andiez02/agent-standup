import json
import urllib.error
from datetime import date

import pytest

from agent_standup.deliver import DeliveryError, already_sent, archive, mark_sent, post_slack

DAY = date(2026, 10, 8)


def test_post_slack_success():
    seen = {}

    def opener(req, timeout):
        seen["body"], seen["type"] = json.loads(req.data), req.get_header("Content-type")
        return b"ok"

    post_slack("https://hooks.slack.com/x", "hello", opener, sleep=lambda s: None)
    assert seen["body"] == {"text": "hello", "mrkdwn": True}
    assert seen["type"] == "application/json"


def test_post_slack_retries_then_succeeds():
    attempts, sleeps = [], []

    def opener(req, timeout):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, None)
        return b"ok"

    post_slack("https://hooks.slack.com/x", "hi", opener, sleep=sleeps.append)
    assert len(attempts) == 3
    assert sleeps == [2, 4]


def test_post_slack_gives_up_without_leaking_url():
    def opener(req, timeout):
        raise urllib.error.URLError("offline")

    with pytest.raises(DeliveryError) as exc:
        post_slack("https://hooks.slack.com/services/SECRET", "hi", opener, sleep=lambda s: None)
    assert "SECRET" not in str(exc.value)
    assert "offline" in str(exc.value)


def test_post_slack_4xx_fails_fast():
    calls = []

    def opener(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 404, "no_service", {}, None)

    with pytest.raises(DeliveryError, match="HTTP 404"):
        post_slack("https://hooks.slack.com/x", "hi", opener, sleep=lambda s: None)
    assert len(calls) == 1


def test_archive_and_marker(tmp_path):
    path = archive("# hi\n", DAY, tmp_path)
    assert path == tmp_path / "reports" / "2026-10-08.md"
    assert path.read_text() == "# hi\n"
    assert not already_sent(DAY, tmp_path)
    mark_sent(DAY, tmp_path)
    assert already_sent(DAY, tmp_path)
