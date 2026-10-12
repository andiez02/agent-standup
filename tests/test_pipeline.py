from datetime import date, datetime, timedelta, timezone

import pytest

from agent_standup import pipeline
from agent_standup.config import Config, ConfigError
from agent_standup.deliver import DeliveryError
from agent_standup.models import Event, GitInfo, ProjectReport, Report, ReportItem

ICT = timezone(timedelta(hours=7))
MONDAY = date(2026, 10, 12)
FRIDAY = date(2026, 10, 9)


def fake_report(cfg, day, **kw):
    return Report(day, [ProjectReport("P", [ReportItem(None, "Work", "done", "ok")])])


def test_build_report_wires_stages(tmp_path):
    seen = {}

    def collect(cfg, day, exclude_cwd=None):
        seen["exclude"] = exclude_cwd
        return [Event("claude", datetime(2026, 10, 9, 9, tzinfo=ICT), "s1", "/r/app", "fix/APP-1", "user", "do APP-1")]

    def summarizer(day, evidence, cfg, workdir):
        seen["workdir"], seen["evidence"] = workdir, evidence
        return Report(day, [])

    cfg = Config(extra_ticket_prefixes=("APP",))
    pipeline.build_report(cfg, FRIDAY, data=tmp_path, collect=collect,
                          resolver=pipeline.ProjectResolver((), git_toplevel=lambda c: "/r/app"),
                          jira_fetch=lambda cfg, keys: {}, git_info=lambda root, day, tz: GitInfo(root, "fix/APP-1", (), True),
                          summarizer=summarizer)
    assert seen["exclude"] == tmp_path
    assert seen["workdir"] == tmp_path / "engine"
    assert seen["evidence"][0].git == [GitInfo("/r/app", "fix/APP-1", (), True)]
    assert "APP-1" in seen["evidence"][0].tickets


def test_build_report_none_when_no_events(tmp_path):
    assert pipeline.build_report(Config(), FRIDAY, data=tmp_path, collect=lambda cfg, day, exclude_cwd=None: []) is None


def test_send_posts_archives_and_marks(tmp_path):
    posted = []
    cfg = Config(slack_webhook="env:HOOK")
    status = pipeline.send(cfg, MONDAY, data=tmp_path, build=fake_report,
                           post=lambda url, text: posted.append((url, text)), resolve=lambda ref: "https://hooks.slack.com/x")
    assert status == "sent"
    assert posted[0][0] == "https://hooks.slack.com/x"
    assert "📋 Daily report · Fri 09/10" in posted[0][1]
    assert (tmp_path / "reports" / "2026-10-09.md").exists()
    assert (tmp_path / "sent" / "2026-10-09").exists()


def test_send_is_idempotent(tmp_path):
    cfg = Config(slack_webhook="env:HOOK")
    posted = []
    for _ in range(2):
        status = pipeline.send(cfg, MONDAY, data=tmp_path, build=fake_report,
                               post=lambda url, text: posted.append(text), resolve=lambda ref: "u")
    assert status == "already-sent"
    assert len(posted) == 1


def test_send_empty_day(tmp_path):
    posted = []
    assert pipeline.send(Config(slack_webhook="x"), MONDAY, data=tmp_path, build=lambda cfg, day, **kw: None,
                         post=lambda u, t: posted.append(t), resolve=lambda r: "u") == "empty"
    assert posted == []
    assert pipeline.send(Config(slack_webhook="x", send_when_empty=True), MONDAY, data=tmp_path,
                         build=lambda cfg, day, **kw: None, post=lambda u, t: posted.append(t), resolve=lambda r: "u") == "sent"
    assert "No agent activity" in posted[0]


def test_send_failure_keeps_archive_and_no_marker(tmp_path):
    def boom(url, text):
        raise DeliveryError("Slack unreachable after retries: offline")

    with pytest.raises(DeliveryError):
        pipeline.send(Config(slack_webhook="x"), MONDAY, data=tmp_path, build=fake_report, post=boom, resolve=lambda r: "u")
    assert (tmp_path / "reports" / "2026-10-09.md").exists()
    assert not (tmp_path / "sent" / "2026-10-09").exists()


def test_send_requires_webhook(tmp_path):
    with pytest.raises(ConfigError, match="webhook_url"):
        pipeline.send(Config(), MONDAY, data=tmp_path, build=fake_report, post=lambda u, t: None, resolve=lambda r: r)
