import json
import shutil
import subprocess
from pathlib import Path


class EngineError(Exception):
    pass


def pick(engine: str, which=shutil.which) -> str:
    if engine == "auto":
        for name in ("claude", "codex"):
            if which(name):
                return name
        raise EngineError("neither `claude` nor `codex` found on PATH")
    if not which(engine):
        raise EngineError(f"`{engine}` not found on PATH")
    return engine


def claude_cmd(model: str | None = None) -> list[str]:
    return ["claude", "-p", "--output-format", "json", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--model", model or "sonnet"]


def codex_cmd(out_file: Path, model: str | None = None) -> list[str]:
    cmd = ["codex", "exec", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check", "--color", "never", "-o", str(out_file)]
    if model:
        cmd.extend(["-m", model])
    return cmd + ["-"]


def run(engine: str, prompt: str, workdir: Path, model: str | None = None, runner=subprocess.run, timeout: int = 180) -> str:
    workdir.mkdir(parents=True, exist_ok=True)
    out_file = workdir / "codex-last-message.txt"
    cmd = claude_cmd(model) if engine == "claude" else codex_cmd(out_file, model)
    if engine == "codex":
        out_file.unlink(missing_ok=True)
    try:
        result = runner(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=workdir)
    except subprocess.TimeoutExpired:
        raise EngineError(f"{engine} timed out after {timeout}s") from None
    except OSError as e:
        raise EngineError(f"{engine} failed to start: {e}") from None
    if result.returncode != 0:
        raise EngineError(f"{engine} exited {result.returncode}: {(result.stderr or '')[-500:]}")
    if engine == "codex":
        return out_file.read_text() if out_file.exists() else result.stdout
    try:
        return json.loads(result.stdout)["result"]
    except (ValueError, KeyError, TypeError):
        return result.stdout
