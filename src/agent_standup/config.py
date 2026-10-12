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
    model: str | None = None
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

    model = raw.get("model")
    if "model" in raw and (not isinstance(model, str) or not model.strip()):
        fail("model must be a non-empty string")

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
        model=model,
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
# model = "sonnet"        # claude default: sonnet; codex default: its own
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
