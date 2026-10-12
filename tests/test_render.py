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
        "*📋 Daily report · Thu 08/10*",
        "",
        "*Storefront*",
        "• <https://acme.atlassian.net/browse/APP-9588|APP-9588>: History page loads indefinitely",
        "    ✅ Done: Fix ready (Jira: In Review)",
        "",
        "───────────────",
        "*jukebox*",
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
    assert to_slack(Report(DAY, [])) == "*📋 Daily report · Thu 08/10*\n\nNo agent activity recorded for 2026-10-08."


def test_slack_truncates_long_reports():
    items = [ReportItem(None, f"item {i} " + "w" * 200, "done", "n") for i in range(400)]
    text = to_slack(Report(DAY, [ProjectReport("Big", items)]), limit=5000)
    assert len(text) <= 5000
    assert text.endswith("…(truncated, full report in the local archive)")


def test_markdown():
    assert to_markdown(report()) == (
        "# 📋 Daily report · Thu 08/10\n\n"
        "## Storefront\n\n"
        "- [APP-9588](https://acme.atlassian.net/browse/APP-9588): History page loads indefinitely\n"
        "  - ✅ Done: Fix ready (Jira: In Review)\n\n"
        "## jukebox\n\n"
        "- Stake picker polish\n"
        "  - 🟡 In progress: PR opened\n"
        "- Waiting on design\n"
        "  - 🔴 Blocked: Needs Alex's mockup\n"
    )


def test_status_order_and_fallback_items():
    r = Report(DAY, [ProjectReport("Mixed", [
        ReportItem(None, "Blocked task", "blocked", ""),
        ReportItem("KEY-1", "", None, ""),
        ReportItem(None, "Working", "in_progress", ""),
        ReportItem(None, "Unknown status", "unknown", "note", "Open"),
        ReportItem("KEY-2", "Finished", "done", ""),
        ReportItem(None, "Also finished", "done", ""),
    ])])
    assert to_slack(r).splitlines()[3:] == [
        "• KEY-1",
        "• Unknown status",
        "    note (Jira: Open)",
        "• KEY-2: Finished",
        "    ✅ Done",
        "• Also finished",
        "    ✅ Done",
        "• Working",
        "    🟡 In progress",
        "• Blocked task",
        "    🔴 Blocked",
    ]
    assert to_markdown(r).splitlines()[4:] == [
        "- KEY-1",
        "- Unknown status",
        "  - note (Jira: Open)",
        "- KEY-2: Finished",
        "  - ✅ Done",
        "- Also finished",
        "  - ✅ Done",
        "- Working",
        "  - 🟡 In progress",
        "- Blocked task",
        "  - 🔴 Blocked",
    ]


def test_statusless_details_without_notes():
    r = Report(DAY, [ProjectReport("Fallback", [
        ReportItem(None, "Title only", None, ""),
        ReportItem("KEY-1", "", None, "note"),
        ReportItem("KEY-2", "", None, "", "Open"),
    ])])
    assert to_slack(r).splitlines()[3:] == [
        "• Title only", "• KEY-1", "    note", "• KEY-2", "    (Jira: Open)",
    ]
    assert to_markdown(r).splitlines()[4:] == [
        "- Title only", "- KEY-1", "  - note", "- KEY-2", "  - (Jira: Open)",
    ]


def test_markdown_warning_and_empty_report():
    assert to_markdown(Report(DAY, [], warning="AI unavailable")) == (
        "# 📋 Daily report · Thu 08/10\n\n"
        "> ⚠️ AI unavailable\n\n"
        "No agent activity recorded for 2026-10-08.\n"
    )
