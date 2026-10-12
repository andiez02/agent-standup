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
