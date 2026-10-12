import os
import subprocess
from typing import Callable

Runner = Callable[[list[str]], subprocess.CompletedProcess]


class SecretError(Exception):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def resolve(ref: str | None, runner: Runner = _run) -> str | None:
    if ref is None:
        return None
    if ref.startswith("env:"):
        name = ref[4:]
        value = os.environ.get(name)
        if not value:
            raise SecretError(f"environment variable {name} is not set")
        return value
    if ref.startswith("keychain:"):
        service = ref[9:]
        result = runner(["security", "find-generic-password", "-s", service, "-w"])
        if result.returncode != 0:
            raise SecretError(f"keychain item {service!r} not found")
        return result.stdout.strip()
    return ref


def is_plain(ref: str | None) -> bool:
    return bool(ref) and not ref.startswith(("env:", "keychain:"))
