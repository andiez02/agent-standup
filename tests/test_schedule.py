import plistlib
import subprocess
from pathlib import Path

from agent_standup import schedule
from agent_standup.config import Config, JiraSite
from agent_standup.doctor import run_checks


def test_build_plist_weekdays_and_path():
    data = plistlib.loads(schedule.build_plist(["/u/bin/agent-standup", "send"], 10, 0, "/u/bin:/usr/bin", Path("/l/a.log")))
    assert data["Label"] == "dev.agent-standup"
    assert data["ProgramArguments"] == ["/u/bin/agent-standup", "send"]
    assert data["StartCalendarInterval"] == [{"Weekday": d, "Hour": 10, "Minute": 0} for d in range(1, 6)]
    assert data["EnvironmentVariables"]["PATH"] == "/u/bin:/usr/bin"
    assert data["StandardOutPath"] == data["StandardErrorPath"] == "/l/a.log"


def test_program_args_prefers_console_script():
    assert schedule.program_args(which=lambda n: "/x/agent-standup") == ["/x/agent-standup", "send"]
    args = schedule.program_args(which=lambda n: None)
    assert args[1:] == ["-m", "agent_standup", "send"]


def test_install_writes_plist_and_bootstraps(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, "plist_path", lambda: tmp_path / "dev.agent-standup.plist")
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    path = schedule.install(Config(hour=9, minute=15), run=run, which=lambda n: "/x/agent-standup", env={"PATH": "/opt/homebrew/bin:/usr/bin"})
    data = plistlib.loads(path.read_bytes())
    assert data["StartCalendarInterval"][0] == {"Weekday": 1, "Hour": 9, "Minute": 15}
    assert data["EnvironmentVariables"]["PATH"] == "/opt/homebrew/bin:/usr/bin"
    assert calls[0][:2] == ["launchctl", "bootout"]
    assert calls[1][:2] == ["launchctl", "bootstrap"]
    assert calls[1][-1] == str(path)


def test_uninstall_removes_plist(tmp_path, monkeypatch):
    plist = tmp_path / "dev.agent-standup.plist"
    plist.write_text("x")
    monkeypatch.setattr(schedule, "plist_path", lambda: plist)
    schedule.uninstall(run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert not plist.exists()


def test_doctor_reports_each_check(tmp_path):
    cfg = Config(slack_webhook="https://hooks.slack.com/plain", claude_dir=tmp_path, codex_dir=tmp_path / "missing",
                 jira=(JiraSite("https://acme.atlassian.net", "a@b", "env:T", ("APP",)),))
    results = run_checks(cfg, which={"claude": "/x/claude"}.get, resolve=lambda r: "tok" if r == "env:T" else r,
                         jira_auth=lambda site, token: True)
    text = "\n".join(f"{'ok' if ok else 'FAIL'} {msg}" for ok, msg in results)
    assert "ok engine: claude" in text
    assert "FAIL slack webhook is stored in plain text" in text
    assert "ok claude logs" in text
    assert "FAIL codex logs" in text
    assert "ok jira https://acme.atlassian.net" in text
