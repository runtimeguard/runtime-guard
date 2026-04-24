import os
import subprocess

from config import WORKSPACE_ROOT


def safe_subprocess_env() -> dict:
    source = os.environ.copy()
    allowed_exact = {
        "LANG",
        "LC_ALL",
        "TERM",
        "TZ",
        "PATH",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
    }
    allowed_prefixes = ("AIRG_",)
    blocked_exact = {
        "PYTHONPATH",
        "IFS",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "BASH_ENV",
        "ENV",
        "PROMPT_COMMAND",
        "GIT_SSH_COMMAND",
    }
    safe: dict[str, str] = {}
    for key, value in source.items():
        if key in blocked_exact:
            continue
        if key in allowed_exact or any(key.startswith(prefix) for prefix in allowed_prefixes):
            safe[key] = value
            continue
        lower = key.lower()
        if any(marker in lower for marker in ("api_key", "token", "secret", "password")):
            continue

    safe["HOME"] = WORKSPACE_ROOT
    if "PATH" not in safe:
        safe["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    if "LANG" not in safe:
        safe["LANG"] = "C"
    if "LC_ALL" not in safe:
        safe["LC_ALL"] = safe["LANG"]
    return safe


def run_shell_command(command: str, timeout_seconds: int):
    return subprocess.run(
        command,
        shell=True,
        executable="/bin/bash",
        cwd=WORKSPACE_ROOT,
        env=safe_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
