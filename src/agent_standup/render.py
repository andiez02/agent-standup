from agent_standup.models import Report, ReportItem

LABELS = {"done": "✅ Done", "in_progress": "🟡 In progress", "blocked": "🔴 Blocked"}
TRUNCATED = "…(truncated, full report in the local archive)"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _detail(item: ReportItem) -> str:
    parts = []
    if item.status in LABELS:
        parts.append(LABELS[item.status] + (f": {item.note}" if item.note else ""))
    elif item.note:
        parts.append(item.note)
    if item.jira_status:
        parts.append(f"(Jira: {item.jira_status})")
    return " ".join(parts)


def _ordered(items: list[ReportItem]) -> list[ReportItem]:
    order = {status: index for index, status in enumerate(LABELS, 1)}
    return sorted(items, key=lambda item: order.get(item.status, 0))


def _header(report: Report) -> str:
    return f"📋 Daily report · {report.day:%a %d/%m}"


def to_slack(report: Report, limit: int = 39000) -> str:
    lines = [f"*{_header(report)}*"]
    if report.warning:
        lines.append(f"_⚠️ {_esc(report.warning)}_")
    if not report.projects:
        lines += ["", f"No agent activity recorded for {report.day.isoformat()}."]
    for index, project in enumerate(report.projects):
        lines.append("")
        if index:
            lines.append("───────────────")
        lines.append(f"*{_esc(project.name)}*")
        for item in _ordered(project.items):
            key = _esc(item.key or "")
            if key and item.url:
                key = f"<{_esc(item.url)}|{key}>"
            title = _esc(item.title)
            lines.append("• " + (f"{key}: {title}" if key and title else key or title))
            detail = _detail(item)
            if detail:
                lines.append("    " + _esc(detail))
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - len(TRUNCATED) - 1)
    return text[:cut] + "\n" + TRUNCATED


def to_markdown(report: Report) -> str:
    lines = [f"# {_header(report)}", ""]
    if report.warning:
        lines += [f"> ⚠️ {report.warning}", ""]
    if not report.projects:
        lines.append(f"No agent activity recorded for {report.day.isoformat()}.")
    for project in report.projects:
        lines += [f"## {project.name}", ""]
        for item in _ordered(project.items):
            key = f"[{item.key}]({item.url})" if item.key and item.url else (item.key or "")
            lines.append("- " + (f"{key}: {item.title}" if key and item.title else key or item.title))
            detail = _detail(item)
            if detail:
                lines.append(f"  - {detail}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
