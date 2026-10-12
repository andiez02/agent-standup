import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from agent_standup import paths
from agent_standup.config import Config

LABEL = "dev.agent-standup"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(program: list[str], hour: int, minute: int, path_env: str, log: Path) -> bytes:
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": program,
        "StartCalendarInterval": [{"Weekday": d, "Hour": hour, "Minute": minute} for d in range(1, 6)],
        "EnvironmentVariables": {"PATH": path_env},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        "RunAtLoad": False,
    })


def program_args(which=shutil.which) -> list[str]:
    exe = which("agent-standup")
    return [exe, "send"] if exe else [sys.executable, "-m", "agent_standup", "send"]


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(cfg: Config, run=subprocess.run, which=shutil.which, env=os.environ) -> Path:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    log = paths.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_plist(program_args(which), cfg.hour, cfg.minute, env.get("PATH", "/usr/bin:/bin"), log))
    run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True)
    result = run(["launchctl", "bootstrap", _domain(), str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip()}")
    return path


def uninstall(run=subprocess.run) -> None:
    run(["launchctl", "bootout", f"{_domain()}/{LABEL}"], capture_output=True)
    plist_path().unlink(missing_ok=True)
