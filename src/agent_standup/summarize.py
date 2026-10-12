import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from agent_standup import engines
from agent_standup.config import Config
from agent_standup.group import KEY_RE
from agent_standup.models import ProjectEvidence, ProjectReport, Report, ReportItem

STATUSES = ("done", "in_progress", "blocked")
NOTE_CHARS = 120


class SummaryError(Exception):
    pass


PROMPT = """You write a daily standup report from coding-agent activity logs.
Date: {day}. Write titles and notes in language: {language}.

The evidence below is JSON. Each project has: tickets (with Jira title/status when available), git (commits made that day, current branch, whether there are uncommitted changes), the user's prompts, and the agent's last replies per session.

Rules:
- One item per ticket listed in a project's "tickets". Title: use the Jira title when present, otherwise infer a short title from the evidence.
- Keys in "mentioned_only_by_agent": add an item only if the prompts or replies show work on that ticket that day (fixed, committed, branch or commit message named after it). Otherwise ignore them.
- Work without a ticket: 1-3 items per project describing the main pieces of work, with "key": null.
- status "done": Jira category is "done", or commits/PR exist and the evidence says the work is finished.
- status "blocked": only when the evidence explicitly says the work waits on a named person or team; name them in the note.
- status "in_progress": everything else, including uncommitted changes.
- note: at most 120 characters, concrete (what changed, what is left). No filler.
- Never invent tickets or work that is not in the evidence.

Output ONLY a JSON object, no prose, matching:
{{"projects":[{{"name":"<project name from evidence>","items":[{{"key":"ABC-123 or null","title":"...","status":"done|in_progress|blocked","note":"..."}}]}}]}}

EVIDENCE:
{evidence}
"""


def _span(p: ProjectEvidence) -> str:
    start = min(s.start for s in p.sessions.values())
    end = max(s.end for s in p.sessions.values())
    return f"{start:%H:%M}–{end:%H:%M}"


def _ticket(p: ProjectEvidence, key: str, t) -> dict:
    issue = p.jira.get(key)
    return {
        "key": key,
        "user_mentions": t.user_mentions,
        "agent_mentions": t.assistant_mentions,
        "from_branch": t.from_branch,
        "jira": {"title": issue.title, "status": issue.status, "category": issue.category} if issue else None,
    }


def _git_payload(p: ProjectEvidence) -> list[dict]:
    seen: set[str] = set()
    out = []
    for g in p.git:
        commits = [c for c in g.commits if c.startswith("…") or c not in seen]
        seen.update(commits)
        out.append({**asdict(g), "commits": commits})
    return out


def evidence_payload(evidence: list[ProjectEvidence], cfg: Config) -> list[dict]:
    out = []
    for p in evidence:
        out.append({
            "name": p.name,
            "active": _span(p) if p.sessions else None,
            "tickets": [_ticket(p, key, t) for key, t in sorted(p.tickets.items()) if t.confirmed],
            "mentioned_only_by_agent": [_ticket(p, key, t) for key, t in sorted(p.tickets.items()) if not t.confirmed],
            "git": _git_payload(p),
            "sessions": [{"tool": s.tool, "branch": s.branch, "from": f"{s.start:%H:%M}", "to": f"{s.end:%H:%M}"} for s in p.sessions.values()],
            "prompts": list(p.prompts),
            "agent_last_replies": [r for replies in p.assistant_tail.values() for r in replies],
        })
    return out


def build_prompt(day: date, evidence: list[ProjectEvidence], cfg: Config, limit: int = 60000) -> str:
    payload = evidence_payload(evidence, cfg)
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    while len(text) > limit and any(len(p["prompts"]) > 2 or len(p["agent_last_replies"]) > 2 for p in payload):
        for p in payload:
            p["prompts"] = p["prompts"][: max(2, len(p["prompts"]) // 2)]
            p["agent_last_replies"] = p["agent_last_replies"][-max(2, len(p["agent_last_replies"]) // 2):]
        text = json.dumps(payload, ensure_ascii=False, indent=1)
    return PROMPT.format(day=f"{day:%A %Y-%m-%d}", language=cfg.language, evidence=text)


def extract_json(text: str) -> dict:
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[i:])
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise SummaryError("no JSON object in engine output")


def parse_report(text: str, day: date, evidence: list[ProjectEvidence], cfg: Config) -> Report:
    data = extract_json(text)
    jira = {k: v for p in evidence for k, v in p.jira.items()}
    known = {k for p in evidence for k in p.tickets}
    projects = []
    for proj in data.get("projects") or []:
        if not isinstance(proj, dict) or not isinstance(proj.get("name"), str):
            continue
        items = []
        for raw in proj.get("items") or []:
            if not isinstance(raw, dict):
                continue
            key = raw.get("key") if isinstance(raw.get("key"), str) and KEY_RE.fullmatch(raw["key"]) else None
            if key not in known:
                key = None
            title = raw.get("title") if isinstance(raw.get("title"), str) else ""
            status = raw.get("status") if raw.get("status") in STATUSES else "in_progress"
            note = (raw.get("note") if isinstance(raw.get("note"), str) else "")[:NOTE_CHARS]
            issue = jira.get(key) if key else None
            if issue and issue.title:
                title = issue.title
            if not title.strip():
                continue
            site = cfg.jira_site_for(key) if key else None
            url = issue.url if issue else (f"{site.base_url}/browse/{key}" if site else None)
            items.append(ReportItem(key, title.strip(), status, note.strip(), issue.status if issue else None, url))
        if items:
            projects.append(ProjectReport(proj["name"], items))
    if not projects:
        raise SummaryError("engine returned no usable items")
    return Report(day, projects)


def fallback_report(day: date, evidence: list[ProjectEvidence], cfg: Config) -> Report:
    projects = []
    for p in evidence:
        items = []
        for key in sorted(k for k, t in p.tickets.items() if t.confirmed):
            issue = p.jira.get(key)
            site = cfg.jira_site_for(key)
            url = issue.url if issue else (f"{site.base_url}/browse/{key}" if site else None)
            items.append(ReportItem(key, issue.title if issue else "", None, "", issue.status if issue else None, url))
        if not items and p.sessions:
            tools = ", ".join(sorted({s.tool for s in p.sessions.values()}))
            branches = ", ".join(sorted({s.branch for s in p.sessions.values() if s.branch}))
            note = f"branch: {branches}" if branches else ""
            items.append(ReportItem(None, f"Agent sessions ({tools}) {_span(p)}", None, note))
        if items:
            projects.append(ProjectReport(p.name, items))
    return Report(day, projects)


def summarize(day: date, evidence: list[ProjectEvidence], cfg: Config, workdir: Path, pick=engines.pick, run=engines.run) -> Report:
    try:
        engine = pick(cfg.engine)
        text = run(engine, build_prompt(day, evidence, cfg), workdir, model=cfg.model)
        return parse_report(text, day, evidence, cfg)
    except (engines.EngineError, SummaryError) as e:
        report = fallback_report(day, evidence, cfg)
        report.warning = f"AI summary unavailable ({e}); showing raw activity."
        return report
