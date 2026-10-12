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
