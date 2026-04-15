import subprocess
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context
else:
    Context = Any

from approvals import issue_or_reuse_approval_token
from audit import append_log_entry, build_log_entry
from backup import MODIFYING_COMMAND_RE, backup_paths, extract_paths
from config import AGENT_ID, MAX_RETRIES, POLICY, SERVER_BUILD, WORKSPACE_ROOT, refresh_policy_if_changed
from executor import run_shell_command
from models import PolicyResult
from policy_engine import (
    check_policy,
    command_targets_backup_storage,
    execution_limits,
    has_shell_unsafe_control_chars,
    network_policy_check,
    normalize_for_audit,
    register_retry,
    shell_workspace_containment_check,
    truncate_output,
)
from runtime_context import activate_runtime_context, current_agent_session_id, reset_runtime_context
import script_sentinel


def server_info(ctx: Context | None = None) -> str:
    """Return runtime identity details for this AIRG server instance.

    Includes build id, active workspace root, and resolved base directory.
    """
    tokens = activate_runtime_context(ctx)
    from config import BASE_DIR
    try:
        return f"ai-runtime-guard build={SERVER_BUILD} workspace={WORKSPACE_ROOT} base_dir={BASE_DIR}"
    finally:
        reset_runtime_context(tokens)


def _default_sentinel_eval() -> dict[str, Any]:
    return {
        "enabled": False,
        "has_hits": False,
        "decision": "allowed",
        "mode": "match_original",
        "hits": [],
    }


def _script_sentinel_paths(sentinel_eval: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(hit.get("path", ""))
            for hit in sentinel_eval.get("hits", [])
            if str(hit.get("path", "")).strip()
        }
    )


def _script_sentinel_preview(sentinel_eval: dict[str, Any]) -> str:
    paths = _script_sentinel_paths(sentinel_eval)
    preview = ", ".join(paths[:3]) if paths else "script artifact"
    if len(paths) > 3:
        preview += ", ..."
    return preview


def _evaluate_policy_and_sentinel(
    command: str,
) -> tuple[PolicyResult, str | None, str | None, list[str], dict[str, Any]]:
    network_warning = None
    shell_containment_warning = None
    shell_containment_paths: list[str] = []
    sentinel_eval: dict[str, Any] = _default_sentinel_eval()

    if has_shell_unsafe_control_chars(command):
        return (
            PolicyResult(
                allowed=False,
                reason="Command contains disallowed control characters (newline, carriage return, or NUL)",
                decision_tier="blocked",
                matched_rule="command_control_characters",
            ),
            network_warning,
            shell_containment_warning,
            shell_containment_paths,
            sentinel_eval,
        )

    if command_targets_backup_storage(command):
        return (
            PolicyResult(
                allowed=False,
                reason="Command targets protected backup storage; use restore_backup for controlled recovery operations",
                decision_tier="blocked",
                matched_rule="backup_storage_protected",
            ),
            network_warning,
            shell_containment_warning,
            shell_containment_paths,
            sentinel_eval,
        )

    net_allowed, net_reason = network_policy_check(command)
    mode = str(POLICY.get("network", {}).get("enforcement_mode", "off")).lower()
    if not net_allowed:
        return (
            PolicyResult(
                allowed=False,
                reason=net_reason or "Network command blocked by policy",
                decision_tier="blocked",
                matched_rule="network_policy",
            ),
            network_warning,
            shell_containment_warning,
            shell_containment_paths,
            sentinel_eval,
        )

    if mode == "monitor" and net_reason:
        network_warning = net_reason

    containment_allowed, containment_reason, containment_paths = shell_workspace_containment_check(command)
    if not containment_allowed:
        return (
            PolicyResult(
                allowed=False,
                reason=containment_reason or "Shell workspace containment blocked command.",
                decision_tier="blocked",
                matched_rule="execution.shell_workspace_containment",
            ),
            network_warning,
            shell_containment_warning,
            containment_paths,
            sentinel_eval,
        )

    if containment_reason:
        shell_containment_warning = containment_reason
        shell_containment_paths = containment_paths

    result = check_policy(command)
    if not result.allowed:
        return result, network_warning, shell_containment_warning, shell_containment_paths, sentinel_eval

    sentinel_eval = script_sentinel.evaluate_command_execution(
        command,
        agent_id=AGENT_ID,
        session_id=current_agent_session_id(),
    )
    sentinel_decision = str(sentinel_eval.get("decision", "allowed"))
    if sentinel_eval.get("has_hits") and sentinel_decision in {"blocked", "requires_confirmation"}:
        preview = _script_sentinel_preview(sentinel_eval)
        if sentinel_decision == "blocked":
            result = PolicyResult(
                allowed=False,
                reason=(
                    "Script Sentinel preserved policy intent: execution of a tagged script artifact "
                    f"is blocked for this agent ({preview})."
                ),
                decision_tier="blocked",
                matched_rule="script_sentinel",
            )
        else:
            result = PolicyResult(
                allowed=False,
                reason=(
                    "Script Sentinel preserved policy intent: execution of a tagged script artifact "
                    f"requires explicit confirmation for this agent ({preview})."
                ),
                decision_tier="requires_confirmation",
                matched_rule="script_sentinel",
            )
    return result, network_warning, shell_containment_warning, shell_containment_paths, sentinel_eval


def _retry_state(command: str, result: PolicyResult) -> tuple[int, bool]:
    server_retry_count = 0
    final_block = False
    if not result.allowed and result.decision_tier != "requires_confirmation":
        server_retry_count = register_retry(command, result.decision_tier, result.matched_rule)
        final_block = server_retry_count >= MAX_RETRIES
    return server_retry_count, final_block


def _script_sentinel_log_fields(sentinel_eval: dict[str, Any]) -> dict[str, Any]:
    if not sentinel_eval.get("has_hits"):
        return {}
    return {
        "script_sentinel_hits_count": len(sentinel_eval.get("hits", [])),
        "script_sentinel_decision": sentinel_eval.get("decision", "allowed"),
        "script_sentinel_mode": sentinel_eval.get("mode", "match_original"),
        "script_sentinel_paths": [
            str(hit.get("path", ""))
            for hit in sentinel_eval.get("hits", [])
            if str(hit.get("path", "")).strip()
        ],
        "script_sentinel_hashes": [
            str(hit.get("content_hash", ""))
            for hit in sentinel_eval.get("hits", [])
            if str(hit.get("content_hash", "")).strip()
        ],
        "script_sentinel_allowance_applied": [
            str(hit.get("allowance_applied", ""))
            for hit in sentinel_eval.get("hits", [])
            if str(hit.get("allowance_applied", "")).strip()
        ],
    }


def _append_script_sentinel_events(log_entry: dict[str, Any], sentinel_eval: dict[str, Any]) -> None:
    if not sentinel_eval.get("has_hits"):
        return
    checked_event = {
        **log_entry,
        "source": "mcp-server",
        "event": "script_sentinel_execute_checked",
    }
    append_log_entry(checked_event)
    decision = str(sentinel_eval.get("decision", "allowed"))
    if decision == "blocked":
        append_log_entry({**checked_event, "event": "script_sentinel_blocked"})
    elif decision == "requires_confirmation":
        append_log_entry({**checked_event, "event": "script_sentinel_requires_confirmation"})
    for hit in sentinel_eval.get("hits", []):
        allowance_type = str(hit.get("allowance_applied", "")).strip()
        if allowance_type == "once":
            append_log_entry({**checked_event, "event": "script_sentinel_dismissed_once"})
        elif allowance_type == "persistent":
            append_log_entry({**checked_event, "event": "script_sentinel_trusted"})


def _requires_confirmation_response(command: str, result: PolicyResult, sentinel_eval: dict[str, Any]) -> str:
    approval_paths = extract_paths(command)
    token, expires_at = issue_or_reuse_approval_token(
        command,
        session_id=current_agent_session_id(),
        affected_paths=approval_paths,
    )
    sentinel_context = ""
    if sentinel_eval.get("has_hits"):
        sentinel_context = (
            "Script Sentinel context: policy-intent match detected for script execution target(s): "
            f"{_script_sentinel_preview(sentinel_eval)}\n"
        )
    return (
        f"[POLICY BLOCK] {result.reason}\n\n"
        "This command requires an explicit confirmation handshake.\n"
        f"{sentinel_context}"
        "Ask a human operator to approve it via the control-plane GUI/API using this exact command and token, then retry execute_command:\n"
        f"approval_token={token}\n"
        f"token_expires_at={expires_at.isoformat()}Z"
    )


def _blocked_response(result: PolicyResult, *, final_block: bool, server_retry_count: int) -> str:
    if final_block:
        return (
            f"[POLICY BLOCK] {result.reason}\n\n"
            f"Maximum retries reached ({MAX_RETRIES}/{MAX_RETRIES}). "
            "This action is permanently blocked for the current request. "
            "No further attempts will be accepted."
        )
    attempts_remaining = MAX_RETRIES - server_retry_count
    return (
        f"[POLICY BLOCK] {result.reason}\n\n"
        f"You have {attempts_remaining} attempt(s) remaining. "
        "Please retry execute_command with a safer alternative command "
        f"(server attempt {server_retry_count}/{MAX_RETRIES})."
    )


def _maybe_backup_modifying_command(command: str, log_entry: dict[str, Any]) -> None:
    if not MODIFYING_COMMAND_RE.search(command):
        return
    affected = extract_paths(command)
    if not (affected and POLICY.get("audit", {}).get("backup_enabled", True)):
        return
    backup_location = backup_paths(affected)
    if backup_location:
        append_log_entry(
            {
                **log_entry,
                "source": "mcp-server",
                "backup_location": backup_location,
                "event": "backup_created",
            }
        )


def _execute_shell(command: str) -> str:
    timeout_seconds, max_output_chars = execution_limits()
    try:
        proc = run_shell_command(command, timeout_seconds)
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout_seconds} seconds"

    stdout = truncate_output(proc.stdout or "", max_output_chars)
    stderr = truncate_output(proc.stderr or "", max_output_chars)

    if proc.returncode != 0:
        return stderr or f"Command exited with code {proc.returncode}"
    return stdout


def execute_command(command: str, retry_count: int = 0, ctx: Context | None = None) -> str:
    """Execute a shell command after full AIRG policy and approval checks.

    The command is evaluated against network/workspace containment, command-tier
    policy, Script Sentinel continuity checks, and optional confirmation gates
    before execution.
    """
    context_tokens = activate_runtime_context(ctx)
    refresh_policy_if_changed()
    affected_paths: list[str] = []

    try:
        (
            result,
            network_warning,
            shell_containment_warning,
            shell_containment_paths,
            sentinel_eval,
        ) = _evaluate_policy_and_sentinel(command)

        if result.allowed:
            affected_paths = extract_paths(command)

        server_retry_count, final_block = _retry_state(command, result)

        log_entry = build_log_entry(
            "execute_command",
            result,
            command=command,
            normalized_command=normalize_for_audit(command),
            retry_count=retry_count,
            server_retry_count=server_retry_count,
            affected_paths_count=len(affected_paths),
            **({"network_warning": network_warning} if network_warning else {}),
            **({"shell_containment_warning": shell_containment_warning} if shell_containment_warning else {}),
            **({"shell_containment_offending_paths": shell_containment_paths} if shell_containment_paths else {}),
            **_script_sentinel_log_fields(sentinel_eval),
            **({"final_block": True} if final_block else {}),
        )
        append_log_entry(log_entry)
        _append_script_sentinel_events(log_entry, sentinel_eval)

        if not result.allowed:
            if result.decision_tier == "requires_confirmation":
                return _requires_confirmation_response(command, result, sentinel_eval)
            return _blocked_response(result, final_block=final_block, server_retry_count=server_retry_count)

        _maybe_backup_modifying_command(command, log_entry)
        return _execute_shell(command)
    finally:
        reset_runtime_context(context_tokens)
