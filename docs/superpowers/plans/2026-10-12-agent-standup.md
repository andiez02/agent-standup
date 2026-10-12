# agent-standup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python CLI that reads yesterday's Claude Code CLI and Codex CLI session logs, groups the work by project and Jira ticket, has `claude -p` or `codex exec` write a status-per-item summary, and posts it to Slack every weekday at 10:00 via launchd.

**Architecture:** A linear pipeline of small modules (`collect → group → enrich → summarize → render → deliver`) passing plain dataclasses. All I/O (subprocess, HTTP, keychain, clock) is injected as callables with real defaults, so every stage is unit-testable without network or real CLIs. A thin argparse CLI and a launchd installer sit on top.

**Tech Stack:** Python ≥ 3.11 standard library only at runtime (`tomllib`, `urllib`, `subprocess`, `plistlib`, `zoneinfo`, `concurrent.futures`); pytest for tests; hatchling build backend.

**Spec:** `docs/superpowers/specs/2026-10-12-agent-standup-design.md`

## Global Constraints

- Python `>=3.11`; zero runtime dependencies (`dependencies = []`).
- Package name `agent-standup`, import package `agent_standup`, console script `agent-standup`.
- Config path `~/.config/agent-standup/config.toml` (honor `XDG_CONFIG_HOME`); data dir `~/.local/share/agent-standup` (honor `XDG_DATA_HOME`); macOS log `~/Library/Logs/agent-standup.log`.
- launchd label `dev.agent-standup`; schedule Weekday 1–5 at config `schedule.hour`:`schedule.minute` (default 10:00).
- Engine flags exactly: Claude `claude -p --output-format json --tools "" --strict-mcp-config --no-session-persistence`; Codex `codex exec --sandbox read-only --ephemeral --skip-git-repo-check --color never -o <file> -`. Engine timeout 180 s.
- Status values exactly `done`, `in_progress`, `blocked`; rendered as `✅ Done`, `🟡 In progress`, `🔴 Blocked`.
- Secrets (webhook URL, Jira token) never appear in logs, exceptions, or stdout.
- Do **not** run `git commit`, `git add`, `git push`, or create branches. The repository owner commits. Run `git init` once in Task 1 only so the reviewer can diff.
- No explanatory comments in source code. Docstrings only where a public function's contract is not obvious from its name and types.
- Run tests with `.venv/bin/python -m pytest -q`. Every task ends with the full suite green.
- At the end of each task: stop, print the list of files changed and the pytest summary line, and wait for the reviewer.

## Review Focus

1. **The summarizer reports on itself** — `claude -p`/`codex exec` runs would show up in the next day's logs as a project. Expect: engine runs never persist sessions and events with `cwd` under the data dir are dropped. Pinned in Task 3 (collector exclusion) and Task 6 (engine command flags).
2. **Late-night work across UTC midnight** — a prompt at 23:30 local is stored as next-day UTC. Expect it to count for the local day it happened. Pinned in Task 3.
3. **A session file being written while we read it** — last line is truncated JSON, or lines are blank/garbage. Expect the bad line to be skipped, not a crash. Pinned in Task 3.
4. **launchd's minimal PATH** — under launchd `claude`, `codex`, `git` are not on PATH, so every run silently falls back to the raw report. Expect the plist to carry the installing shell's PATH. Pinned in Task 9.
5. **Ticket titles or notes containing `<`, `>`, `&`** (e.g. "Fix <Modal> & overlay") — break Slack mrkdwn or create bogus links. Expect them escaped. Pinned in Task 7.

---

## File Structure

```
pyproject.toml
README.md
LICENSE
.gitignore
.github/workflows/ci.yml
src/agent_standup/__init__.py
src/agent_standup/__main__.py
src/agent_standup/paths.py          # config/data/log locations
src/agent_standup/models.py         # all dataclasses shared between stages
src/agent_standup/day.py            # previous_workday, timestamp parsing, local-date conversion
src/agent_standup/config.py         # Config dataclasses, TOML loading/validation, init template
src/agent_standup/secrets.py        # env:/keychain: secret references
src/agent_standup/collect/__init__.py   # collect_events (both tools + exclusion)
src/agent_standup/collect/noise.py      # injected-context detection and cleaning
src/agent_standup/collect/claude.py     # Claude Code JSONL parser
src/agent_standup/collect/codex.py      # Codex rollout JSONL parser
src/agent_standup/group.py          # project resolution, ticket extraction, evidence bundles
src/agent_standup/enrich/__init__.py
src/agent_standup/enrich/jira.py    # Jira REST lookups
src/agent_standup/enrich/git.py     # per-repo git facts
src/agent_standup/engines.py        # claude/codex subprocess wrappers
src/agent_standup/summarize.py      # prompt, JSON parsing/validation, fallback report
src/agent_standup/render.py         # Slack mrkdwn and Markdown output
src/agent_standup/deliver.py        # webhook POST with retry, archive, sent-marker
src/agent_standup/pipeline.py       # build_report, send
src/agent_standup/schedule.py       # launchd plist install/uninstall
src/agent_standup/doctor.py         # environment checks
src/agent_standup/cli.py            # argparse entry point
tests/conftest.py                   # JSONL builders for Claude and Codex logs
tests/test_day.py
tests/test_config.py
tests/test_secrets.py
tests/test_collect.py
tests/test_group.py
tests/test_enrich.py
tests/test_summarize.py
tests/test_render.py
tests/test_deliver.py
tests/test_pipeline.py
tests/test_schedule.py
tests/test_cli.py
```

The spec mentions static fixture files; this plan builds the fixture JSONL in `tests/conftest.py` builder functions instead, so each test shows exactly the records it depends on.

---

### Task 1: Project scaffold, models, day logic

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/agent_standup/__init__.py`, `src/agent_standup/__main__.py`, `src/agent_standup/paths.py`, `src/agent_standup/models.py`, `src/agent_standup/day.py`
- Test: `tests/test_day.py`

**Interfaces:**
- Produces:
  - `paths.config_path() -> Path`, `paths.data_dir() -> Path`, `paths.log_path() -> Path`
  - `day.previous_workday(today: date, holidays: frozenset[date] = frozenset()) -> date`
  - `day.parse_ts(value: object) -> datetime | None` (always tz-aware; naive treated as UTC)
  - `day.to_local(ts: datetime, tz: tzinfo | None) -> datetime`
  - `day.local_tz(tz: tzinfo | None) -> tzinfo`
  - dataclasses in `models.py` (used by every later task): `Event`, `TicketRef`, `SessionInfo`, `JiraIssue`, `GitInfo`, `ProjectEvidence`, `ReportItem`, `ProjectReport`, `Report`

- [ ] **Step 1: Create the repo skeleton and virtualenv**

```bash
cd ~/Desktop/Workspace/Code/agent-standup
git init
python3 -m venv .venv
mkdir -p src/agent_standup/collect src/agent_standup/enrich tests
```

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "agent-standup"
version = "0.1.0"
description = "Daily standup reports from your Claude Code and Codex CLI sessions, posted to Slack."
readme = "README.md"
requires-python = ">=3.11"
license = "MIT"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8"]

[project.scripts]
agent-standup = "agent_standup.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/agent_standup"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

`.gitignore`:

```
.venv/
__pycache__/
*.egg-info/
dist/
.pytest_cache/
```

`src/agent_standup/__init__.py`:

```python
__version__ = "0.1.0"
```

`src/agent_standup/__main__.py`:

```python
from agent_standup.cli import main

raise SystemExit(main())
```

Create an empty `README.md` (filled in Task 9) so the build backend finds it, then install:

```bash
touch README.md
.venv/bin/pip install -e '.[dev]'
```

- [ ] **Step 2: Write `paths.py` and `models.py`**

`src/agent_standup/paths.py`:

```python
import os
import sys
from pathlib import Path

APP = "agent-standup"


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP / "config.toml"


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP


def log_path() -> Path:
    if sys.platform == "darwin" and not os.environ.get("XDG_DATA_HOME"):
        return Path.home() / "Library" / "Logs" / f"{APP}.log"
    return data_dir() / f"{APP}.log"
```

`src/agent_standup/models.py`:

```python
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class Event:
    tool: str
    ts: datetime
    session_id: str
    cwd: str | None
    branch: str | None
    role: str
    text: str


@dataclass
class TicketRef:
    key: str
    user_mentions: int = 0
    assistant_mentions: int = 0
    from_branch: bool = False

    @property
    def confirmed(self) -> bool:
        return self.user_mentions > 0 or self.from_branch


@dataclass
class SessionInfo:
    tool: str
    session_id: str
    start: datetime
    end: datetime
    branch: str | None


@dataclass(frozen=True)
class JiraIssue:
    key: str
    title: str
    status: str | None
    category: str | None
    url: str


@dataclass(frozen=True)
class GitInfo:
    repo_root: str
    branch: str | None
    commits: tuple[str, ...]
    dirty: bool


@dataclass
class ProjectEvidence:
    name: str
    events: int = 0
    repo_roots: set[str] = field(default_factory=set)
    tickets: dict[str, TicketRef] = field(default_factory=dict)
    sessions: dict[tuple[str, str], SessionInfo] = field(default_factory=dict)
    prompts: list[str] = field(default_factory=list)
    assistant_tail: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    jira: dict[str, JiraIssue] = field(default_factory=dict)
    git: list[GitInfo] = field(default_factory=list)


@dataclass
class ReportItem:
    key: str | None
    title: str
    status: str | None
    note: str
    jira_status: str | None = None
    url: str | None = None


@dataclass
class ProjectReport:
    name: str
    items: list[ReportItem]


@dataclass
class Report:
    day: date
    projects: list[ProjectReport]
    warning: str | None = None
```

- [ ] **Step 3: Write the failing tests for `day.py`**

`tests/test_day.py`:

```python
from datetime import date, datetime, timezone, timedelta

from agent_standup.day import parse_ts, previous_workday, to_local

ICT = timezone(timedelta(hours=7))


def test_monday_reports_friday():
    assert previous_workday(date(2026, 10, 12)) == date(2026, 10, 9)


def test_tuesday_reports_monday():
    assert previous_workday(date(2026, 10, 13)) == date(2026, 10, 12)


def test_sunday_run_reports_friday():
    assert previous_workday(date(2026, 10, 11)) == date(2026, 10, 9)


def test_holiday_is_skipped():
    holidays = frozenset({date(2026, 10, 9)})
    assert previous_workday(date(2026, 10, 12), holidays) == date(2026, 10, 8)


def test_parse_ts_zulu():
    assert parse_ts("2026-10-08T16:30:00.123Z") == datetime(2026, 10, 8, 16, 30, 0, 123000, tzinfo=timezone.utc)


def test_parse_ts_naive_is_utc():
    assert parse_ts("2026-10-08T16:30:00").tzinfo == timezone.utc


def test_parse_ts_rejects_garbage():
    assert parse_ts("yesterday") is None
    assert parse_ts(None) is None
    assert parse_ts(12345) is None


def test_to_local_crosses_midnight():
    ts = parse_ts("2026-10-08T17:30:00Z")
    assert to_local(ts, ICT).date() == date(2026, 10, 9)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_day.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_standup.day'`

- [ ] **Step 5: Implement `day.py`**

`src/agent_standup/day.py`:

```python
from datetime import date, datetime, timedelta, timezone, tzinfo


def previous_workday(today: date, holidays: frozenset[date] = frozenset()) -> date:
    d = today - timedelta(days=1)
    while d.weekday() >= 5 or d in holidays:
        d -= timedelta(days=1)
    return d


def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def to_local(ts: datetime, tz: tzinfo | None) -> datetime:
    return ts.astimezone(tz) if tz else ts.astimezone()


def local_tz(tz: tzinfo | None) -> tzinfo:
    return tz or datetime.now().astimezone().tzinfo
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: `8 passed`

- [ ] **Step 7: Stop for review**

Print changed files and the pytest summary. Do not commit.

---

### Task 2: Config loading and secret references

**Files:**
- Create: `src/agent_standup/config.py`, `src/agent_standup/secrets.py`
- Test: `tests/test_config.py`, `tests/test_secrets.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `config.ConfigError(Exception)`
  - `config.JiraSite(base_url: str, email: str, api_token: str, prefixes: tuple[str, ...])`
  - `config.ProjectRule(name: str, paths: tuple[str, ...], prefixes: tuple[str, ...] = ())`
  - `config.Config` with fields `engine, language, hour, minute, timezone, holidays, send_when_empty, ignore_projects, extra_ticket_prefixes, max_prompts, slack_webhook, jira, projects, claude_dir, codex_dir` and methods `tz() -> tzinfo | None`, `ticket_prefixes() -> frozenset[str]`, `jira_site_for(key: str) -> JiraSite | None`
  - `config.load(path: Path) -> Config`, `config.from_dict(raw: dict, path: str = "<config>") -> Config`
  - `config.TEMPLATE: str`
  - `secrets.SecretError(Exception)`, `secrets.resolve(ref: str | None, runner=...) -> str | None`, `secrets.is_plain(ref: str | None) -> bool`

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
import tomllib
from datetime import date
from pathlib import Path

import pytest

from agent_standup.config import TEMPLATE, ConfigError, from_dict, load


def test_defaults():
    cfg = from_dict({})
    assert cfg.engine == "auto"
    assert (cfg.hour, cfg.minute) == (10, 0)
    assert cfg.ticket_prefixes() == frozenset()
    assert cfg.claude_dir == Path.home() / ".claude"


def test_full_config():
    cfg = from_dict({
        "engine": "codex",
        "schedule": {"hour": 9, "minute": 30},
        "holidays": ["2026-12-25"],
        "extra_ticket_prefixes": ["OPS"],
        "slack": {"webhook_url": "env:HOOK"},
        "jira": [{"base_url": "https://a.atlassian.net/", "email": "me@x.com", "api_token": "env:T", "prefixes": ["APP"]}],
        "projects": [{"name": "Storefront", "paths": ["~/Code/shop/**"], "prefixes": ["APP"]}],
    })
    assert cfg.engine == "codex"
    assert (cfg.hour, cfg.minute) == (9, 30)
    assert cfg.holidays == frozenset({date(2026, 12, 25)})
    assert cfg.ticket_prefixes() == frozenset({"APP", "OPS"})
    assert cfg.jira[0].base_url == "https://a.atlassian.net"
    assert cfg.jira_site_for("APP-12").email == "me@x.com"
    assert cfg.jira_site_for("OPS-1") is None
    assert cfg.slack_webhook == "env:HOOK"


@pytest.mark.parametrize("raw, message", [
    ({"engine": "gpt"}, "engine"),
    ({"schedule": {"hour": 24}}, "schedule"),
    ({"holidays": ["12/25"]}, "holidays"),
    ({"timezone": "Mars/Base"}, "timezone"),
    ({"jira": [{"base_url": "https://a"}]}, "[[jira]] missing"),
    ({"projects": [{"name": "X"}]}, "[[projects]]"),
])
def test_invalid(raw, message):
    with pytest.raises(ConfigError, match=message.replace("[", r"\[").replace("]", r"\]")):
        from_dict(raw)


def test_load_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="agent-standup init"):
        load(tmp_path / "nope.toml")


def test_load_bad_toml(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("engine = ")
    with pytest.raises(ConfigError, match=str(p)):
        load(p)


def test_template_is_valid_and_loads():
    cfg = from_dict(tomllib.loads(TEMPLATE))
    assert cfg.engine == "auto"
```

`tests/test_secrets.py`:

```python
import subprocess

import pytest

from agent_standup.secrets import SecretError, is_plain, resolve


def fake_runner(returncode, stdout=""):
    def run(cmd):
        run.cmd = cmd
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
    return run


def test_none_passthrough():
    assert resolve(None) is None


def test_plain_value():
    assert resolve("https://hooks.slack.com/x") == "https://hooks.slack.com/x"
    assert is_plain("https://hooks.slack.com/x")
    assert not is_plain("env:X")


def test_env(monkeypatch):
    monkeypatch.setenv("HOOK", "secret-value")
    assert resolve("env:HOOK") == "secret-value"


def test_env_missing_does_not_leak(monkeypatch):
    monkeypatch.delenv("HOOK", raising=False)
    with pytest.raises(SecretError, match="HOOK"):
        resolve("env:HOOK")


def test_keychain():
    runner = fake_runner(0, "s3cret\n")
    assert resolve("keychain:agent-standup-slack", runner) == "s3cret"
    assert runner.cmd == ["security", "find-generic-password", "-s", "agent-standup-slack", "-w"]


def test_keychain_missing():
    with pytest.raises(SecretError, match="agent-standup-slack"):
        resolve("keychain:agent-standup-slack", fake_runner(44))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_config.py tests/test_secrets.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_standup.config'`

- [ ] **Step 3: Implement `secrets.py`**

`src/agent_standup/secrets.py`:

```python
import os
import subprocess
from typing import Callable

Runner = Callable[[list[str]], subprocess.CompletedProcess]


class SecretError(Exception):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def resolve(ref: str | None, runner: Runner = _run) -> str | None:
    if ref is None:
        return None
    if ref.startswith("env:"):
        name = ref[4:]
        value = os.environ.get(name)
        if not value:
            raise SecretError(f"environment variable {name} is not set")
        return value
    if ref.startswith("keychain:"):
        service = ref[9:]
        result = runner(["security", "find-generic-password", "-s", service, "-w"])
        if result.returncode != 0:
            raise SecretError(f"keychain item {service!r} not found")
        return result.stdout.strip()
    return ref


def is_plain(ref: str | None) -> bool:
    return bool(ref) and not ref.startswith(("env:", "keychain:"))
```

- [ ] **Step 4: Implement `config.py`**

`src/agent_standup/config.py`:

```python
import tomllib
from dataclasses import dataclass, field
from datetime import date, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

ENGINES = ("auto", "claude", "codex")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class JiraSite:
    base_url: str
    email: str
    api_token: str
    prefixes: tuple[str, ...]


@dataclass(frozen=True)
class ProjectRule:
    name: str
    paths: tuple[str, ...]
    prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    engine: str = "auto"
    language: str = "en"
    hour: int = 10
    minute: int = 0
    timezone: str | None = None
    holidays: frozenset[date] = frozenset()
    send_when_empty: bool = False
    ignore_projects: tuple[str, ...] = ()
    extra_ticket_prefixes: tuple[str, ...] = ()
    max_prompts: int = 20
    slack_webhook: str | None = None
    jira: tuple[JiraSite, ...] = ()
    projects: tuple[ProjectRule, ...] = ()
    claude_dir: Path = field(default_factory=lambda: Path.home() / ".claude")
    codex_dir: Path = field(default_factory=lambda: Path.home() / ".codex")

    def tz(self) -> tzinfo | None:
        return ZoneInfo(self.timezone) if self.timezone else None

    def ticket_prefixes(self) -> frozenset[str]:
        prefixes = set(self.extra_ticket_prefixes)
        for site in self.jira:
            prefixes.update(site.prefixes)
        for rule in self.projects:
            prefixes.update(rule.prefixes)
        return frozenset(prefixes)

    def jira_site_for(self, key: str) -> JiraSite | None:
        prefix = key.split("-", 1)[0]
        return next((s for s in self.jira if prefix in s.prefixes), None)


def load(path: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"config not found: {path} (run `agent-standup init`)") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    return from_dict(raw, str(path))


def from_dict(raw: dict, path: str = "<config>") -> Config:
    def fail(message: str):
        raise ConfigError(f"{path}: {message}")

    engine = raw.get("engine", "auto")
    if engine not in ENGINES:
        fail(f"engine must be one of {', '.join(ENGINES)}")

    schedule = raw.get("schedule", {})
    hour, minute = int(schedule.get("hour", 10)), int(schedule.get("minute", 0))
    if not (0 <= hour < 24 and 0 <= minute < 60):
        fail("schedule hour/minute out of range")

    try:
        holidays = frozenset(date.fromisoformat(h) for h in raw.get("holidays", []))
    except (ValueError, TypeError) as e:
        fail(f"holidays: {e}")

    tz_name = raw.get("timezone")
    if tz_name:
        try:
            ZoneInfo(tz_name)
        except Exception:
            fail(f"unknown timezone {tz_name!r}")

    jira = []
    for site in raw.get("jira", []):
        missing = [k for k in ("base_url", "email", "api_token", "prefixes") if not site.get(k)]
        if missing:
            fail(f"[[jira]] missing {', '.join(missing)}")
        jira.append(JiraSite(site["base_url"].rstrip("/"), site["email"], site["api_token"], tuple(site["prefixes"])))

    projects = []
    for rule in raw.get("projects", []):
        if not rule.get("name") or not rule.get("paths"):
            fail("[[projects]] needs name and paths")
        projects.append(ProjectRule(rule["name"], tuple(rule["paths"]), tuple(rule.get("prefixes", []))))

    return Config(
        engine=engine,
        language=raw.get("language", "en"),
        hour=hour,
        minute=minute,
        timezone=tz_name,
        holidays=holidays,
        send_when_empty=bool(raw.get("send_when_empty", False)),
        ignore_projects=tuple(raw.get("ignore_projects", [])),
        extra_ticket_prefixes=tuple(raw.get("extra_ticket_prefixes", [])),
        max_prompts=int(raw.get("max_prompts", 20)),
        slack_webhook=raw.get("slack", {}).get("webhook_url"),
        jira=tuple(jira),
        projects=tuple(projects),
        claude_dir=Path(raw.get("claude_dir", "~/.claude")).expanduser(),
        codex_dir=Path(raw.get("codex_dir", "~/.codex")).expanduser(),
    )


TEMPLATE = '''\
# agent-standup configuration
# Secrets accept "env:VARIABLE" or "keychain:SERVICE" (macOS Keychain, read with `security`).

engine = "auto"          # auto | claude | codex
language = "en"          # language of titles and notes
schedule = { hour = 10, minute = 0 }
holidays = []            # ["2026-12-25"]
send_when_empty = false
ignore_projects = []
extra_ticket_prefixes = []

[slack]
# webhook_url = "keychain:agent-standup-slack"

# [[jira]]
# base_url = "https://your-company.atlassian.net"
# email = "you@company.com"
# api_token = "keychain:agent-standup-jira"
# prefixes = ["APP"]

# [[projects]]
# name = "My Project"
# paths = ["~/Code/my-project/**"]
'''
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass (`23 passed` or more).

- [ ] **Step 6: Stop for review**

Print changed files and the pytest summary. Do not commit.

---

### Task 3: Log collectors (Claude + Codex) with noise filtering

**Files:**
- Create: `src/agent_standup/collect/__init__.py`, `src/agent_standup/collect/noise.py`, `src/agent_standup/collect/claude.py`, `src/agent_standup/collect/codex.py`, `tests/conftest.py`
- Test: `tests/test_collect.py`

**Interfaces:**
- Consumes: `models.Event`, `day.parse_ts`, `day.to_local`, `config.Config`
- Produces:
  - `collect.collect_events(cfg: Config, day: date, exclude_cwd: Path | None = None) -> list[Event]` (sorted by `ts`, `ts` already converted to local time)
  - `collect.claude.iter_events(root: Path, day: date, tz: tzinfo | None) -> Iterator[Event]`
  - `collect.codex.iter_events(root: Path, day: date, tz: tzinfo | None) -> Iterator[Event]`
  - `collect.noise.clean(text: str) -> str`, `collect.noise.is_injected(text: str) -> bool`, `collect.noise.REVIEWER_PREFIX: str`
  - test builders in `tests/conftest.py`: `claude_user`, `claude_assistant`, `write_claude_session`, `codex_meta`, `codex_turn_context`, `codex_user`, `codex_user_event`, `codex_assistant`, `write_codex_session`

- [ ] **Step 1: Write the JSONL builders**

`tests/conftest.py`:

```python
import json
from pathlib import Path


def claude_user(ts, text, cwd="/work/app", branch="main", session="s1", **extra):
    return {"type": "user", "timestamp": ts, "sessionId": session, "cwd": cwd, "gitBranch": branch,
            "message": {"role": "user", "content": text}, **extra}


def claude_assistant(ts, text, cwd="/work/app", branch="main", session="s1"):
    return {"type": "assistant", "timestamp": ts, "sessionId": session, "cwd": cwd, "gitBranch": branch,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def write_claude_session(root: Path, records, project="-work-app", name="s1", raw_tail=""):
    path = root / "projects" / project / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records) + raw_tail)
    return path


def codex_meta(ts, cwd="/work/app", branch="main", session="c1"):
    return {"type": "session_meta", "timestamp": ts,
            "payload": {"id": session, "cwd": cwd, "git": {"branch": branch}}}


def codex_turn_context(ts, cwd):
    return {"type": "turn_context", "timestamp": ts, "payload": {"cwd": cwd}}


def codex_user(ts, text):
    return {"type": "response_item", "timestamp": ts,
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}}


def codex_user_event(ts, text):
    return {"type": "event_msg", "timestamp": ts, "payload": {"type": "user_message", "message": text}}


def codex_assistant(ts, text):
    return {"type": "response_item", "timestamp": ts,
            "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}}


def write_codex_session(root: Path, records, rel="sessions/2026/10/08/rollout-c1.jsonl"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path
```

- [ ] **Step 2: Write the failing tests**

`tests/test_collect.py`:

```python
from datetime import date, timedelta, timezone
from pathlib import Path

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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_collect.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_standup.collect'`

- [ ] **Step 4: Implement `noise.py`**

`src/agent_standup/collect/noise.py`:

```python
import re

INJECTED_PREFIXES = (
    "# AGENTS.md",
    "<environment_context",
    "<user_instructions",
    "<INSTRUCTIONS",
    "<skills_instructions",
    "<system-reminder",
    "This session is being continued from a previous conversation",
    "Caveat:",
    "[Request interrupted",
    "<task-notification",
    "<command-name>",
    "<command-message>",
    "<local-command-",
    "<bash-stdout",
    "<bash-stderr",
    "<user_shell_command",
    "<send_user_message_question",
)
REVIEWER_PREFIX = "The following is the Codex agent history"
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)


def clean(text: str) -> str:
    return _REMINDER.sub("", text).strip()


def is_injected(text: str) -> bool:
    return text.lstrip().startswith(INJECTED_PREFIXES)
```

- [ ] **Step 5: Implement the Claude collector**

`src/agent_standup/collect/claude.py`:

```python
import json
from datetime import date, datetime, tzinfo
from pathlib import Path
from typing import Iterator

from agent_standup.collect.noise import clean, is_injected
from agent_standup.day import parse_ts, to_local
from agent_standup.models import Event


def iter_events(root: Path, day: date, tz: tzinfo | None) -> Iterator[Event]:
    for path in sorted((root / "projects").glob("*/*.jsonl")):
        if _touched_since(path, day, tz):
            yield from _parse(path, day, tz)


def _touched_since(path: Path, day: date, tz: tzinfo | None) -> bool:
    mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    return to_local(mtime, tz).date() >= day


def _parse(path: Path, day: date, tz: tzinfo | None) -> Iterator[Event]:
    with path.open(errors="ignore") as fh:
        for line in fh:
            rec = _loads(line)
            if rec is None:
                continue
            ts = parse_ts(rec.get("timestamp"))
            if ts is None:
                continue
            local = to_local(ts, tz)
            if local.date() != day:
                continue
            role, text = _extract(rec)
            if not text:
                continue
            text = clean(text)
            if not text or is_injected(text):
                continue
            yield Event("claude", local, rec.get("sessionId") or path.stem, rec.get("cwd"), rec.get("gitBranch"), role, text)


def _loads(line: str) -> dict | None:
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def _extract(rec: dict) -> tuple[str | None, str | None]:
    kind = rec.get("type")
    if kind not in ("user", "assistant"):
        return None, None
    if rec.get("isMeta") or rec.get("isSidechain") or rec.get("isCompactSummary"):
        return None, None
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return kind, content
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        return kind, "\n".join(parts) if parts else None
    return None, None
```

- [ ] **Step 6: Implement the Codex collector**

`src/agent_standup/collect/codex.py`:

```python
from datetime import date, tzinfo
from pathlib import Path
from typing import Iterator

from agent_standup.collect.claude import _loads, _touched_since
from agent_standup.collect.noise import REVIEWER_PREFIX, clean, is_injected
from agent_standup.day import parse_ts, to_local
from agent_standup.models import Event


def iter_events(root: Path, day: date, tz: tzinfo | None) -> Iterator[Event]:
    paths = set((root / "sessions").glob("*/*/*/*.jsonl")) | set((root / "archived_sessions").rglob("*.jsonl"))
    for path in sorted(paths):
        if _touched_since(path, day, tz):
            yield from _parse(path, day, tz)


def _parse(path: Path, day: date, tz: tzinfo | None) -> list[Event]:
    cwd = branch = None
    session_id = path.stem
    first_user: str | None = None
    seen: set[str] = set()
    events: list[Event] = []
    with path.open(errors="ignore") as fh:
        for line in fh:
            rec = _loads(line)
            if rec is None:
                continue
            kind = rec.get("type")
            payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            if kind == "session_meta":
                meta = payload.get("payload", payload) if isinstance(payload.get("payload"), dict) else payload
                cwd = meta.get("cwd", cwd)
                session_id = meta.get("id") or meta.get("session_id") or session_id
                branch = (meta.get("git") or {}).get("branch", branch)
                continue
            if kind == "turn_context":
                cwd = payload.get("cwd", cwd)
                continue
            role, text = _extract(kind, payload)
            if not text:
                continue
            text = clean(text)
            if not text or is_injected(text):
                continue
            if role == "user":
                if first_user is None:
                    first_user = text
                if text in seen:
                    continue
                seen.add(text)
            ts = parse_ts(rec.get("timestamp"))
            if ts is None:
                continue
            local = to_local(ts, tz)
            if local.date() == day:
                events.append(Event("codex", local, session_id, cwd, branch, role, text))
    if first_user is not None and first_user.startswith(REVIEWER_PREFIX):
        return []
    return events


def _extract(kind: str | None, payload: dict) -> tuple[str | None, str | None]:
    if kind == "response_item" and payload.get("type") == "message" and payload.get("role") in ("user", "assistant"):
        content = payload.get("content")
        if isinstance(content, list):
            parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in ("input_text", "output_text")]
            return payload["role"], "\n".join(parts)
        return None, None
    if kind == "event_msg" and payload.get("type") == "user_message":
        return "user", payload.get("message") or ""
    return None, None
```

- [ ] **Step 7: Implement `collect_events`**

`src/agent_standup/collect/__init__.py`:

```python
from datetime import date
from pathlib import Path

from agent_standup.collect import claude, codex
from agent_standup.config import Config
from agent_standup.models import Event


def collect_events(cfg: Config, day: date, exclude_cwd: Path | None = None) -> list[Event]:
    tz = cfg.tz()
    events = [*claude.iter_events(cfg.claude_dir, day, tz), *codex.iter_events(cfg.codex_dir, day, tz)]
    if exclude_cwd is not None:
        events = [e for e in events if not (e.cwd and Path(e.cwd).is_relative_to(exclude_cwd))]
    return sorted(events, key=lambda e: e.ts)
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 9: Smoke-test against the real logs (read-only)**

Run: `.venv/bin/python -c "from datetime import date; from agent_standup.collect import collect_events; from agent_standup.config import Config; ev = collect_events(Config(), date(2026,10,8)); print(len(ev)); print(sorted({(e.tool, e.cwd) for e in ev})[:20])"`
Expected: a non-zero count and a list of working directories. Paste the output in the review report.

- [ ] **Step 10: Stop for review**

Print changed files and the pytest summary. Do not commit.

---

### Task 4: Grouping into project evidence

**Files:**
- Create: `src/agent_standup/group.py`
- Test: `tests/test_group.py`

**Interfaces:**
- Consumes: `models.Event`, `models.ProjectEvidence`, `models.TicketRef`, `models.SessionInfo`, `config.Config`, `config.ProjectRule`
- Produces:
  - `group.KEY_RE` (compiled regex)
  - `group.extract_keys(text: str, prefixes: frozenset[str]) -> list[str]`
  - `group.ProjectResolver(rules: tuple[ProjectRule, ...], git_toplevel: Callable[[str], str | None] = ...)` with `.resolve(cwd: str | None) -> tuple[str, str | None]` returning `(project_name, repo_root)`
  - `group.build_evidence(events: list[Event], cfg: Config, resolver: ProjectResolver) -> list[ProjectEvidence]` (sorted by `events` descending; `tickets` holds every configured-prefix key seen in user text, assistant text, or branch names; `TicketRef.confirmed` is true only for user-text or branch matches)

- [ ] **Step 1: Write the failing tests**

`tests/test_group.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from agent_standup.config import Config, ProjectRule
from agent_standup.group import ProjectResolver, build_evidence, extract_keys
from agent_standup.models import Event

ICT = timezone(timedelta(hours=7))


def ev(text, role="user", cwd="/w/shop/web", branch="main", tool="claude", session="s1", minute=0):
    return Event(tool, datetime(2026, 10, 8, 9, minute, tzinfo=ICT), session, cwd, branch, role, text)


def no_git(cwd):
    return None


def test_extract_keys_only_configured_prefixes():
    text = "APP-9588 and LM-1491, not UTF-8 or SHA-256 or app-1"
    assert extract_keys(text, frozenset({"APP", "LM"})) == ["APP-9588", "LM-1491"]
    assert extract_keys(text, frozenset()) == []


def test_resolver_rule_then_git_then_basename():
    rules = (ProjectRule("Storefront", ("~/shop/**",)),)
    git = {"/r/jukebox/lobby": "/r/jukebox"}.get
    resolver = ProjectResolver(rules, git_toplevel=git)
    home_shop = str(__import__("pathlib").Path("~/shop/web").expanduser())
    assert resolver.resolve(home_shop) == ("Storefront", None)
    assert resolver.resolve(str(__import__("pathlib").Path("~/shop").expanduser())) == ("Storefront", None)
    assert resolver.resolve("/r/jukebox/lobby") == ("jukebox", "/r/jukebox")
    assert resolver.resolve("/tmp/scratch") == ("scratch", None)
    assert resolver.resolve(None) == ("(unknown)", None)


def test_resolver_groups_worktrees_under_main_repo_name():
    tops = {"/r/jukebox-lobby": "/r/jukebox-lobby", "/r/jukebox": "/r/jukebox"}
    resolver = ProjectResolver((), git_toplevel=tops.get, git_main_name=lambda root: "jukebox")
    assert resolver.resolve("/r/jukebox-lobby") == ("jukebox", "/r/jukebox-lobby")
    assert resolver.resolve("/r/jukebox") == ("jukebox", "/r/jukebox")


def test_git_main_name_real_repo(tmp_path):
    import subprocess

    from agent_standup.group import _git_main_name

    repo = tmp_path / "proj"
    repo.mkdir()
    if subprocess.run(["git", "init", "-q", str(repo)]).returncode != 0:
        pytest.skip("git unavailable")
    assert _git_main_name(str(repo)) == "proj"
    assert _git_main_name(str(tmp_path / "missing")) is None


def test_resolver_caches_git_calls():
    calls = []

    def git(cwd):
        calls.append(cwd)
        return "/r/app"

    resolver = ProjectResolver((), git_toplevel=git)
    resolver.resolve("/r/app/src")
    resolver.resolve("/r/app/src")
    assert calls == ["/r/app/src"]


def test_build_evidence_groups_and_filters_tickets():
    cfg = Config(extra_ticket_prefixes=("APP",), projects=(ProjectRule("Storefront", ("/w/shop/**",)),))
    events = [
        ev("investigate APP-9630"),
        ev("Found it. Also see APP-9584 from memory.", role="assistant"),
        ev("commit name?", branch="fix/APP-9588-history", session="s2", minute=5),
        ev("jukebox lobby", cwd="/w/jukebox", tool="codex", session="c1", minute=10),
    ]
    out = build_evidence(events, cfg, ProjectResolver(cfg.projects, git_toplevel=no_git))
    assert [p.name for p in out] == ["Storefront", "jukebox"]
    storefront = out[0]
    assert set(storefront.tickets) == {"APP-9630", "APP-9588", "APP-9584"}
    assert storefront.tickets["APP-9630"].user_mentions == 1
    assert storefront.tickets["APP-9630"].confirmed
    assert storefront.tickets["APP-9588"].from_branch
    assert storefront.tickets["APP-9588"].confirmed
    assert storefront.tickets["APP-9584"].assistant_mentions == 1
    assert not storefront.tickets["APP-9584"].confirmed
    assert storefront.events == 3
    assert set(storefront.sessions) == {("claude", "s1"), ("claude", "s2")}
    assert storefront.prompts == ["investigate APP-9630", "commit name?"]
    assert storefront.assistant_tail[("claude", "s1")] == ["Found it. Also see APP-9584 from memory."]


def test_child_repos_used_when_cwd_is_not_a_repo():
    calls = []

    def child_repos(cwd):
        calls.append(cwd)
        return ["/w/shop/api", "/w/shop/web"] if cwd == "/w/shop" else []

    cfg = Config(projects=(ProjectRule("Storefront", ("/w/shop/**",)),))
    events = [ev("hi", cwd="/w/shop"), ev("again", cwd="/w/shop", minute=1)]
    out = build_evidence(events, cfg, ProjectResolver(cfg.projects, git_toplevel=no_git), child_repos=child_repos)
    assert out[0].repo_roots == {"/w/shop/api", "/w/shop/web"}
    assert calls == ["/w/shop"]


def test_ticket_goes_to_project_with_most_mentions():
    cfg = Config(extra_ticket_prefixes=("APP",))
    events = [
        ev("APP-1 here", cwd="/a"),
        ev("APP-1 again", cwd="/b", minute=1),
        ev("APP-1 and more", cwd="/b", minute=2),
    ]
    out = {p.name: p for p in build_evidence(events, cfg, ProjectResolver((), git_toplevel=no_git))}
    assert "APP-1" in out["b"].tickets
    assert "APP-1" not in out["a"].tickets


def test_prefix_hint_overrides_mentions():
    cfg = Config(projects=(ProjectRule("Fieldops", ("/fieldops/**",), ("LM",)),))
    events = [ev("LM-5 talk", cwd="/other"), ev("LM-5 talk", cwd="/other", minute=1), ev("x", cwd="/fieldops/app", minute=2)]
    out = {p.name: p for p in build_evidence(events, cfg, ProjectResolver(cfg.projects, git_toplevel=no_git))}
    assert "LM-5" in out["Fieldops"].tickets
    assert "LM-5" not in out["other"].tickets


def test_ignore_projects_and_prompt_truncation_and_cap():
    cfg = Config(ignore_projects=("secret",), max_prompts=4)
    events = [ev("hidden", cwd="/secret")] + [ev(f"p{i} " + "x" * 400, cwd="/app", minute=i) for i in range(10)]
    out = build_evidence(events, cfg, ProjectResolver((), git_toplevel=no_git))
    assert [p.name for p in out] == ["app"]
    prompts = out[0].prompts
    assert len(prompts) == 4
    assert prompts[0].startswith("p0") and prompts[-1].startswith("p9")
    assert all(len(p) <= 300 for p in prompts)


def test_assistant_tail_keeps_last_three_truncated():
    cfg = Config()
    events = [ev(f"a{i} " + "y" * 600, role="assistant", minute=i) for i in range(5)]
    out = build_evidence(events, cfg, ProjectResolver((), git_toplevel=no_git))
    tail = out[0].assistant_tail[("claude", "s1")]
    assert [t[:2] for t in tail] == ["a2", "a3", "a4"]
    assert all(len(t) <= 500 for t in tail)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_group.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_standup.group'`

- [ ] **Step 3: Implement `group.py`**

`src/agent_standup/group.py`:

```python
import fnmatch
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

from agent_standup.config import Config, ProjectRule
from agent_standup.models import Event, ProjectEvidence, SessionInfo, TicketRef

KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-(\d{1,6})\b")
PROMPT_CHARS = 300
REPLY_CHARS = 500
TAIL = 3


def extract_keys(text: str, prefixes: frozenset[str]) -> list[str]:
    if not prefixes or not text:
        return []
    return [m.group(0) for m in KEY_RE.finditer(text) if m.group(1) in prefixes]


def _git_toplevel(cwd: str) -> str | None:
    if not os.path.isdir(cwd):
        return None
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _git_main_name(root: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    common = Path(r.stdout.strip())
    return (common.parent.name if common.name == ".git" else common.name.removesuffix(".git")) or None


def _child_repos(cwd: str) -> list[str]:
    try:
        entries = sorted(os.scandir(cwd), key=lambda e: e.name)
    except OSError:
        return []
    return [e.path for e in entries if e.is_dir() and os.path.exists(os.path.join(e.path, ".git"))]


def _match(cwd: str, pattern: str) -> bool:
    pat = os.path.expanduser(pattern).rstrip("/")
    if pat.endswith("/**"):
        base = pat[:-3]
        return cwd == base or cwd.startswith(base + "/")
    return fnmatch.fnmatchcase(cwd, pat)


class ProjectResolver:
    def __init__(self, rules: tuple[ProjectRule, ...], git_toplevel: Callable[[str], str | None] = _git_toplevel,
                 git_main_name: Callable[[str], str | None] = _git_main_name):
        self.rules = rules
        self._git = git_toplevel
        self._main = git_main_name
        self._cache: dict[str, tuple[str, str | None]] = {}

    def resolve(self, cwd: str | None) -> tuple[str, str | None]:
        if not cwd:
            return ("(unknown)", None)
        if cwd not in self._cache:
            self._cache[cwd] = self._resolve(cwd)
        return self._cache[cwd]

    def _resolve(self, cwd: str) -> tuple[str, str | None]:
        root = self._git(cwd)
        for rule in self.rules:
            if any(_match(cwd, p) for p in rule.paths):
                return (rule.name, root)
        if root:
            return (self._main(root) or Path(root).name, root)
        return (Path(cwd).name or cwd, None)


def _cap(items: list[str], limit: int) -> list[str]:
    if len(items) <= limit:
        return items
    head = limit // 2
    return items[:head] + items[len(items) - (limit - head):]


def build_evidence(events: list[Event], cfg: Config, resolver: ProjectResolver,
                   child_repos: Callable[[str], list[str]] = _child_repos) -> list[ProjectEvidence]:
    prefixes = cfg.ticket_prefixes()
    projects: dict[str, ProjectEvidence] = {}
    children: dict[str, list[str]] = {}
    user_counts: dict[str, Counter] = defaultdict(Counter)
    asst_counts: dict[str, Counter] = defaultdict(Counter)
    branch_keys: dict[str, set[str]] = defaultdict(set)
    all_prompts: dict[str, list[str]] = defaultdict(list)

    for e in events:
        name, root = resolver.resolve(e.cwd)
        if name in cfg.ignore_projects:
            continue
        p = projects.setdefault(name, ProjectEvidence(name=name))
        p.events += 1
        if root:
            p.repo_roots.add(root)
        elif e.cwd:
            if e.cwd not in children:
                children[e.cwd] = child_repos(e.cwd)
            p.repo_roots.update(children[e.cwd])
        sid = (e.tool, e.session_id)
        s = p.sessions.get(sid)
        if s is None:
            p.sessions[sid] = SessionInfo(e.tool, e.session_id, e.ts, e.ts, e.branch)
        else:
            s.start, s.end = min(s.start, e.ts), max(s.end, e.ts)
            s.branch = e.branch or s.branch
        for key in extract_keys(e.branch or "", prefixes):
            branch_keys[name].add(key)
        if e.role == "user":
            user_counts[name].update(extract_keys(e.text, prefixes))
            short = e.text[:PROMPT_CHARS]
            if short not in all_prompts[name]:
                all_prompts[name].append(short)
        else:
            asst_counts[name].update(extract_keys(e.text, prefixes))
            tail = p.assistant_tail.setdefault(sid, [])
            tail.append(e.text[:REPLY_CHARS])
            del tail[:-TAIL]

    for name, p in projects.items():
        p.prompts = _cap(all_prompts[name], cfg.max_prompts)

    candidates = set()
    for name in projects:
        candidates |= set(user_counts[name]) | set(asst_counts[name]) | branch_keys[name]

    hint = {pre: rule.name for rule in cfg.projects for pre in rule.prefixes if rule.name in projects}
    for key in sorted(candidates):
        owner = hint.get(key.split("-", 1)[0])
        if owner is None:
            owner = max(projects, key=lambda n: (user_counts[n][key] + asst_counts[n][key] + (key in branch_keys[n]), projects[n].events))
        projects[owner].tickets[key] = TicketRef(
            key=key,
            user_mentions=sum(user_counts[n][key] for n in projects),
            assistant_mentions=sum(asst_counts[n][key] for n in projects),
            from_branch=any(key in branch_keys[n] for n in projects),
        )

    return sorted(projects.values(), key=lambda p: p.events, reverse=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 5: Stop for review**

Print changed files and the pytest summary. Do not commit.

---

### Task 5: Enrichment — Jira and git

**Files:**
- Create: `src/agent_standup/enrich/__init__.py` (empty), `src/agent_standup/enrich/jira.py`, `src/agent_standup/enrich/git.py`
- Test: `tests/test_enrich.py`

**Interfaces:**
- Consumes: `config.Config`, `config.JiraSite`, `secrets.resolve`, `secrets.SecretError`, `models.JiraIssue`, `models.GitInfo`, `day.local_tz`
- Produces:
  - `enrich.jira.fetch_issue(site: JiraSite, token: str, key: str, opener=...) -> JiraIssue | None`
  - `enrich.jira.fetch_all(cfg: Config, keys: list[str], resolve_secret=secrets.resolve, opener=...) -> dict[str, JiraIssue]`
  - `enrich.jira.check_auth(site: JiraSite, token: str, opener=...) -> bool`
  - `enrich.git.repo_info(root: str, day: date, tz: tzinfo | None, run=...) -> GitInfo`
  - Opener signature used everywhere: `opener(request: urllib.request.Request, timeout: float) -> bytes`

- [ ] **Step 1: Write the failing tests**

`tests/test_enrich.py`:

```python
import base64
import json
import urllib.error
from datetime import date, timedelta, timezone

from agent_standup.config import Config, JiraSite
from agent_standup.enrich import git as gitmod
from agent_standup.enrich import jira
from agent_standup.secrets import SecretError

SITE = JiraSite("https://acme.atlassian.net", "me@acme.com", "env:T", ("APP",))
ICT = timezone(timedelta(hours=7))


def issue_json(summary="History page loads forever", status="In Progress", category="indeterminate"):
    return json.dumps({"fields": {"summary": summary, "status": {"name": status, "statusCategory": {"key": category}}}}).encode()


def test_fetch_issue_builds_request_and_parses():
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["auth"], seen["timeout"] = req.full_url, req.get_header("Authorization"), timeout
        return issue_json()

    issue = jira.fetch_issue(SITE, "tok", "APP-9588", opener)
    assert seen["url"] == "https://acme.atlassian.net/rest/api/3/issue/APP-9588?fields=summary,status"
    assert seen["auth"] == "Basic " + base64.b64encode(b"me@acme.com:tok").decode()
    assert seen["timeout"] == 10
    assert issue.title == "History page loads forever"
    assert issue.status == "In Progress"
    assert issue.category == "indeterminate"
    assert issue.url == "https://acme.atlassian.net/browse/APP-9588"


def test_fetch_issue_failure_returns_none():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

    assert jira.fetch_issue(SITE, "tok", "APP-1", opener) is None
    assert jira.fetch_issue(SITE, "tok", "APP-1", lambda r, t: b"not json") is None


def test_fetch_all_skips_unknown_prefix_and_bad_token():
    cfg = Config(jira=(SITE, JiraSite("https://other.net", "a@b", "env:MISSING", ("LM",))))

    def resolve(ref):
        if ref == "env:MISSING":
            raise SecretError("nope")
        return "tok"

    out = jira.fetch_all(cfg, ["APP-1", "APP-1", "LM-2", "OPS-3"], resolve_secret=resolve, opener=lambda r, t: issue_json())
    assert set(out) == {"APP-1"}


def test_check_auth():
    assert jira.check_auth(SITE, "tok", lambda r, t: b"{}")

    def denied(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, None)

    assert not jira.check_auth(SITE, "tok", denied)


def test_repo_info_commands_and_parsing():
    calls = []
    answers = {
        "rev-parse": "fix/APP-9588\n",
        "status": " M src/a.ts\n",
        "config": "me@acme.com\n",
        "log": "a1b2c3d fix(history): stop spinner\n9f8e7d6 test: cover 403\n",
    }

    def run(args):
        calls.append(args)
        return answers[args[2]]

    info = gitmod.repo_info("/r/app", date(2026, 10, 8), ICT, run)
    assert info.branch == "fix/APP-9588"
    assert info.dirty is True
    assert info.commits == ("a1b2c3d fix(history): stop spinner", "9f8e7d6 test: cover 403")
    log_call = next(c for c in calls if c[2] == "log")
    assert "--since=2026-10-08T00:00:00+07:00" in log_call
    assert "--until=2026-10-09T00:00:00+07:00" in log_call
    assert "--author=me@acme.com" in log_call


def test_repo_info_caps_commits():
    log = "".join(f"{i:07x} commit {i}\n" for i in range(40))
    answers = {"rev-parse": "main\n", "status": "", "config": "me@acme.com\n", "log": log}
    info = gitmod.repo_info("/r/app", date(2026, 10, 8), ICT, lambda args: answers[args[2]])
    assert len(info.commits) == 16
    assert info.commits[0] == "0000000 commit 0"
    assert info.commits[-1] == "… 25 more"


def test_repo_info_without_email_has_no_commits():
    info = gitmod.repo_info("/r/app", date(2026, 10, 8), ICT, lambda args: "" if args[2] != "rev-parse" else "main\n")
    assert info.commits == ()
    assert info.dirty is False


def test_repo_info_git_failure():
    info = gitmod.repo_info("/gone", date(2026, 10, 8), ICT, lambda args: None)
    assert info == gitmod.GitInfo("/gone", None, (), False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_enrich.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_standup.enrich'`

- [ ] **Step 3: Implement `enrich/jira.py`**

Create empty `src/agent_standup/enrich/__init__.py`, then `src/agent_standup/enrich/jira.py`:

```python
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from agent_standup import secrets
from agent_standup.config import Config, JiraSite
from agent_standup.models import JiraIssue

Opener = Callable[[urllib.request.Request, float], bytes]


def _open(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _request(site: JiraSite, token: str, path: str) -> urllib.request.Request:
    auth = base64.b64encode(f"{site.email}:{token}".encode()).decode()
    return urllib.request.Request(f"{site.base_url}{path}", headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})


def fetch_issue(site: JiraSite, token: str, key: str, opener: Opener = _open) -> JiraIssue | None:
    req = _request(site, token, f"/rest/api/3/issue/{urllib.parse.quote(key)}?fields=summary,status")
    try:
        data = json.loads(opener(req, 10))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    fields = data.get("fields") or {}
    status = fields.get("status") or {}
    return JiraIssue(
        key=key,
        title=fields.get("summary") or "",
        status=status.get("name"),
        category=(status.get("statusCategory") or {}).get("key"),
        url=f"{site.base_url}/browse/{key}",
    )


def fetch_all(cfg: Config, keys: list[str], resolve_secret=secrets.resolve, opener: Opener = _open) -> dict[str, JiraIssue]:
    tokens: dict[str, str | None] = {}
    jobs: list[tuple[JiraSite, str]] = []
    for key in sorted(set(keys)):
        site = cfg.jira_site_for(key)
        if site is None:
            continue
        if site.base_url not in tokens:
            try:
                tokens[site.base_url] = resolve_secret(site.api_token)
            except secrets.SecretError:
                tokens[site.base_url] = None
        if tokens[site.base_url]:
            jobs.append((site, key))
    if not jobs:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda job: fetch_issue(job[0], tokens[job[0].base_url], job[1], opener), jobs))
    return {issue.key: issue for issue in results if issue}


def check_auth(site: JiraSite, token: str, opener: Opener = _open) -> bool:
    try:
        opener(_request(site, token, "/rest/api/3/myself"), 10)
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    return True
```

- [ ] **Step 4: Implement `enrich/git.py`**

`src/agent_standup/enrich/git.py`:

```python
import subprocess
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Callable

from agent_standup.day import local_tz
from agent_standup.models import GitInfo

Runner = Callable[[list[str]], str | None]
MAX_COMMITS = 15


def _git(args: list[str]) -> str | None:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def repo_info(root: str, day: date, tz: tzinfo | None, run: Runner = _git) -> GitInfo:
    branch_out = run(["-C", root, "rev-parse", "--abbrev-ref", "HEAD"])
    if branch_out is None:
        return GitInfo(root, None, (), False)
    branch = branch_out.strip() or None
    dirty = bool((run(["-C", root, "status", "--porcelain"]) or "").strip())
    email = (run(["-C", root, "config", "user.email"]) or "").strip()
    commits: tuple[str, ...] = ()
    if email:
        start = datetime.combine(day, time.min, tzinfo=local_tz(tz))
        end = start + timedelta(days=1)
        out = run(["-C", root, "log", "--all", f"--since={start.isoformat()}", f"--until={end.isoformat()}",
                   f"--author={email}", "--format=%h %s"]) or ""
        lines = [line for line in out.splitlines() if line.strip()]
        if len(lines) > MAX_COMMITS:
            lines = lines[:MAX_COMMITS] + [f"… {len(lines) - MAX_COMMITS} more"]
        commits = tuple(lines)
    return GitInfo(root, branch, commits, dirty)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Stop for review**

Print changed files and the pytest summary. Do not commit.

---

### Task 6: Engines and summarization (with fallback report)

**Files:**
- Create: `src/agent_standup/engines.py`, `src/agent_standup/summarize.py`
- Test: `tests/test_summarize.py`

**Interfaces:**
- Consumes: `models.*`, `config.Config`, `group.KEY_RE`
- Produces:
  - `engines.EngineError(Exception)`
  - `engines.pick(engine: str, which=shutil.which) -> str` returning `"claude"` or `"codex"`
  - `engines.claude_cmd() -> list[str]`, `engines.codex_cmd(out_file: Path) -> list[str]`
  - `engines.run(engine: str, prompt: str, workdir: Path, runner=subprocess.run, timeout: int = 180) -> str`
  - `summarize.SummaryError(Exception)`
  - `summarize.evidence_payload(evidence: list[ProjectEvidence], cfg: Config) -> list[dict]`
  - `summarize.build_prompt(day: date, evidence: list[ProjectEvidence], cfg: Config, limit: int = 60000) -> str`
  - `summarize.extract_json(text: str) -> dict`
  - `summarize.parse_report(text: str, day: date, evidence: list[ProjectEvidence], cfg: Config) -> Report`
  - `summarize.fallback_report(day: date, evidence: list[ProjectEvidence], cfg: Config) -> Report`
  - `summarize.summarize(day: date, evidence: list[ProjectEvidence], cfg: Config, workdir: Path, pick=engines.pick, run=engines.run) -> Report`

- [ ] **Step 1: Write the failing tests**

`tests/test_summarize.py`:

```python
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
    assert engines.claude_cmd() == ["claude", "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config", "--no-session-persistence"]
    assert engines.codex_cmd(Path("/tmp/o.txt")) == ["codex", "exec", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check", "--color", "never", "-o", "/tmp/o.txt", "-"]


def test_run_claude_reads_result_field(tmp_path):
    def runner(cmd, **kw):
        assert kw["input"] == "PROMPT" and kw["cwd"] == tmp_path / "w" and kw["timeout"] == 180
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"type": "result", "result": "ANSWER"}), stderr="")

    assert engines.run("claude", "PROMPT", tmp_path / "w", runner) == "ANSWER"


def test_run_codex_reads_output_file(tmp_path):
    def runner(cmd, **kw):
        Path(cmd[cmd.index("-o") + 1]).write_text("CODEX ANSWER")
        return subprocess.CompletedProcess(cmd, 0, stdout="noise", stderr="")

    assert engines.run("codex", "PROMPT", tmp_path, runner) == "CODEX ANSWER"


def test_run_errors_become_engine_error(tmp_path):
    def fails(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="auth required")

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 180)

    with pytest.raises(engines.EngineError, match="auth required"):
        engines.run("claude", "P", tmp_path, fails)
    with pytest.raises(engines.EngineError, match="timed out"):
        engines.run("codex", "P", tmp_path, slow)


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


def test_summarize_happy_path(tmp_path):
    report = summarize(DAY, evidence(), CFG, tmp_path, pick=lambda e: "claude", run=lambda e, p, w: json.dumps(GOOD))
    assert report.warning is None
    assert report.projects[0].items[0].status == "done"


@pytest.mark.parametrize("run", [
    lambda e, p, w: "garbage",
    lambda e, p, w: (_ for _ in ()).throw(engines.EngineError("claude timed out after 180s")),
])
def test_summarize_falls_back_with_warning(tmp_path, run):
    report = summarize(DAY, evidence(), CFG, tmp_path, pick=lambda e: "claude", run=run)
    assert report.warning and report.warning.startswith("AI summary unavailable")
    assert report.projects[0].items[0].status is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_summarize.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_standup.engines'`

- [ ] **Step 3: Implement `engines.py`**

`src/agent_standup/engines.py`:

```python
import json
import shutil
import subprocess
from pathlib import Path


class EngineError(Exception):
    pass


def pick(engine: str, which=shutil.which) -> str:
    if engine == "auto":
        for name in ("claude", "codex"):
            if which(name):
                return name
        raise EngineError("neither `claude` nor `codex` found on PATH")
    if not which(engine):
        raise EngineError(f"`{engine}` not found on PATH")
    return engine


def claude_cmd() -> list[str]:
    return ["claude", "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config", "--no-session-persistence"]


def codex_cmd(out_file: Path) -> list[str]:
    return ["codex", "exec", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check", "--color", "never", "-o", str(out_file), "-"]


def run(engine: str, prompt: str, workdir: Path, runner=subprocess.run, timeout: int = 180) -> str:
    workdir.mkdir(parents=True, exist_ok=True)
    out_file = workdir / "codex-last-message.txt"
    cmd = claude_cmd() if engine == "claude" else codex_cmd(out_file)
    if engine == "codex":
        out_file.unlink(missing_ok=True)
    try:
        result = runner(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=workdir)
    except subprocess.TimeoutExpired:
        raise EngineError(f"{engine} timed out after {timeout}s") from None
    except OSError as e:
        raise EngineError(f"{engine} failed to start: {e}") from None
    if result.returncode != 0:
        raise EngineError(f"{engine} exited {result.returncode}: {(result.stderr or '')[-500:]}")
    if engine == "codex":
        return out_file.read_text() if out_file.exists() else result.stdout
    try:
        return json.loads(result.stdout)["result"]
    except (ValueError, KeyError, TypeError):
        return result.stdout
```

- [ ] **Step 4: Implement `summarize.py`**

`src/agent_standup/summarize.py`:

```python
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
        text = run(engine, build_prompt(day, evidence, cfg), workdir)
        return parse_report(text, day, evidence, cfg)
    except (engines.EngineError, SummaryError) as e:
        report = fallback_report(day, evidence, cfg)
        report.warning = f"AI summary unavailable ({e}); showing raw activity."
        return report
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 6: Stop for review**

Print changed files and the pytest summary. Do not commit.

---

### Task 7: Rendering (Slack mrkdwn + Markdown)

**Files:**
- Create: `src/agent_standup/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `models.Report`, `models.ProjectReport`, `models.ReportItem`
- Produces:
  - `render.to_slack(report: Report, limit: int = 39000) -> str`
  - `render.to_markdown(report: Report) -> str`
  - `render.LABELS: dict[str, str]`

- [ ] **Step 1: Write the failing tests**

`tests/test_render.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_standup.render'`

- [ ] **Step 3: Implement `render.py`**

`src/agent_standup/render.py`:

```python
from agent_standup.models import Report, ReportItem

LABELS = {"done": "✅ Done", "in_progress": "🟡 In progress", "blocked": "🔴 Blocked"}
TRUNCATED = "…(truncated, full report in the local archive)"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _detail(item: ReportItem) -> str | None:
    parts = []
    if item.status in LABELS:
        parts.append(LABELS[item.status] + (f": {item.note}" if item.note else ""))
    elif item.note:
        parts.append(item.note)
    if item.jira_status:
        parts.append(f"(Jira: {item.jira_status})")
    return " ".join(parts) or None


def _header(report: Report) -> str:
    return f"Daily report: {report.day:%a %d/%m}"


def to_slack(report: Report, limit: int = 39000) -> str:
    lines = [f"*{_header(report)}*"]
    if report.warning:
        lines.append(f"_⚠️ {_esc(report.warning)}_")
    if not report.projects:
        lines += ["", f"No agent activity recorded for {report.day.isoformat()}."]
    for project in report.projects:
        lines += ["", f"*{_esc(project.name)}*", "Yesterday I worked on:"]
        for item in project.items:
            key = f"<{item.url}|{item.key}>" if item.key and item.url else (item.key or "")
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
        for item in project.items:
            key = f"[{item.key}]({item.url})" if item.key and item.url else (item.key or "")
            lines.append("- " + (f"{key}: {item.title}" if key and item.title else key or item.title))
            detail = _detail(item)
            if detail:
                lines.append(f"  - {detail}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 5: Stop for review**

Print changed files and the pytest summary. Do not commit.

---

### Task 8: Delivery, pipeline, and CLI (`init`, `run`, `send`)

**Files:**
- Create: `src/agent_standup/deliver.py`, `src/agent_standup/pipeline.py`, `src/agent_standup/cli.py`
- Test: `tests/test_deliver.py`, `tests/test_pipeline.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `deliver.DeliveryError(Exception)`
  - `deliver.post_slack(url: str, text: str, opener=..., sleep=time.sleep) -> None` (1 attempt + 3 retries at 2/4/8 s on network errors and 5xx; 4xx fails immediately)
  - `deliver.archive(markdown: str, day: date, base: Path) -> Path`
  - `deliver.already_sent(day: date, base: Path) -> bool`, `deliver.mark_sent(day: date, base: Path) -> None`
  - `pipeline.build_report(cfg: Config, day: date, *, data: Path, use_ai: bool = True, collect=..., resolver=None, jira_fetch=..., git_info=..., summarizer=...) -> Report | None`
  - `pipeline.send(cfg: Config, today: date, *, data: Path, build=build_report, post=deliver.post_slack, resolve=secrets.resolve) -> str` returning one of `"sent"`, `"already-sent"`, `"empty"`
  - `cli.main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

`tests/test_deliver.py`:

```python
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
```

`tests/test_pipeline.py`:

```python
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
    assert "Daily report: Fri 09/10" in posted[0][1]
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
```

`tests/test_cli.py`:

```python
from agent_standup import cli


def test_init_writes_template_once(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["init"]) == 0
    path = tmp_path / "agent-standup" / "config.toml"
    assert "engine = \"auto\"" in path.read_text()
    assert cli.main(["init"]) == 2
    assert cli.main(["init", "--force"]) == 0


def test_run_dry_run_prints_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    cfg_dir = tmp_path / "cfg" / "agent-standup"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(f'claude_dir = "{tmp_path / "none"}"\ncodex_dir = "{tmp_path / "none"}"\n')
    assert cli.main(["run", "--date", "2026-10-08", "--dry-run", "--no-ai"]) == 0
    assert "No agent activity recorded for 2026-10-08." in capsys.readouterr().out


def test_missing_config_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["run", "--dry-run"]) == 2
    assert "agent-standup init" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_deliver.py tests/test_pipeline.py tests/test_cli.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_standup.deliver'`

- [ ] **Step 3: Implement `deliver.py`**

`src/agent_standup/deliver.py`:

```python
import json
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path


class DeliveryError(Exception):
    pass


def _open(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def post_slack(url: str, text: str, opener=_open, sleep=time.sleep) -> None:
    body = json.dumps({"text": text, "mrkdwn": True}).encode()
    for delay in (2, 4, 8, None):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            opener(req, 15)
            return
        except urllib.error.HTTPError as e:
            if e.code < 500:
                raise DeliveryError(f"Slack rejected the message: HTTP {e.code}") from None
            reason = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = str(getattr(e, "reason", e))
        if delay is None:
            raise DeliveryError(f"Slack unreachable after retries: {reason}")
        sleep(delay)


def archive(markdown: str, day: date, base: Path) -> Path:
    path = base / "reports" / f"{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown)
    return path


def _marker(day: date, base: Path) -> Path:
    return base / "sent" / day.isoformat()


def already_sent(day: date, base: Path) -> bool:
    return _marker(day, base).exists()


def mark_sent(day: date, base: Path) -> None:
    path = _marker(day, base)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
```

- [ ] **Step 4: Implement `pipeline.py`**

`src/agent_standup/pipeline.py`:

```python
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
```

- [ ] **Step 5: Implement `cli.py` (init/run/send; schedule and doctor are wired in Task 9)**

`src/agent_standup/cli.py`:

```python
import argparse
import logging
import sys
from datetime import date, datetime

from agent_standup import config, paths, pipeline, render
from agent_standup.day import previous_workday
from agent_standup.deliver import DeliveryError
from agent_standup.secrets import SecretError

log = logging.getLogger("agent_standup")


def _today(cfg: config.Config) -> date:
    return datetime.now(cfg.tz()).date() if cfg.tz() else datetime.now().astimezone().date()


def _cmd_init(args) -> int:
    path = paths.config_path()
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)", file=sys.stderr)
        return 2
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.TEMPLATE)
    print(f"wrote {path}")
    return 0


def _cmd_run(args, cfg: config.Config) -> int:
    day = date.fromisoformat(args.date) if args.date else previous_workday(_today(cfg), cfg.holidays)
    report = pipeline.build_report(cfg, day, data=paths.data_dir(), use_ai=not args.no_ai)
    report = report or pipeline.Report(day, [])
    print(render.to_slack(report))
    return 0


def _cmd_send(args, cfg: config.Config) -> int:
    status = pipeline.send(cfg, _today(cfg), data=paths.data_dir())
    log.info("send: %s", status)
    print(status)
    return 0


def _setup_logging() -> None:
    path = paths.log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=path, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-standup", description="Daily standup from your Claude Code and Codex sessions.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init", help="write a config template")
    p_init.add_argument("--force", action="store_true")
    p_run = sub.add_parser("run", help="build a report and print it")
    p_run.add_argument("--date", help="YYYY-MM-DD (default: previous working day)")
    p_run.add_argument("--dry-run", action="store_true", help="print only (run never sends)")
    p_run.add_argument("--no-ai", action="store_true", help="skip the AI summary")
    sub.add_parser("send", help="build yesterday's report and post it to Slack")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init":
        return _cmd_init(args)
    try:
        cfg = config.load(paths.config_path())
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    try:
        if args.command == "run":
            return _cmd_run(args, cfg)
        _setup_logging()
        return _cmd_send(args, cfg)
    except (config.ConfigError, SecretError) as e:
        log.error("%s", e)
        print(e, file=sys.stderr)
        return 2
    except DeliveryError as e:
        log.error("%s", e)
        print(e, file=sys.stderr)
        return 1
```

`pipeline.Report` is referenced in `_cmd_run`; `pipeline.py` already imports `Report` from `agent_standup.models`, so the attribute exists.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Smoke-test on real logs without AI and without sending**

```bash
XDG_CONFIG_HOME=$(mktemp -d) sh -c '.venv/bin/agent-standup init && sed -i "" "s/^extra_ticket_prefixes = \[\]/extra_ticket_prefixes = [\"APP\", \"LM\"]/" "$XDG_CONFIG_HOME/agent-standup/config.toml" && .venv/bin/agent-standup run --date 2026-10-08 --no-ai --dry-run'
```

Expected: projects for the user's real repos with `LM-1491`, `LM-1886`, `LM-1901` listed and no `APP-9610`. (`APP-9588` is only agent-mentioned that day, so it appears only in the AI version.) Paste the output in the review report.

- [ ] **Step 8: Stop for review**

Print changed files and the pytest summary. Do not commit.

---

### Task 9: launchd schedule, doctor, README, CI, license

**Files:**
- Create: `src/agent_standup/schedule.py`, `src/agent_standup/doctor.py`, `README.md` (replace the empty file), `LICENSE`, `.github/workflows/ci.yml`
- Modify: `src/agent_standup/cli.py` (add `schedule` and `doctor` subcommands)
- Test: `tests/test_schedule.py`, extend `tests/test_cli.py`

**Interfaces:**
- Consumes: `config.Config`, `paths.log_path`, `secrets`, `engines.pick`, `enrich.jira.check_auth`
- Produces:
  - `schedule.LABEL = "dev.agent-standup"`
  - `schedule.plist_path() -> Path`
  - `schedule.build_plist(program: list[str], hour: int, minute: int, path_env: str, log: Path) -> bytes`
  - `schedule.program_args(which=shutil.which) -> list[str]`
  - `schedule.install(cfg: Config, run=subprocess.run, which=shutil.which, env=os.environ) -> Path`
  - `schedule.uninstall(run=subprocess.run) -> None`
  - `doctor.run_checks(cfg: Config, which=shutil.which, resolve=secrets.resolve, jira_auth=jira.check_auth) -> list[tuple[bool, str]]`

- [ ] **Step 1: Write the failing tests**

`tests/test_schedule.py`:

```python
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
```

Append to `tests/test_cli.py`:

```python
def test_doctor_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "agent-standup"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(f'claude_dir = "{tmp_path / "none"}"\ncodex_dir = "{tmp_path / "none"}"\n')
    assert cli.main(["doctor"]) == 1
    assert "FAIL" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_schedule.py tests/test_cli.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_standup.schedule'`

- [ ] **Step 3: Implement `schedule.py`**

`src/agent_standup/schedule.py`:

```python
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from agent_standup import paths
from agent_standup.config import Config

LABEL = "dev.agent-standup"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(program: list[str], hour: int, minute: int, path_env: str, log: Path) -> bytes:
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": program,
        "StartCalendarInterval": [{"Weekday": d, "Hour": hour, "Minute": minute} for d in range(1, 6)],
        "EnvironmentVariables": {"PATH": path_env},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "RunAtLoad": False,
    })


def program_args(which=shutil.which) -> list[str]:
    exe = which("agent-standup")
    return [exe, "send"] if exe else [sys.executable, "-m", "agent_standup", "send"]


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(cfg: Config, run=subprocess.run, which=shutil.which, env=os.environ) -> Path:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    log = paths.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_plist(program_args(which), cfg.hour, cfg.minute, env.get("PATH", "/usr/bin:/bin"), log))
    run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True)
    result = run(["launchctl", "bootstrap", _domain(), str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip()}")
    return path


def uninstall(run=subprocess.run) -> None:
    run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True)
    plist_path().unlink(missing_ok=True)
```

- [ ] **Step 4: Implement `doctor.py`**

`src/agent_standup/doctor.py`:

```python
import shutil

from agent_standup import engines, secrets
from agent_standup.config import Config
from agent_standup.enrich import jira


def run_checks(cfg: Config, which=shutil.which, resolve=secrets.resolve, jira_auth=jira.check_auth) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []
    try:
        results.append((True, f"engine: {engines.pick(cfg.engine, which=which)}"))
    except engines.EngineError as e:
        results.append((False, f"engine: {e}"))

    if not cfg.slack_webhook:
        results.append((False, "slack webhook_url is not set"))
    else:
        if secrets.is_plain(cfg.slack_webhook):
            results.append((False, "slack webhook is stored in plain text; use env: or keychain:"))
        try:
            url = resolve(cfg.slack_webhook) or ""
            results.append((url.startswith("https://hooks.slack.com/"), "slack webhook resolves to a hooks.slack.com URL"))
        except secrets.SecretError as e:
            results.append((False, f"slack webhook: {e}"))

    for name, path in (("claude logs", cfg.claude_dir), ("codex logs", cfg.codex_dir)):
        results.append((path.is_dir(), f"{name}: {path}"))

    for site in cfg.jira:
        try:
            token = resolve(site.api_token)
            results.append((bool(token) and jira_auth(site, token), f"jira {site.base_url}"))
        except secrets.SecretError as e:
            results.append((False, f"jira {site.base_url}: {e}"))
    return results
```

- [ ] **Step 5: Wire `schedule` and `doctor` into `cli.py`**

In `build_parser()` add after the `send` subparser:

```python
    p_schedule = sub.add_parser("schedule", help="install the weekday launchd job (macOS)")
    p_schedule.add_argument("--uninstall", action="store_true")
    sub.add_parser("doctor", help="check configuration and environment")
```

Add these functions above `main`:

```python
def _cmd_schedule(args, cfg: config.Config) -> int:
    from agent_standup import schedule

    if sys.platform != "darwin":
        print("schedule is macOS-only; use cron to run `agent-standup send` on other systems", file=sys.stderr)
        return 2
    if args.uninstall:
        schedule.uninstall()
        print("removed launchd job")
        return 0
    path = schedule.install(cfg)
    print(f"installed {path}: weekdays at {cfg.hour:02d}:{cfg.minute:02d}")
    return 0


def _cmd_doctor(args, cfg: config.Config) -> int:
    from agent_standup import doctor

    results = doctor.run_checks(cfg)
    for ok, message in results:
        print(f"{'ok  ' if ok else 'FAIL'} {message}")
    return 0 if all(ok for ok, _ in results) else 1
```

Replace the dispatch block in `main` (the `try:` after config load) with:

```python
    try:
        if args.command == "run":
            return _cmd_run(args, cfg)
        if args.command == "schedule":
            return _cmd_schedule(args, cfg)
        if args.command == "doctor":
            return _cmd_doctor(args, cfg)
        _setup_logging()
        return _cmd_send(args, cfg)
    except (config.ConfigError, SecretError) as e:
        log.error("%s", e)
        print(e, file=sys.stderr)
        return 2
    except DeliveryError as e:
        log.error("%s", e)
        print(e, file=sys.stderr)
        return 1
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Write `LICENSE`, CI, and README**

`LICENSE`: the standard MIT license text with `Copyright (c) 2026 agent-standup contributors`.

`.github/workflows/ci.yml`:

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    strategy:
      matrix:
        os: [macos-latest, ubuntu-latest]
        python: ["3.11", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: pip install -e '.[dev]'
      - run: python -m pytest -q
```

`README.md` must contain these sections, in this order, with working commands:

1. **What it does** — one paragraph plus a sample Slack message (copy the layout from `tests/test_render.py::test_slack_layout`).
2. **Requirements** — macOS (for scheduling), Python 3.11+ (uv installs it), Claude Code CLI and/or Codex CLI logged in, a Slack workspace where you can add an app.
3. **Install** — `uv tool install git+https://github.com/OWNER/agent-standup` then `agent-standup init`. Note: replace `OWNER` when the repo is published.
4. **Slack webhook** — create an app at https://api.slack.com/apps → "From scratch" → Incoming Webhooks → On → "Add New Webhook to Workspace" → pick a private channel that only you are in. Store it: `security add-generic-password -a "$USER" -s agent-standup-slack -w` (paste the URL when prompted) and set `webhook_url = "keychain:agent-standup-slack"`.
5. **Jira (optional)** — create an API token at https://id.atlassian.com/manage-profile/security/api-tokens, store with `security add-generic-password -a "$USER" -s agent-standup-jira -w`, add a `[[jira]]` block with `prefixes`.
6. **Projects** — how project names are chosen (rules → git repo name → folder name) with a `[[projects]]` example.
7. **Try it** — `agent-standup doctor`, then `agent-standup run --dry-run`, then `agent-standup run --date YYYY-MM-DD --no-ai`.
8. **Schedule** — `agent-standup schedule` (weekdays at the configured time; runs on wake if the Mac was asleep; `--uninstall` to remove). Mention that `PATH` is captured at install time, so re-run `schedule` after installing claude/codex somewhere new.
9. **Privacy** — excerpts of your prompts and agent replies are sent to the engine you chose (Claude or Codex) to write the summary; only the summary goes to Slack; nothing else leaves your machine; the summarizer runs without saving its own session.
10. **Files** — config, reports archive, sent markers, log locations.
11. **Troubleshooting** — "AI summary unavailable" warning (run `doctor`, check the engine works from a plain terminal), nothing posted (check `~/Library/Logs/agent-standup.log`, sent marker already exists).

- [ ] **Step 8: Final verification**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

Run: `.venv/bin/agent-standup --help`
Expected: lists `init`, `run`, `send`, `schedule`, `doctor`.

Do **not** run `agent-standup schedule` or `agent-standup send` against the user's real Slack; the reviewer and the user do that together.

- [ ] **Step 9: Stop for review**

Print changed files and the pytest summary. Do not commit.
