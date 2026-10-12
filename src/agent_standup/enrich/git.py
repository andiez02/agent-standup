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
