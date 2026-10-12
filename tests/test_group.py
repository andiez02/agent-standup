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
