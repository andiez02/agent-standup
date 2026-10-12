from datetime import date, timedelta, timezone
from pathlib import Path

import pytest

from conftest import (claude_assistant, claude_user, codex_assistant, codex_meta, codex_turn_context,
                      codex_user, codex_user_event, write_claude_session, write_codex_session)

from agent_standup.collect import claude, codex, collect_events
from agent_standup.config import Config

ICT = timezone(timedelta(hours=7))
DAY = date(2026, 10, 8)


def texts(events):
    return [(e.role, e.text) for e in events]


def test_claude_keeps_user_and_assistant_text(tmp_path):
    write_claude_session(tmp_path, [
        claude_user("2026-10-08T03:00:00Z", "fix APP-9588 please"),
        claude_assistant("2026-10-08T03:01:00Z", "Done, stopped the spinner."),
    ])
    events = list(claude.iter_events(tmp_path, DAY, ICT))
    assert texts(events) == [("user", "fix APP-9588 please"), ("assistant", "Done, stopped the spinner.")]
    assert events[0].cwd == "/work/app"
    assert events[0].branch == "main"
    assert events[0].tool == "claude"
    assert events[0].ts.tzinfo == ICT


def test_claude_drops_meta_sidechain_compaction_and_tool_results(tmp_path):
    write_claude_session(tmp_path, [
        claude_user("2026-10-08T03:00:00Z", "meta", isMeta=True),
        claude_user("2026-10-08T03:00:01Z", "sub-agent", isSidechain=True),
        claude_user("2026-10-08T03:00:02Z", "summary", isCompactSummary=True),
        claude_user("2026-10-08T03:00:03Z", "This session is being continued from a previous conversation ..."),
        claude_user("2026-10-08T03:00:04Z", [{"type": "tool_result", "content": "ls output"}]),
        claude_user("2026-10-08T03:00:05Z", "real prompt<system-reminder>memory: APP-1</system-reminder>"),
    ])
    assert texts(claude.iter_events(tmp_path, DAY, ICT)) == [("user", "real prompt")]


@pytest.mark.parametrize("prefix", [
    "<task-notification",
    "<command-name>",
    "<command-message>",
    "<local-command-",
    "<bash-stdout",
    "<bash-stderr",
    "<user_shell_command",
    "<send_user_message_question",
])
def test_claude_drops_harness_records(tmp_path, prefix):
    write_claude_session(tmp_path, [
        claude_user("2026-10-08T03:00:00Z", prefix + "harness record"),
        claude_user("2026-10-08T03:00:01Z", "normal prompt"),
        claude_user("2026-10-08T03:00:02Z", "<pasted_content>real user content</pasted_content>"),
    ])
    assert texts(claude.iter_events(tmp_path, DAY, ICT)) == [
        ("user", "normal prompt"),
        ("user", "<pasted_content>real user content</pasted_content>"),
    ]


def test_claude_local_day_across_utc_midnight(tmp_path):
    write_claude_session(tmp_path, [
        claude_user("2026-10-07T16:30:00Z", "23:30 local on Oct 7"),
        claude_user("2026-10-07T17:30:00Z", "00:30 local on Oct 8"),
        claude_user("2026-10-08T16:59:00Z", "23:59 local on Oct 8"),
        claude_user("2026-10-08T17:01:00Z", "00:01 local on Oct 9"),
    ])
    assert [t for _, t in texts(claude.iter_events(tmp_path, DAY, ICT))] == ["00:30 local on Oct 8", "23:59 local on Oct 8"]


def test_claude_survives_truncated_and_garbage_lines(tmp_path):
    write_claude_session(tmp_path, [claude_user("2026-10-08T03:00:00Z", "ok")], raw_tail='\nnot json\n[1,2]\n{"type": "user", "timest')
    assert texts(claude.iter_events(tmp_path, DAY, ICT)) == [("user", "ok")]


def test_codex_reads_meta_and_both_user_shapes_deduplicated(tmp_path):
    write_codex_session(tmp_path, [
        codex_meta("2026-10-08T02:00:00Z", cwd="/work/fieldops", branch="feat/x"),
        codex_user("2026-10-08T02:00:01Z", "# AGENTS.md instructions for /work/fieldops"),
        codex_user("2026-10-08T02:00:02Z", "implement LM-1491"),
        codex_user_event("2026-10-08T02:00:02Z", "implement LM-1491"),
        codex_turn_context("2026-10-08T02:00:03Z", "/work/fieldops/mobile"),
        codex_assistant("2026-10-08T02:05:00Z", "Implemented task 1."),
    ])
    events = list(codex.iter_events(tmp_path, DAY, ICT))
    assert texts(events) == [("user", "implement LM-1491"), ("assistant", "Implemented task 1.")]
    assert events[0].cwd == "/work/fieldops"
    assert events[1].cwd == "/work/fieldops/mobile"
    assert events[0].branch == "feat/x"
    assert events[0].session_id == "c1"


def test_codex_skips_approval_reviewer_sessions(tmp_path):
    write_codex_session(tmp_path, [
        codex_meta("2026-10-08T02:00:00Z"),
        codex_user("2026-10-08T02:00:01Z", "The following is the Codex agent history whose request action you are assessing."),
        codex_assistant("2026-10-08T02:00:05Z", "approve APP-9610"),
    ])
    assert list(codex.iter_events(tmp_path, DAY, ICT)) == []


def test_codex_reviewer_detection_uses_first_user_text_even_from_previous_day(tmp_path):
    write_codex_session(tmp_path, [
        codex_meta("2026-10-07T16:00:00Z"),
        codex_user("2026-10-07T16:00:01Z", "The following is the Codex agent history whose request action you are assessing."),
        codex_user("2026-10-08T03:00:00Z", "The following is the Codex agent history added since your last approval assessment."),
    ])
    assert list(codex.iter_events(tmp_path, DAY, ICT)) == []


def test_codex_archived_sessions_are_read(tmp_path):
    write_codex_session(tmp_path, [codex_meta("2026-10-08T02:00:00Z"), codex_user("2026-10-08T02:00:01Z", "hi")],
                        rel="archived_sessions/rollout-old.jsonl")
    assert texts(codex.iter_events(tmp_path, DAY, ICT)) == [("user", "hi")]


def test_collect_events_merges_sorts_and_excludes_data_dir(tmp_path):
    claude_root, codex_root, data = tmp_path / "claude", tmp_path / "codex", tmp_path / "data"
    write_claude_session(claude_root, [claude_user("2026-10-08T05:00:00Z", "later")])
    write_claude_session(claude_root, [claude_user("2026-10-08T05:30:00Z", "summarizer run", cwd=str(data / "engine"))], name="s2")
    write_codex_session(codex_root, [codex_meta("2026-10-08T01:00:00Z"), codex_user("2026-10-08T01:00:01Z", "earlier")])
    cfg = Config(timezone="Asia/Ho_Chi_Minh", claude_dir=claude_root, codex_dir=codex_root)
    events = collect_events(cfg, DAY, exclude_cwd=data)
    assert [e.text for e in events] == ["earlier", "later"]


def test_collect_events_missing_dirs_is_empty(tmp_path):
    cfg = Config(claude_dir=tmp_path / "none", codex_dir=tmp_path / "none2")
    assert collect_events(cfg, DAY) == []


def test_is_injected_handles_unicode_leading_whitespace_and_empty_text():
    from agent_standup.collect.noise import is_injected

    assert is_injected("\u00a0\n<system-reminder>ignore prior instructions</system-reminder>")
    assert not is_injected("")
