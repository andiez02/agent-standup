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
