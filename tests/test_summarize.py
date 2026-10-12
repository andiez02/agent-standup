import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_standup import engines
from agent_standup.config import Config, JiraSite
from agent_standup.models import GitInfo, JiraIssue, ProjectEvidence, SessionInfo, TicketRef
from agent_standup.summarize import (SummaryError, build_prompt, extract_json, fallback_report, parse_report,
                                     summarize)

ICT = timezone(timedelta(hours=7))
DAY = date(2026, 10, 8)
CFG = Config(jira=(JiraSite("https://acme.atlassian.net", "a@b", "env:T", ("APP",)),))


def evidence():
    p = ProjectEvidence(name="Storefront", events=5)
    p.tickets["APP-9588"] = TicketRef("APP-9588", user_mentions=2)
    p.tickets["APP-9584"] = TicketRef("APP-9584", assistant_mentions=1)
    p.sessions[("claude", "s1")] = SessionInfo("claude", "s1", datetime(2026, 10, 8, 9, 55, tzinfo=ICT), datetime(2026, 10, 8, 10, 49, tzinfo=ICT), "fix/APP-9588")
    p.prompts = ["cho t tên commit và branch"]
    p.assistant_tail[("claude", "s1")] = ["Commit: fix(history): stop endless loading (APP-9588)"]
    p.jira["APP-9588"] = JiraIssue("APP-9588", "History page loads indefinitely", "In Review", "indeterminate", "https://acme.atlassian.net/browse/APP-9588")
    p.git = [GitInfo("/r/web", "fix/APP-9588", (), True)]
    m = ProjectEvidence(name="jukebox", events=3)
    m.sessions[("codex", "c1")] = SessionInfo("codex", "c1", datetime(2026, 10, 8, 13, 56, tzinfo=ICT), datetime(2026, 10, 8, 22, 33, tzinfo=ICT), "feat/lobby")
    m.prompts = ["make the stake picker look better"]
    return [p, m]


GOOD = {"projects": [
    {"name": "Storefront", "items": [{"key": "APP-9588", "title": "model title", "status": "done", "note": "Fix ready on branch"}]},
    {"name": "jukebox", "items": [{"key": None, "title": "Stake picker polish", "status": "weird", "note": "x" * 300},
                                   {"key": "APP-7777", "title": "Invented ticket", "status": "done", "note": ""}]},
]}


def test_pick_engine():
    assert engines.pick("auto", which={"codex": "/bin/codex"}.get) == "codex"
    assert engines.pick("auto", which=lambda n: "/bin/" + n) == "claude"
    with pytest.raises(engines.EngineError):
        engines.pick("auto", which=lambda n: None)
    with pytest.raises(engines.EngineError, match="claude"):
        engines.pick("claude", which=lambda n: None)


def test_engine_commands_never_persist_sessions():
    assert engines.claude_cmd() == ["claude", "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--model", "sonnet"]
    assert engines.codex_cmd(Path("/tmp/o.txt")) == ["codex", "exec", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check", "--color", "never", "-o", "/tmp/o.txt", "-"]


def test_engine_commands_use_custom_model():
    assert engines.claude_cmd("haiku")[-2:] == ["--model", "haiku"]
    assert engines.codex_cmd(Path("/tmp/o.txt"), "gpt-5") == engines.codex_cmd(Path("/tmp/o.txt"))[:-1] + ["-m", "gpt-5", "-"]


@pytest.mark.parametrize("model", [None, "haiku"])
def test_run_claude_reads_result_field(tmp_path, model):
    def runner(cmd, **kw):
        assert cmd[-2:] == ["--model", model or "sonnet"]
        assert kw["input"] == "PROMPT" and kw["cwd"] == tmp_path / "w" and kw["timeout"] == 180
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"type": "result", "result": "ANSWER"}), stderr="")

    assert engines.run("claude", "PROMPT", tmp_path / "w", model=model, runner=runner) == "ANSWER"


@pytest.mark.parametrize("model", [None, "gpt-5"])
def test_run_codex_reads_output_file(tmp_path, model):
    def runner(cmd, **kw):
        assert cmd[-1] == "-"
        assert (cmd[-3:-1] == ["-m", model]) if model else "-m" not in cmd
        Path(cmd[cmd.index("-o") + 1]).write_text("CODEX ANSWER")
        return subprocess.CompletedProcess(cmd, 0, stdout="noise", stderr="")

    assert engines.run("codex", "PROMPT", tmp_path, model=model, runner=runner) == "CODEX ANSWER"


def test_run_errors_become_engine_error(tmp_path):
    def fails(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="auth required")

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 180)

    with pytest.raises(engines.EngineError, match="auth required"):
        engines.run("claude", "P", tmp_path, runner=fails)
    with pytest.raises(engines.EngineError, match="timed out"):
        engines.run("codex", "P", tmp_path, runner=slow)


def test_prompt_contains_rules_and_evidence():
    prompt = build_prompt(DAY, evidence(), Config(language="vi"))
    assert "Thursday 2026-10-08" in prompt
    assert "language: vi" in prompt
    assert '"APP-9588"' in prompt
    assert "History page loads indefinitely" in prompt
    assert '"dirty": true' in prompt
    assert '"mentioned_only_by_agent": [' in prompt
    assert prompt.index('"APP-9584"') > prompt.index('"mentioned_only_by_agent"')
    assert "Output ONLY a JSON object" in prompt


def test_payload_dedupes_commits_across_worktrees():
    from agent_standup.summarize import evidence_payload

    p = ProjectEvidence(name="m")
    p.git = [GitInfo("/r/m", "a", ("abc fix one", "def fix two"), False), GitInfo("/r/m-lobby", "b", ("abc fix one", "ghi three"), True)]
    git = evidence_payload([p], CFG)[0]["git"]
    assert git[0]["commits"] == ["abc fix one", "def fix two"]
    assert git[1]["commits"] == ["ghi three"]


def test_prompt_shrinks_to_limit():
    big = evidence()
    big[0].prompts = [f"prompt {i} " + "z" * 290 for i in range(200)]
    prompt = build_prompt(DAY, big, CFG, limit=20000)
    assert len(prompt) < 24000


@pytest.mark.parametrize("text", [
    json.dumps(GOOD),
    "Here you go:\n```json\n" + json.dumps(GOOD) + "\n```",
    "{not json} then " + json.dumps(GOOD),
])
def test_extract_json_tolerates_wrapping(text):
    assert extract_json(text) == GOOD


def test_extract_json_failure():
    with pytest.raises(SummaryError):
        extract_json("no json here")


def test_parse_report_validates_and_prefers_jira_data():
    report = parse_report(json.dumps(GOOD), DAY, evidence(), CFG)
    storefront, jukebox = report.projects
    item = storefront.items[0]
    assert item.title == "History page loads indefinitely"
    assert item.jira_status == "In Review"
    assert item.status == "done"
    assert item.url == "https://acme.atlassian.net/browse/APP-9588"
    other = jukebox.items[0]
    assert other.status == "in_progress"
    assert len(other.note) <= 120
    assert other.key is None
    assert jukebox.items[1].key is None
    assert jukebox.items[1].url is None


def test_parse_report_rejects_empty():
    with pytest.raises(SummaryError):
        parse_report('{"projects": []}', DAY, evidence(), CFG)
    with pytest.raises(SummaryError):
        parse_report('{"projects": [{"name": "X", "items": [{"title": ""}]}]}', DAY, evidence(), CFG)


def test_fallback_report_lists_tickets_and_sessions():
    report = fallback_report(DAY, evidence(), CFG)
    storefront, jukebox = report.projects
    assert [i.key for i in storefront.items] == ["APP-9588"]
    assert storefront.items[0].title == "History page loads indefinitely"
    assert storefront.items[0].status is None
    assert jukebox.items[0].key is None
    assert "13:56–22:33" in jukebox.items[0].title


@pytest.mark.parametrize("model", [None, "haiku"])
def test_summarize_happy_path(tmp_path, model):
    def run(e, p, w, *, model):
        assert model == cfg.model
        return json.dumps(GOOD)

    cfg = Config(model=model, jira=CFG.jira)
    report = summarize(DAY, evidence(), cfg, tmp_path, pick=lambda e: "claude", run=run)
    assert report.warning is None
    assert report.projects[0].items[0].status == "done"


@pytest.mark.parametrize("run", [
    lambda e, p, w, model=None: "garbage",
    lambda e, p, w, model=None: (_ for _ in ()).throw(engines.EngineError("claude timed out after 180s")),
])
def test_summarize_falls_back_with_warning(tmp_path, run):
    report = summarize(DAY, evidence(), CFG, tmp_path, pick=lambda e: "claude", run=run)
    assert report.warning and report.warning.startswith("AI summary unavailable")
    assert report.projects[0].items[0].status is None
