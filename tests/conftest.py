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
