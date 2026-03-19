import json
import os
import pathlib
import sys
from datetime import UTC, datetime
from typing import Any


REDIRECTS = {
    "Bash": "mcp__ai-runtime-guard__execute_command",
    "Write": "mcp__ai-runtime-guard__write_file",
    "Edit": "mcp__ai-runtime-guard__write_file",
    "MultiEdit": "mcp__ai-runtime-guard__write_file",
}
ALWAYS_ALLOW = {
    "Read",
    "Glob",
    "Grep",
    "LS",
    "Task",
    "WebSearch",
}
SENSITIVE_READ_SUFFIXES = (".env", ".pem", ".key")
HOOK_VERSION = "v2.0.dev2"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _hook_log_path() -> pathlib.Path:
    explicit = str(os.environ.get("AIRG_HOOK_LOG_PATH", "")).strip()
    if explicit:
        return pathlib.Path(explicit).expanduser().resolve()
    log_path = str(os.environ.get("AIRG_LOG_PATH", "")).strip()
    if log_path:
        return pathlib.Path(log_path).expanduser().resolve().with_name("hook_activity.log")
    return (pathlib.Path.home() / ".local" / "state" / "ai-runtime-guard" / "hook_activity.log").resolve()


def _append_log(entry: dict[str, Any]) -> None:
    try:
        path = _hook_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception:
        pass


def _emit_deny(reason: str) -> int:
    payload = {
        "decision": "deny",
        "action": "block",
        "blocked": True,
        "continue": False,
        "reason": reason,
        "message": reason,
    }
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()
    return 0


def _extract_detail(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "Bash":
        return str(tool_input.get("command", "")).strip()
    return str(tool_input.get("file_path", "")).strip()


def _common_log_fields(tool_name: str) -> dict[str, Any]:
    return {
        "timestamp": _utc_now(),
        "source": "airg-hook",
        "hook_version": HOOK_VERSION,
        "agent_id": str(os.environ.get("AIRG_AGENT_ID", "")).strip() or "unknown",
        "tool_name": tool_name,
    }


def _is_sensitive_read(tool_input: dict[str, Any]) -> bool:
    path = str(tool_input.get("file_path", "")).strip().lower()
    if not path:
        return False
    if any(path.endswith(suffix) for suffix in SENSITIVE_READ_SUFFIXES):
        return True
    if "/secrets/" in path:
        return True
    return False


def _allow() -> int:
    return 0


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw else {}
        if not isinstance(payload, dict):
            return _allow()
        tool_name = str(payload.get("tool_name", "")).strip()
        tool_input = payload.get("tool_input", {})
        if not isinstance(tool_input, dict):
            tool_input = {}

        # Always allow AIRG MCP tool calls.
        if tool_name.startswith("mcp__ai-runtime-guard__"):
            _append_log({**_common_log_fields(tool_name), "decision": "allow", "reason": "airg_mcp_tool"})
            return _allow()

        # Allow general read-only tools except sensitive read targets.
        if tool_name in ALWAYS_ALLOW:
            if tool_name == "Read" and _is_sensitive_read(tool_input):
                reason = "AIRG policy: sensitive native Read target restricted. Use mcp__ai-runtime-guard__read_file instead."
                _append_log(
                    {
                        **_common_log_fields(tool_name),
                        "decision": "deny",
                        "reason": reason,
                        "detail": _extract_detail(tool_name, tool_input),
                    }
                )
                return _emit_deny(reason)
            _append_log({**_common_log_fields(tool_name), "decision": "allow", "reason": "read_only_tool"})
            return _allow()

        if tool_name in REDIRECTS:
            target = REDIRECTS[tool_name]
            detail = _extract_detail(tool_name, tool_input)
            reason = (
                f"AIRG policy: native '{tool_name}' is restricted. "
                f"Use {target} instead."
            )
            if detail:
                reason = f"{reason}\nDetail: '{detail}'"
            _append_log(
                {
                    **_common_log_fields(tool_name),
                    "decision": "deny",
                    "reason": reason,
                    "detail": detail,
                    "redirect_tool": target,
                }
            )
            return _emit_deny(reason)

        _append_log({**_common_log_fields(tool_name), "decision": "allow", "reason": "unmapped_tool"})
        return _allow()
    except Exception as exc:
        _append_log(
            {
                **_common_log_fields("unknown"),
                "decision": "allow",
                "reason": f"fail_open:{type(exc).__name__}",
            }
        )
        return _allow()


if __name__ == "__main__":
    raise SystemExit(main())
