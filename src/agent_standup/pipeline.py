from datetime import date
from pathlib import Path

from agent_standup import deliver, render, secrets, summarize
from agent_standup.collect import collect_events
from agent_standup.config import Config, ConfigError
from agent_standup.day import previous_workday
from agent_standup.enrich import git, jira
from agent_standup.group import ProjectResolver, build_evidence
from agent_standup.models import Report


def build_report(cfg: Config, day: date, *, data: Path, use_ai: bool = True, collect=collect_events, resolver=None,
                 jira_fetch=jira.fetch_all, git_info=git.repo_info, summarizer=summarize.summarize) -> Report | None:
    events = collect(cfg, day, exclude_cwd=data)
    if not events:
        return None
    evidence = build_evidence(events, cfg, resolver or ProjectResolver(cfg.projects))
    if not evidence:
        return None
    issues = jira_fetch(cfg, [key for p in evidence for key in p.tickets])
    for p in evidence:
        p.jira = {k: issues[k] for k in p.tickets if k in issues}
        p.git = [git_info(root, day, cfg.tz()) for root in sorted(p.repo_roots)]
    if not use_ai:
        return summarize.fallback_report(day, evidence, cfg)
    return summarizer(day, evidence, cfg, workdir=data / "engine")


def send(cfg: Config, today: date, *, data: Path, build=build_report, post=deliver.post_slack, resolve=secrets.resolve) -> str:
    day = previous_workday(today, cfg.holidays)
    if deliver.already_sent(day, data):
        return "already-sent"
    report = build(cfg, day, data=data)
    if report is None:
        if not cfg.send_when_empty:
            return "empty"
        report = Report(day, [])
    deliver.archive(render.to_markdown(report), day, data)
    url = resolve(cfg.slack_webhook)
    if not url:
        raise ConfigError("slack.webhook_url is not set")
    post(url, render.to_slack(report))
    deliver.mark_sent(day, data)
    return "sent"
