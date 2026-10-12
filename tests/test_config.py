import tomllib
from datetime import date
from pathlib import Path

import pytest

from agent_standup.config import TEMPLATE, ConfigError, from_dict, load


def test_defaults():
    cfg = from_dict({})
    assert cfg.engine == "auto"
    assert cfg.model is None
    assert (cfg.hour, cfg.minute) == (10, 0)
    assert cfg.ticket_prefixes() == frozenset()
    assert cfg.claude_dir == Path.home() / ".claude"


def test_full_config():
    cfg = from_dict({
        "engine": "codex",
        "model": "gpt-5",
        "schedule": {"hour": 9, "minute": 30},
        "holidays": ["2026-12-25"],
        "extra_ticket_prefixes": ["OPS"],
        "slack": {"webhook_url": "env:HOOK"},
        "jira": [{"base_url": "https://a.atlassian.net/", "email": "me@x.com", "api_token": "env:T", "prefixes": ["APP"]}],
        "projects": [{"name": "Storefront", "paths": ["~/Code/shop/**"], "prefixes": ["APP"]}],
    })
    assert cfg.engine == "codex"
    assert cfg.model == "gpt-5"
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


@pytest.mark.parametrize("model", [None, "", "   ", 1, True, []])
def test_invalid_model(model):
    with pytest.raises(ConfigError, match="model"):
        from_dict({"model": model})


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
