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
