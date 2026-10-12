from datetime import date

from agent_standup.models import ProjectReport, Report, ReportItem
from agent_standup.render import to_markdown, to_slack

DAY = date(2026, 10, 8)


def report(**kw):
    return Report(DAY, [
        ProjectReport("Storefront", [ReportItem("APP-9588", "History page loads indefinitely", "done", "Fix ready", "In Review", "https://acme.atlassian.net/browse/APP-9588")]),
        ProjectReport("jukebox", [ReportItem(None, "Stake picker polish", "in_progress", "PR opened"),
                                   ReportItem(None, "Waiting on design", "blocked", "Needs Alex's mockup")]),
    ], **kw)


def test_slack_layout():
    text = to_slack(report())
    assert text.splitlines() == [
        "*Daily report: Thu 08/10*",
        "",
        "*Storefront*",
        "Yesterday I worked on:",
        "• <https://acme.atlassian.net/browse/APP-9588|APP-9588>: History page loads indefinitely",
        "    ✅ Done: Fix ready (Jira: In Review)",
        "",
        "*jukebox*",
        "Yesterday I worked on:",
        "• Stake picker polish",
        "    🟡 In progress: PR opened",
        "• Waiting on design",
        "    🔴 Blocked: Needs Alex's mockup",
    ]


def test_slack_escapes_user_text():
    r = Report(DAY, [ProjectReport("A & B", [ReportItem(None, "Fix <Modal> & overlay", "done", "a < b")])])
    text = to_slack(r)
    assert "*A &amp; B*" in text
    assert "• Fix &lt;Modal&gt; &amp; overlay" in text
    assert "a &lt; b" in text
    assert "<Modal>" not in text


def test_slack_warning_and_fallback_items():
    r = Report(DAY, [ProjectReport("Fieldops", [ReportItem("LM-1", "", None, "", None, None)])], warning="AI summary unavailable (x); showing raw activity.")
    lines = to_slack(r).splitlines()
    assert lines[1] == "_⚠️ AI summary unavailable (x); showing raw activity._"
    assert "• LM-1" in lines
    assert not any(l.startswith("    ") for l in lines)


def test_slack_empty_report():
    assert to_slack(Report(DAY, [])) == "*Daily report: Thu 08/10*\n\nNo agent activity recorded for 2026-10-08."


def test_slack_truncates_long_reports():
    items = [ReportItem(None, f"item {i} " + "w" * 200, "done", "n") for i in range(400)]
    text = to_slack(Report(DAY, [ProjectReport("Big", items)]), limit=5000)
    assert len(text) <= 5000
    assert text.endswith("…(truncated, full report in the local archive)")


def test_markdown():
    md = to_markdown(report())
    assert md.startswith("# Daily report: Thu 08/10\n")
    assert "## Storefront" in md
    assert "- [APP-9588](https://acme.atlassian.net/browse/APP-9588): History page loads indefinitely" in md
    assert "  - ✅ Done: Fix ready (Jira: In Review)" in md
    assert "- Stake picker polish" in md
