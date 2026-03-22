import json
import os
import pathlib
import shlex
import shutil
import sys
from datetime import UTC, datetime
from typing import Any

import agent_configs

CLAUDE_SCOPES = {"project", "local", "user"}

_CLAUDE_JSON_MISSING = (
    "~/.claude.json not found. Claude Code may not have been initialised for this user. "
    "Please run Claude Code at least once, then retry. If the Claude settings file is saved in "
    "a different location, please manually apply the MCP config using the Copy buttons."
)
_INVALID_JSON_MESSAGE = (
    "The target file contains invalid JSON. Please fix it manually or back it up before AIRG can apply."
)
AIRG_MCP_TOOLS = [
    "server_info",
    "restore_backup",
    "execute_command",
    "read_file",
    "write_file",
    "delete_file",
    "list_directory",
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _state_dir(paths: dict[str, pathlib.Path]) -> pathlib.Path:
    return paths["approval_db_path"].expanduser().resolve().parent


def _backups_dir(paths: dict[str, pathlib.Path]) -> pathlib.Path:
    out = _state_dir(paths) / "mcp-configs" / "backups"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _safe_slug(value: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in str(value or "").strip())
    out = "-".join(filter(None, out.split("-")))
    return out or "agent"


def _backup_file(paths: dict[str, pathlib.Path], target: pathlib.Path, agent_id: str) -> pathlib.Path | None:
    if not target.exists() or not target.is_file():
        return None
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_name = f"{_safe_slug(agent_id)}_{ts}_{target.name}"
    backup_path = _backups_dir(paths) / backup_name
    shutil.copy2(target, backup_path)
    return backup_path


def _home() -> pathlib.Path:
    return pathlib.Path.home().expanduser().resolve()


def _workspace_path(profile: dict[str, Any]) -> pathlib.Path:
    raw = str(profile.get("workspace", "")).strip()
    if not raw:
        raise ValueError("Workspace is required")
    workspace = pathlib.Path(raw).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError(f"Workspace does not exist or is not a directory: {workspace}")
    return workspace


def _normalize_scope(raw_scope: Any) -> str:
    requested = str(raw_scope or "").strip().lower()
    if requested in CLAUDE_SCOPES:
        return requested
    return "project"


def _server_process() -> tuple[str, list[str]]:
    explicit = str(os.environ.get("AIRG_SERVER_COMMAND", "")).strip()
    if explicit:
        parts = shlex.split(explicit)
        if parts:
            cmd = parts[0]
            args = parts[1:]
            if os.path.isabs(cmd):
                return cmd, args
            resolved = shutil.which(cmd)
            if resolved:
                return str(pathlib.Path(resolved).resolve()), args
            if cmd != "airg-server":
                return cmd, args

    venv = str(os.environ.get("VIRTUAL_ENV", "")).strip()
    if venv:
        candidate = pathlib.Path(venv) / "bin" / "airg-server"
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate.resolve()), []

    exe_dir = pathlib.Path(sys.executable).resolve().parent
    candidate = exe_dir / "airg-server"
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate.resolve()), []

    return str(pathlib.Path(sys.executable).resolve()), ["-m", "airg_cli", "server"]


def _server_block(workspace: pathlib.Path, agent_id: str) -> dict[str, Any]:
    command, args = _server_process()
    return {
        "command": command,
        "args": args,
        "env": {
            "AIRG_AGENT_ID": str(agent_id or "").strip() or "default",
            "AIRG_WORKSPACE": str(workspace),
        },
    }


def _target_file_for_scope(workspace: pathlib.Path, scope: str) -> pathlib.Path:
    if scope == "project":
        return workspace / ".mcp.json"
    return _home() / ".claude.json"


def _settings_local_path(workspace: pathlib.Path) -> pathlib.Path:
    return workspace / ".claude" / "settings.local.json"


def _cleanup_empty_dicts(payload: dict[str, Any], keys: list[str]) -> None:
    cursor: Any = payload
    chain: list[tuple[dict[str, Any], str]] = []
    for key in keys:
        if not isinstance(cursor, dict) or key not in cursor:
            return
        chain.append((cursor, key))
        cursor = cursor[key]
    for parent, key in reversed(chain):
        value = parent.get(key)
        if isinstance(value, dict) and not value:
            parent.pop(key, None)
            continue
        if isinstance(value, list) and not value:
            parent.pop(key, None)
            continue
        break


def _sync_settings_local_allowlist(
    paths: dict[str, pathlib.Path],
    workspace: pathlib.Path,
    *,
    ensure_enabled: bool,
    agent_id: str,
) -> dict[str, Any]:
    target = _settings_local_path(workspace)
    before_exists = target.exists()
    before_payload = _read_json_strict(target) if before_exists else {}
    if not isinstance(before_payload, dict):
        before_payload = {}
    after = _deep_copy(before_payload)

    changed = False
    enabled = after.get("enabledMcpjsonServers")
    if ensure_enabled:
        if not isinstance(enabled, list):
            enabled = []
            after["enabledMcpjsonServers"] = enabled
            changed = True
        if "ai-runtime-guard" not in enabled:
            enabled.append("ai-runtime-guard")
            changed = True
    else:
        if isinstance(enabled, list):
            filtered = [item for item in enabled if str(item).strip() != "ai-runtime-guard"]
            if filtered != enabled:
                changed = True
            if filtered:
                after["enabledMcpjsonServers"] = filtered
            else:
                after.pop("enabledMcpjsonServers", None)

    permissions = after.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        if ensure_enabled:
            after["permissions"] = permissions
            changed = True
    allow = permissions.get("allow")
    if ensure_enabled:
        if not isinstance(allow, list):
            allow = []
            permissions["allow"] = allow
            changed = True
        for tool_name in AIRG_MCP_TOOLS:
            token = f"mcp__ai-runtime-guard__{tool_name}"
            if token not in allow:
                allow.append(token)
                changed = True
    else:
        if isinstance(allow, list):
            filtered_allow = [item for item in allow if not str(item).strip().startswith("mcp__ai-runtime-guard__")]
            if filtered_allow != allow:
                changed = True
            if filtered_allow:
                permissions["allow"] = filtered_allow
            else:
                permissions.pop("allow", None)
        _cleanup_empty_dicts(after, ["permissions"])

    if not changed:
        return {
            "target_path": str(target),
            "changed": False,
            "backup_path": "",
            "created": False,
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup_file(paths, target, agent_id)
    try:
        _write_json_verified(target, after)
    except Exception:
        if before_exists and backup and backup.exists():
            shutil.copy2(backup, target)
        elif not before_exists and target.exists():
            try:
                target.unlink()
            except Exception:
                pass
        raise

    return {
        "target_path": str(target),
        "changed": True,
        "backup_path": str(backup) if backup else "",
        "created": not before_exists,
    }


def _read_json_strict(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(_INVALID_JSON_MESSAGE) from exc
    except Exception as exc:  # pragma: no cover - OS-level read failures
        raise RuntimeError(f"Failed to read `{path}`: {exc}. Check file permissions and try again.") from exc
    if not isinstance(payload, dict):
        raise ValueError(_INVALID_JSON_MESSAGE)
    return payload


def _write_json_verified(path: pathlib.Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2) + "\n"
    try:
        path.write_text(text)
    except Exception as exc:  # pragma: no cover - OS-level write failures
        raise RuntimeError(
            f"Failed to write to `{path}`: {exc}. You can apply manually using the Copy buttons."
        ) from exc
    try:
        verify = _read_json_strict(path)
    except Exception as exc:
        raise RuntimeError(f"Write appeared to succeed but verification failed. Please check the file manually at `{path}`.") from exc
    if _canonical(verify) != _canonical(payload):
        raise RuntimeError(f"Write appeared to succeed but verification failed. Please check the file manually at `{path}`.")


def _ensure_claude_json_exists(path: pathlib.Path) -> None:
    if path.exists() and path.is_file():
        return
    raise FileNotFoundError(_CLAUDE_JSON_MISSING)


def _ensure_mcp_servers(payload: dict[str, Any]) -> dict[str, Any]:
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        payload["mcpServers"] = servers
    return servers


def _ensure_project_entry(payload: dict[str, Any], workspace: pathlib.Path) -> dict[str, Any]:
    projects = payload.get("projects")
    if not isinstance(projects, dict):
        projects = {}
        payload["projects"] = projects
    key = str(workspace)
    project_entry = projects.get(key)
    if not isinstance(project_entry, dict):
        project_entry = {}
        projects[key] = project_entry
    return project_entry


def _apply_airg_entry(payload: dict[str, Any], scope: str, workspace: pathlib.Path, block: dict[str, Any]) -> dict[str, Any]:
    after = _deep_copy(payload)
    if scope == "project":
        _ensure_mcp_servers(after)["ai-runtime-guard"] = block
        return after
    if scope == "user":
        _ensure_mcp_servers(after)["ai-runtime-guard"] = block
        return after
    project_entry = _ensure_project_entry(after, workspace)
    servers = project_entry.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        project_entry["mcpServers"] = servers
    servers["ai-runtime-guard"] = block
    return after


def _remove_airg_entry(payload: dict[str, Any], scope: str, workspace: pathlib.Path) -> tuple[dict[str, Any], bool]:
    after = _deep_copy(payload)
    if scope in {"project", "user"}:
        servers = after.get("mcpServers")
        if not isinstance(servers, dict):
            return after, False
        existed = "ai-runtime-guard" in servers
        servers.pop("ai-runtime-guard", None)
        if not servers:
            after.pop("mcpServers", None)
        return after, existed

    projects = after.get("projects")
    if not isinstance(projects, dict):
        return after, False
    key = str(workspace)
    entry = projects.get(key)
    if not isinstance(entry, dict):
        return after, False
    servers = entry.get("mcpServers")
    if not isinstance(servers, dict):
        return after, False
    existed = "ai-runtime-guard" in servers
    servers.pop("ai-runtime-guard", None)
    if not servers:
        entry.pop("mcpServers", None)
    return after, existed


def _validate_profile(profile: dict[str, Any]) -> tuple[pathlib.Path, str, str]:
    agent_type = str(profile.get("agent_type", "")).strip().lower()
    if agent_type != "claude_code":
        raise ValueError("Only Claude Code MCP apply/remove is supported in this pass.")
    workspace = _workspace_path(profile)
    scope = _normalize_scope(profile.get("agent_scope"))
    agent_id = str(profile.get("agent_id") or "").strip() or "default"
    return workspace, scope, agent_id


def _normalize_last_applied(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    scope = str(raw.get("scope") or "").strip().lower()
    file_path = str(raw.get("file_path") or "").strip()
    timestamp = str(raw.get("timestamp") or "").strip()
    if not (scope and file_path and timestamp):
        return None
    return {
        "scope": scope,
        "file_path": file_path,
        "timestamp": timestamp,
        "workspace": str(raw.get("workspace") or "").strip(),
        "agent_id": str(raw.get("agent_id") or "").strip(),
        "created_by_airg": bool(raw.get("created_by_airg", False)),
    }


def _build_plan(profile: dict[str, Any]) -> dict[str, Any]:
    workspace, scope, agent_id = _validate_profile(profile)
    target_path = _target_file_for_scope(workspace, scope)
    previous = _normalize_last_applied(profile.get("last_applied"))

    scope_changed = False
    workspace_changed = False
    agent_id_changed = False
    target_changed = False
    requires_previous_choice = False
    must_remove_previous = False

    if previous:
        old_scope = str(previous.get("scope") or "").strip().lower()
        old_workspace = str(previous.get("workspace") or "").strip()
        old_agent_id = str(previous.get("agent_id") or "").strip()
        old_target = str(previous.get("file_path") or "").strip()

        scope_changed = old_scope != scope
        workspace_changed = bool(old_workspace) and old_workspace != str(workspace)
        agent_id_changed = bool(old_agent_id) and old_agent_id != agent_id
        target_changed = bool(old_target) and old_target != str(target_path)

        if scope_changed:
            must_remove_previous = True
        elif target_changed and (workspace_changed or agent_id_changed):
            requires_previous_choice = True

    return {
        "agent_type": "claude_code",
        "scope": scope,
        "workspace": str(workspace),
        "agent_id": agent_id,
        "target_path": str(target_path),
        "server_entry": _server_block(workspace, agent_id),
        "preview_json": {"mcpServers": {"ai-runtime-guard": _server_block(workspace, agent_id)}},
        "previous": previous,
        "scope_changed": scope_changed,
        "workspace_changed": workspace_changed,
        "agent_id_changed": agent_id_changed,
        "target_changed": target_changed,
        "must_remove_previous": must_remove_previous,
        "requires_previous_choice": requires_previous_choice,
    }


def plan_apply(paths: dict[str, pathlib.Path], profile: dict[str, Any]) -> dict[str, Any]:
    try:
        plan = _build_plan(profile)
        return {"ok": True, "plan": plan}
    except Exception as exc:
        return {"ok": False, "errors": [str(exc)]}


def _apply_for_scope(paths: dict[str, pathlib.Path], plan: dict[str, Any]) -> dict[str, Any]:
    workspace = pathlib.Path(str(plan["workspace"])).resolve()
    scope = str(plan["scope"])
    target = pathlib.Path(str(plan["target_path"])).expanduser().resolve()
    agent_id = str(plan["agent_id"])
    server_entry = _deep_copy(plan["server_entry"])

    before_exists = target.exists()
    before_payload: dict[str, Any]
    if before_exists:
        before_payload = _read_json_strict(target)
    else:
        if scope in {"local", "user"}:
            _ensure_claude_json_exists(target)
            before_payload = _read_json_strict(target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            before_payload = {}

    after_payload = _apply_airg_entry(before_payload, scope, workspace, server_entry)
    if _canonical(after_payload) == _canonical(before_payload):
        return {
            "scope": scope,
            "target_path": str(target),
            "changed": False,
            "backup_path": "",
            "created_by_airg": bool(plan["previous"] and plan["previous"].get("created_by_airg", False)),
        }

    backup = _backup_file(paths, target, agent_id)
    try:
        _write_json_verified(target, after_payload)
    except Exception:
        if before_exists and backup and backup.exists():
            shutil.copy2(backup, target)
        elif not before_exists and target.exists():
            try:
                target.unlink()
            except Exception:
                pass
        raise

    verify_payload = _read_json_strict(target)
    verify_removed, has_entry = _remove_airg_entry(verify_payload, scope, workspace)
    # has_entry here means entry existed pre-removal of verification payload.
    if not has_entry:
        raise RuntimeError(f"Write appeared to succeed but verification failed. Please check the file manually at `{target}`.")

    return {
        "scope": scope,
        "target_path": str(target),
        "changed": True,
        "backup_path": str(backup) if backup else "",
        "created_by_airg": scope == "project" and not before_exists,
        "before_missing": not before_exists,
    }


def _remove_from_last_applied(
    paths: dict[str, pathlib.Path],
    previous: dict[str, Any],
    *,
    strict_missing_file: bool,
) -> dict[str, Any]:
    scope = str(previous.get("scope") or "").strip().lower()
    workspace_raw = str(previous.get("workspace") or "").strip()
    workspace = pathlib.Path(workspace_raw).expanduser().resolve() if workspace_raw else None
    target = pathlib.Path(str(previous.get("file_path") or "")).expanduser().resolve()
    agent_id = str(previous.get("agent_id") or "").strip() or "default"

    if not str(target):
        raise RuntimeError("Missing previous MCP target path")
    if not target.exists():
        if strict_missing_file:
            raise RuntimeError(f"Failed to remove previous MCP config from `{target}`: file not found")
        return {"removed": False, "target_path": str(target), "backup_path": "", "deleted_file": False}

    before_payload = _read_json_strict(target)
    if scope == "local" and workspace is None:
        raise RuntimeError(f"Failed to remove previous MCP config from `{target}`: missing previous workspace metadata")

    cleaned, had_entry = _remove_airg_entry(before_payload, scope, workspace or pathlib.Path("/"))
    if not had_entry:
        return {"removed": False, "target_path": str(target), "backup_path": "", "deleted_file": False}

    backup = _backup_file(paths, target, agent_id)
    delete_project_file = (
        scope == "project"
        and bool(previous.get("created_by_airg", False))
        and not cleaned.get("mcpServers")
    )

    if delete_project_file:
        try:
            target.unlink()
        except Exception as exc:
            raise RuntimeError(f"Failed to remove previous MCP config from `{target}`: {exc}") from exc
        return {
            "removed": True,
            "target_path": str(target),
            "backup_path": str(backup) if backup else "",
            "deleted_file": True,
        }

    _write_json_verified(target, cleaned)
    return {
        "removed": True,
        "target_path": str(target),
        "backup_path": str(backup) if backup else "",
        "deleted_file": False,
    }


def apply_mcp_config(
    paths: dict[str, pathlib.Path],
    profile: dict[str, Any],
    *,
    remove_previous: bool | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    planned = plan_apply(paths, profile)
    if not planned.get("ok"):
        return planned

    plan = planned["plan"]
    if dry_run:
        return {"ok": True, "plan": plan, "dry_run": True}

    previous = plan.get("previous")
    must_remove_previous = bool(plan.get("must_remove_previous", False))
    requires_choice = bool(plan.get("requires_previous_choice", False))

    if requires_choice and remove_previous is None:
        return {
            "ok": False,
            "requires_previous_choice": True,
            "plan": plan,
            "errors": [
                "Previous MCP configuration was found in a different location. Choose whether to remove it before applying the new config."
            ],
        }

    previous_removal: dict[str, Any] | None = None
    settings_local_sync: dict[str, Any] | None = None
    try:
        if previous and (must_remove_previous or (requires_choice and bool(remove_previous))):
            previous_removal = _remove_from_last_applied(paths, previous, strict_missing_file=True)

        applied = _apply_for_scope(paths, plan)
        settings_local_sync = _sync_settings_local_allowlist(
            paths,
            pathlib.Path(str(plan["workspace"])).resolve(),
            ensure_enabled=True,
            agent_id=str(plan["agent_id"]),
        )
    except Exception as exc:
        if must_remove_previous and previous_removal is not None:
            return {
                "ok": False,
                "plan": plan,
                "errors": [
                    f"Failed to apply MCP configuration after removing previous config. Manual intervention required: {exc}"
                ],
            }
        return {"ok": False, "plan": plan, "errors": [str(exc)]}

    last_applied = {
        "scope": plan["scope"],
        "file_path": plan["target_path"],
        "timestamp": _now_iso(),
        "workspace": plan["workspace"],
        "agent_id": plan["agent_id"],
        "created_by_airg": bool(applied.get("created_by_airg", False)),
    }
    profile_id = str(profile.get("profile_id") or "").strip()
    if not profile_id:
        return {"ok": False, "errors": ["profile_id is required"]}
    updated = agent_configs.set_last_applied(paths, profile_id, last_applied)
    if not updated.get("ok"):
        return updated

    return {
        "ok": True,
        "plan": plan,
        "applied": applied,
        "settings_local": settings_local_sync,
        "previous_removed": previous_removal,
        "profile": updated.get("profile"),
        "profiles": updated.get("profiles", []),
    }


def remove_applied_mcp(paths: dict[str, pathlib.Path], profile: dict[str, Any]) -> dict[str, Any]:
    last_applied = _normalize_last_applied(profile.get("last_applied"))
    workspace_raw = str(profile.get("workspace") or "").strip()
    workspace_path = pathlib.Path(workspace_raw).expanduser().resolve() if workspace_raw else None
    settings_local_cleanup: dict[str, Any] | None = None

    if last_applied:
        try:
            removal = _remove_from_last_applied(paths, last_applied, strict_missing_file=True)
        except Exception as exc:
            return {"ok": False, "errors": [str(exc)]}
    else:
        removal = {"removed": False, "reason": "no_last_applied"}

    if workspace_path is not None:
        try:
            settings_local_cleanup = _sync_settings_local_allowlist(
                paths,
                workspace_path,
                ensure_enabled=False,
                agent_id=str(profile.get("agent_id") or "default"),
            )
        except Exception as exc:
            return {"ok": False, "errors": [f"Failed to update Claude settings.local.json: {exc}"]}

    profile_id = str(profile.get("profile_id") or "").strip()
    if not profile_id:
        return {"ok": False, "errors": ["profile_id is required"]}
    updated = agent_configs.set_last_applied(paths, profile_id, None)
    if not updated.get("ok"):
        return updated

    return {
        "ok": True,
        "removed": removal,
        "settings_local": settings_local_cleanup,
        "profile": updated.get("profile"),
        "profiles": updated.get("profiles", []),
    }
