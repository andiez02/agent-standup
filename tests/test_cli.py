from agent_standup import cli


def test_init_writes_template_once(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["init"]) == 0
    path = tmp_path / "agent-standup" / "config.toml"
    assert "engine = \"auto\"" in path.read_text()
    assert cli.main(["init"]) == 2
    assert cli.main(["init", "--force"]) == 0


def test_run_dry_run_prints_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    cfg_dir = tmp_path / "cfg" / "agent-standup"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.toml").write_text(f'claude_dir = "{tmp_path / "none"}"\ncodex_dir = "{tmp_path / "none"}"\n')
    assert cli.main(["run", "--date", "2026-10-08", "--dry-run", "--no-ai"]) == 0
    assert "No agent activity recorded for 2026-10-08." in capsys.readouterr().out


def test_missing_config_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["run", "--dry-run"]) == 2
    assert "agent-standup init" in capsys.readouterr().err


def test_doctor_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    cfg_dir = tmp_path / "agent-standup"
    cfg_dir.mkdir()
    (cfg_dir / "config.toml").write_text(f'claude_dir = "{tmp_path / "none"}"\ncodex_dir = "{tmp_path / "none"}"\n')
    assert cli.main(["doctor"]) == 1
    assert "FAIL" in capsys.readouterr().out
