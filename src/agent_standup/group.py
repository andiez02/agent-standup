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
