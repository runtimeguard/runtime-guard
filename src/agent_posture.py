import json
import os
import platform
import pathlib
import re
from typing import Any


CLAUDE_SIGNAL_LABELS = {
    "airg_mcp_present": "AIRG MCP configured",
    "tier1_hook_active": "Tier 1 hook coverage active",
    "native_tools_restricted": "Tier 1 native tools restricted",
    "tier2_hook_active": "Tier 2 hook coverage active",
    "sandbox_enabled": "Sandbox enabled",
    "sandbox_escape_closed": "Sandbox unsandboxed-command escape disabled",
}
CLAUDE_NATIVE_TOOLS = {"Bash", "Glob", "Grep", "Read", "Write", "Edit", "MultiEdit"}
CLAUDE_TIER1_TOOLS = {"Bash", "Write", "Edit", "MultiEdit"}
CLAUDE_TIER2_TOOLS = {"Read", "Glob", "Grep"}
_CODEX_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        if not path.exists() or not path.is_file():
            return {}
        payload = json.loads(path.read_text())
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _contains_airg_mcp_servers(servers: Any) -> bool:
    if not isinstance(servers, dict):
        return False
    if "ai-runtime-guard" in servers:
        return True
    for value in servers.values():
        if not isinstance(value, dict):
            continue
        cmd = str(value.get("command", "")).strip().lower()
        args = [str(x).strip().lower() for x in (value.get("args", []) or []) if str(x).strip()]
        if "airg" in cmd or any("airg" in a for a in args):
            return True
    return False


def _contains_airg_mcp(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    return _contains_airg_mcp_servers(payload.get("mcpServers", {}))


def _hook_active_matchers(effective: dict[str, Any]) -> set[str]:
    hooks = effective.get("hooks", {})
    if not isinstance(hooks, dict):
        return set()
    pre = hooks.get("PreToolUse", [])
    if not isinstance(pre, list):
        return set()
    out: set[str] = set()
    for matcher in pre:
        if not isinstance(matcher, dict):
            continue
        matcher_name = str(matcher.get("matcher", "")).strip()
        for hook in matcher.get("hooks", []) or []:
            if not isinstance(hook, dict):
                continue
            cmd = str(hook.get("command", "")).strip()
            if cmd == "airg-hook" or pathlib.Path(cmd).name == "airg-hook":
                if matcher_name:
                    out.add(matcher_name)
                break
    return out


def _hook_tier_active(effective: dict[str, Any], required_tools: set[str]) -> bool:
    matchers = _hook_active_matchers(effective)
    if not matchers:
        return False
    if "*" in matchers:
        return True
    return required_tools.issubset(matchers)


def _native_tools_restricted(effective: dict[str, Any]) -> bool:
    permissions = effective.get("permissions", {})
    if not isinstance(permissions, dict):
        return False
    deny = permissions.get("deny", [])
    if not isinstance(deny, list):
        return False
    denied = {str(x).strip() for x in deny}
    return CLAUDE_TIER1_TOOLS.issubset(denied)


def _native_tools_denied_set(effective: dict[str, Any]) -> set[str]:
    permissions = effective.get("permissions", {})
    if not isinstance(permissions, dict):
        return set()
    deny = permissions.get("deny", [])
    if not isinstance(deny, list):
        return set()
    denied = {str(x).strip() for x in deny if str(x).strip()}
    return {tool for tool in denied if tool in CLAUDE_NATIVE_TOOLS}


def _sandbox_hardened(effective: dict[str, Any]) -> tuple[bool, bool]:
    sandbox = effective.get("sandbox", {})
    if not isinstance(sandbox, dict):
        return False, False
    enabled = bool(sandbox.get("enabled", False))
    allow_unsandboxed = bool(sandbox.get("allowUnsandboxedCommands", True))
    return enabled, (not allow_unsandboxed)


def _score_claude(
    *,
    has_mcp: bool,
    tier1_hook_active: bool,
    native_denied: bool,
    tier2_hook_active: bool,
    sandbox_enabled: bool,
    escape_closed: bool,
) -> tuple[str, str]:
    if not has_mcp:
        return "gray", "No AIRG MCP configuration detected for this agent profile."
    tier1_ready = tier1_hook_active and native_denied
    if tier1_ready and tier2_hook_active and sandbox_enabled and escape_closed:
        return "green", "Full hardening detected: MCP + Tier 1 + Tier 2 + sandbox protections."
    if tier1_ready:
        return "yellow", "Tier 1 hardening detected. Complete Tier 2 and sandbox controls to reach Green."
    return "red", "AIRG MCP is configured but Tier 1 hardening is incomplete."


def _score_cursor(*, has_mcp: bool) -> tuple[str, str]:
    if has_mcp:
        return "red", "AIRG MCP detected. Advanced client-side hardening is not available for this agent."
    return "gray", "No AIRG MCP server configuration detected."


def _missing_controls_from_signals(signals: dict[str, Any], *, labels: dict[str, str]) -> list[str]:
    missing: list[str] = []
    for key, label in labels.items():
        if not bool(signals.get(key, False)):
            missing.append(label)
    return missing


def _claude_recommendations(signals: dict[str, Any]) -> list[str]:
    rec: list[str] = []
    if not bool(signals.get("airg_mcp_present", False)):
        rec.append("Add ai-runtime-guard to your MCP config for this workspace.")
    if not bool(signals.get("tier1_hook_active", False)):
        rec.append("Enable Tier 1 hook coverage (Bash/Write/Edit/MultiEdit) for deterministic MCP redirection.")
    if not bool(signals.get("native_tools_restricted", False)):
        rec.append("Restrict native Bash/Write/Edit/MultiEdit tools in Claude permissions.")
    if not bool(signals.get("tier2_hook_active", False)):
        rec.append("Enable Tier 2 hook coverage (Read/Glob/Grep) for path/extension policy checks and audit.")
    if not bool(signals.get("sandbox_enabled", False)):
        rec.append("Enable Claude sandbox mode for this workspace.")
    if bool(signals.get("sandbox_enabled", False)) and not bool(signals.get("sandbox_escape_closed", False)):
        rec.append("Disable allowUnsandboxedCommands to close sandbox escape hatch.")
    return rec


def _cursor_recommendations(signals: dict[str, Any]) -> list[str]:
    if bool(signals.get("airg_mcp_present", False)):
        return ["Cursor is MCP-layer protected; client-native hook/permission hardening is limited."]
    return ["Add ai-runtime-guard MCP server to Cursor mcp.json for this workspace."]


def _home() -> pathlib.Path:
    return pathlib.Path.home().expanduser().resolve()


def _workspace_from_profile(profile: dict[str, Any]) -> pathlib.Path:
    raw = str(profile.get("workspace", "")).strip()
    if not raw:
        return pathlib.Path.cwd().resolve()
    try:
        return pathlib.Path(raw).expanduser().resolve()
    except Exception:
        return pathlib.Path.cwd().resolve()


def _claude_paths(workspace: pathlib.Path) -> list[pathlib.Path]:
    scoped = _claude_paths_by_scope(workspace)
    return [scoped["user"], scoped["project"], scoped["local"]]


def _claude_paths_by_scope(workspace: pathlib.Path) -> dict[str, pathlib.Path]:
    home = _home()
    return {
        "user": home / ".claude" / "settings.json",
        "project": workspace / ".claude" / "settings.json",
        "local": workspace / ".claude" / "settings.local.json",
    }


def _normalize_claude_scope(raw_scope: Any) -> str:
    value = str(raw_scope or "").strip().lower()
    if value in {"project", "local", "user"}:
        return value
    return "local"


def _cursor_paths(workspace: pathlib.Path) -> list[pathlib.Path]:
    home = _home()
    return [
        workspace / ".cursor" / "mcp.json",
        home / ".cursor" / "mcp.json",
    ]


def _claude_managed_paths() -> list[pathlib.Path]:
    system = platform.system().lower()
    if system == "darwin":
        return [pathlib.Path("/Library/Application Support/ClaudeCode/managed-mcp.json")]
    if system == "linux":
        return [pathlib.Path("/etc/claude-code/managed-mcp.json")]
    if system == "windows":
        return [pathlib.Path(r"C:\Program Files\ClaudeCode\managed-mcp.json")]
    return []


def _workspace_key_matches(workspace: pathlib.Path, project_key: Any) -> bool:
    raw = str(project_key or "").strip()
    if not raw:
        return False
    if raw == str(workspace):
        return True
    try:
        candidate = pathlib.Path(raw).expanduser().resolve()
    except Exception:
        return False
    return str(candidate) == str(workspace)


def _claude_mcp_probe_paths(workspace: pathlib.Path) -> list[pathlib.Path]:
    home = _home()
    return [
        workspace / ".mcp.json",
        home / ".claude.json",
        *_claude_managed_paths(),
    ]


def _claude_desktop_config_path() -> pathlib.Path:
    if platform.system().lower() == "darwin":
        return _home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if platform.system().lower() == "windows":
        appdata = str(os.environ.get("APPDATA", "")).strip()
        if appdata:
            return pathlib.Path(appdata).expanduser().resolve() / "Claude" / "claude_desktop_config.json"
        return _home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    return _home() / ".config" / "Claude" / "claude_desktop_config.json"


def _codex_paths(workspace: pathlib.Path) -> list[pathlib.Path]:
    home = _home()
    return [
        home / ".codex" / "config.toml",
        workspace / ".codex" / "config.toml",
    ]


def _codex_has_airg_mcp(path: pathlib.Path) -> bool:
    try:
        if not path.exists() or not path.is_file():
            return False
        for line in path.read_text().splitlines():
            m = _CODEX_SECTION_RE.match(line)
            if not m:
                continue
            if str(m.group(1)).strip() == "mcp_servers.ai-runtime-guard":
                return True
        return False
    except Exception:
        return False


def _build_claude_desktop_posture(profile: dict[str, Any]) -> dict[str, Any]:
    config_path = _claude_desktop_config_path()
    payload = _read_json(config_path)
    has_mcp = _contains_airg_mcp(payload)
    status = "green" if has_mcp else "gray"
    rationale = (
        "AIRG MCP is configured in Claude Desktop. MCP-layer enforcement is active."
        if has_mcp
        else "No AIRG MCP server configuration detected in Claude Desktop config."
    )
    detected_scopes = ["desktop"] if has_mcp else []
    signals = {"airg_mcp_present": has_mcp}
    return {
        "status": status,
        "rationale": rationale,
        "signals": signals,
        "signal_scopes": {"airg_mcp_present": detected_scopes},
        "mcp_detected_scopes": detected_scopes,
        "mcp_detected_locations": ([{"scope": "desktop", "path": str(config_path)}] if has_mcp else []),
        "mcp_expected_scope": "desktop",
        "mcp_scope_match": has_mcp,
        "native_tools_denied": [],
        "missing_controls": [] if has_mcp else ["AIRG MCP configured"],
        "recommended_actions": (
            ["Claude Desktop is MCP-layer protected."]
            if has_mcp
            else ["Add ai-runtime-guard MCP server to Claude Desktop config."]
        ),
        "paths_checked": [str(config_path)],
        "existing_paths": [str(config_path)] if config_path.exists() else [],
    }


def _detect_claude_mcp_locations(workspace: pathlib.Path) -> list[dict[str, str]]:
    home = _home()
    found: list[dict[str, str]] = []

    project_mcp = workspace / ".mcp.json"
    if _contains_airg_mcp(_read_json(project_mcp)):
        found.append({"scope": "project", "path": str(project_mcp)})

    claude_local = home / ".claude.json"
    claude_payload = _read_json(claude_local)
    if _contains_airg_mcp_servers(claude_payload.get("mcpServers", {})):
        found.append({"scope": "user", "path": str(claude_local)})

    projects = claude_payload.get("projects", {})
    if isinstance(projects, dict):
        for key, value in projects.items():
            if not _workspace_key_matches(workspace, key):
                continue
            if not isinstance(value, dict):
                continue
            if _contains_airg_mcp_servers(value.get("mcpServers", {})):
                found.append({"scope": "local", "path": str(claude_local)})
                break

    for managed in _claude_managed_paths():
        if _contains_airg_mcp(_read_json(managed)):
            found.append({"scope": "managed", "path": str(managed)})

    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in found:
        key = (str(item.get("scope", "")), str(item.get("path", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append({"scope": key[0], "path": key[1]})
    return deduped


def _build_claude_posture(profile: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_from_profile(profile)
    settings_by_scope = _claude_paths_by_scope(workspace)
    settings_paths = [settings_by_scope["user"], settings_by_scope["project"], settings_by_scope["local"]]
    effective: dict[str, Any] = {}
    for p in settings_paths:
        effective = _deep_merge(effective, _read_json(p))
    payload_by_scope = {scope: _read_json(path) for scope, path in settings_by_scope.items()}
    mcp_probe_paths = _claude_mcp_probe_paths(workspace)
    mcp_locations = _detect_claude_mcp_locations(workspace)
    mcp_scopes = list(dict.fromkeys([str(item.get("scope", "")) for item in mcp_locations if str(item.get("scope", ""))]))
    expected_scope = _normalize_claude_scope(profile.get("agent_scope"))
    has_mcp = bool(mcp_locations)
    native_denied = _native_tools_restricted(effective)
    tier1_hook_active = _hook_tier_active(effective, CLAUDE_TIER1_TOOLS)
    tier2_hook_active = _hook_tier_active(effective, CLAUDE_TIER2_TOOLS)
    sandbox_enabled, escape_closed = _sandbox_hardened(effective)
    native_tool_denied = sorted(list(_native_tools_denied_set(effective)))
    signal_scopes = {
        "tier1_hook_active": [scope for scope, payload in payload_by_scope.items() if _hook_tier_active(payload, CLAUDE_TIER1_TOOLS)],
        "tier2_hook_active": [scope for scope, payload in payload_by_scope.items() if _hook_tier_active(payload, CLAUDE_TIER2_TOOLS)],
        "native_tools_restricted": [scope for scope, payload in payload_by_scope.items() if _native_tools_restricted(payload)],
        "sandbox_enabled": [scope for scope, payload in payload_by_scope.items() if bool((_sandbox_hardened(payload))[0])],
        "sandbox_escape_closed": [scope for scope, payload in payload_by_scope.items() if bool((_sandbox_hardened(payload))[1])],
    }
    status, rationale = _score_claude(
        has_mcp=has_mcp,
        tier1_hook_active=tier1_hook_active,
        native_denied=native_denied,
        tier2_hook_active=tier2_hook_active,
        sandbox_enabled=sandbox_enabled,
        escape_closed=escape_closed,
    )
    signals = {
        "airg_mcp_present": has_mcp,
        "tier1_hook_active": tier1_hook_active,
        "native_tools_restricted": native_denied,
        "tier2_hook_active": tier2_hook_active,
        "sandbox_enabled": sandbox_enabled,
        "sandbox_escape_closed": escape_closed,
    }
    return {
        "status": status,
        "rationale": rationale,
        "signals": signals,
        "signal_scopes": signal_scopes,
        "mcp_detected_scopes": mcp_scopes,
        "mcp_detected_locations": mcp_locations,
        "mcp_expected_scope": expected_scope,
        "mcp_scope_match": (expected_scope in mcp_scopes) if has_mcp else False,
        "native_tools_denied": native_tool_denied,
        "missing_controls": _missing_controls_from_signals(signals, labels=CLAUDE_SIGNAL_LABELS),
        "recommended_actions": _claude_recommendations(signals),
        "paths_checked": [str(p) for p in settings_paths + mcp_probe_paths],
        "existing_paths": [str(p) for p in settings_paths + mcp_probe_paths if p.exists()],
    }


def _build_cursor_posture(profile: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_from_profile(profile)
    paths = _cursor_paths(workspace)
    has_mcp = any(_contains_airg_mcp(_read_json(p)) for p in paths)
    status, rationale = _score_cursor(has_mcp=has_mcp)
    signals = {
        "airg_mcp_present": has_mcp,
    }
    return {
        "status": status,
        "rationale": rationale,
        "signals": signals,
        "missing_controls": [] if has_mcp else ["AIRG MCP configured"],
        "recommended_actions": _cursor_recommendations(signals),
        "paths_checked": [str(p) for p in paths],
        "existing_paths": [str(p) for p in paths if p.exists()],
    }


def _build_codex_posture(profile: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_from_profile(profile)
    paths = _codex_paths(workspace)
    has_global = _codex_has_airg_mcp(paths[0])
    has_project = _codex_has_airg_mcp(paths[1])
    has_mcp = has_global or has_project
    status, rationale = _score_cursor(has_mcp=has_mcp)
    detected_scopes: list[str] = []
    if has_global:
        detected_scopes.append("global")
    if has_project:
        detected_scopes.append("project")
    expected_scope = str(profile.get("agent_scope", "")).strip().lower() or "global"
    locations = []
    if has_global:
        locations.append({"scope": "global", "path": str(paths[0])})
    if has_project:
        locations.append({"scope": "project", "path": str(paths[1])})
    return {
        "status": status,
        "rationale": rationale,
        "signals": {"airg_mcp_present": has_mcp},
        "signal_scopes": {"airg_mcp_present": detected_scopes},
        "mcp_detected_scopes": detected_scopes,
        "mcp_detected_locations": locations,
        "mcp_expected_scope": expected_scope,
        "mcp_scope_match": (expected_scope in detected_scopes) if has_mcp else False,
        "missing_controls": [] if has_mcp else ["AIRG MCP configured"],
        "recommended_actions": (
            ["Codex is MCP-layer protected; client-native hardening controls are limited."]
            if has_mcp
            else ["Add ai-runtime-guard MCP server to Codex config.toml (global or project scope)."]
        ),
        "paths_checked": [str(p) for p in paths],
        "existing_paths": [str(p) for p in paths if p.exists()],
    }


def _build_generic_posture(profile: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_from_profile(profile)
    paths = _claude_mcp_probe_paths(workspace)
    has_mcp = any(_contains_airg_mcp(_read_json(p)) for p in paths)
    status = "red" if has_mcp else "gray"
    rationale = (
        "AIRG MCP detected. Advanced client-side hardening depends on agent support."
        if has_mcp
        else "No AIRG MCP server configuration detected."
    )
    return {
        "status": status,
        "rationale": rationale,
        "signals": {
            "airg_mcp_present": has_mcp,
        },
        "missing_controls": [] if has_mcp else ["AIRG MCP configured"],
        "recommended_actions": (
            ["Add ai-runtime-guard MCP server for this agent type/workspace."]
            if not has_mcp
            else ["MCP protection detected. Additional native tool restrictions depend on client support."]
        ),
        "paths_checked": [str(p) for p in paths],
        "existing_paths": [str(p) for p in paths if p.exists()],
    }


def build_posture_for_profile(profile: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "profile_id": str(profile.get("profile_id", "")).strip(),
        "name": str(profile.get("name", "")).strip(),
        "agent_type": str(profile.get("agent_type", "")).strip().lower(),
        "agent_scope": str(profile.get("agent_scope", "")).strip().lower(),
        "agent_id": str(profile.get("agent_id", "")).strip(),
        "workspace": str(profile.get("workspace", "")).strip(),
    }
    agent_type = normalized["agent_type"]
    if agent_type == "claude_code":
        posture = _build_claude_posture(profile)
    elif agent_type == "claude_desktop":
        posture = _build_claude_desktop_posture(profile)
    elif agent_type == "codex":
        posture = _build_codex_posture(profile)
    elif agent_type == "cursor":
        posture = _build_cursor_posture(profile)
    else:
        posture = _build_generic_posture(profile)
    return {**normalized, **posture}


def detect_unregistered_configs(*, known_workspaces: list[pathlib.Path]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    home = _home()
    candidates: list[tuple[str, pathlib.Path, str]] = [
        ("cursor", home / ".cursor" / "mcp.json", "home"),
        ("claude_code", home / ".claude" / "settings.json", "home"),
        ("claude_desktop", _claude_desktop_config_path(), "desktop"),
        ("codex", home / ".codex" / "config.toml", "global"),
    ]
    for workspace in known_workspaces:
        candidates.extend(
            [
                ("cursor", workspace / ".cursor" / "mcp.json", "workspace"),
                ("claude_code", workspace / ".claude" / "settings.json", "workspace"),
                ("claude_code", workspace / ".claude" / "settings.local.json", "workspace"),
                ("codex", workspace / ".codex" / "config.toml", "project"),
            ]
        )
    seen: set[str] = set()
    for agent_type, path, scope in candidates:
        key = f"{agent_type}:{path}"
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            found.append(
                {
                    "agent_type": agent_type,
                    "path": str(path),
                    "scope": scope,
                }
            )
    return found


def _normalize_path(path_value: Any) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return ""
    try:
        return str(pathlib.Path(raw).expanduser().resolve())
    except Exception:
        return raw


def _filter_registered_discovered(
    profile_postures: list[dict[str, Any]],
    discovered: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    registered: set[tuple[str, str]] = set()
    for row in profile_postures:
        if not isinstance(row, dict):
            continue
        agent_type = str(row.get("agent_type", "")).strip().lower()
        if not agent_type:
            continue
        paths_checked = row.get("paths_checked", [])
        if isinstance(paths_checked, list):
            for path in paths_checked:
                norm = _normalize_path(path)
                if norm:
                    registered.add((agent_type, norm))
        detected_locations = row.get("mcp_detected_locations", [])
        if isinstance(detected_locations, list):
            for item in detected_locations:
                if not isinstance(item, dict):
                    continue
                norm = _normalize_path(item.get("path"))
                if norm:
                    registered.add((agent_type, norm))

    filtered: list[dict[str, Any]] = []
    for item in discovered:
        if not isinstance(item, dict):
            continue
        agent_type = str(item.get("agent_type", "")).strip().lower()
        path_norm = _normalize_path(item.get("path"))
        if not agent_type or not path_norm:
            filtered.append(item)
            continue
        if (agent_type, path_norm) in registered:
            continue
        filtered.append(item)
    return filtered


def _sort_discovered(discovered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        discovered,
        key=lambda x: (str(x.get("agent_type", "")), str(x.get("scope", "")), str(x.get("path", ""))),
    )


def _known_workspaces_from_profiles(profiles: list[dict[str, Any]]) -> list[pathlib.Path]:
    workspaces: list[pathlib.Path] = []
    for profile in profiles:
        raw = str((profile or {}).get("workspace", "")).strip()
        if not raw:
            continue
        try:
            workspaces.append(pathlib.Path(raw).expanduser().resolve())
        except Exception:
            continue
    return workspaces


def detect_unregistered_for_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profile_postures = [build_posture_for_profile(profile) for profile in profiles]
    workspaces = _known_workspaces_from_profiles(profiles)
    discovered_raw = detect_unregistered_configs(known_workspaces=workspaces)
    return _sort_discovered(_filter_registered_discovered(profile_postures, discovered_raw))


def build_posture_summary(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    profile_postures = [build_posture_for_profile(profile) for profile in profiles]
    workspaces = _known_workspaces_from_profiles(profiles)
    discovered_raw = detect_unregistered_configs(known_workspaces=workspaces)
    discovered = _sort_discovered(_filter_registered_discovered(profile_postures, discovered_raw))
    totals = {"gray": 0, "green": 0, "yellow": 0, "red": 0}
    for row in profile_postures:
        status = str(row.get("status", "")).lower()
        if status in totals:
            totals[status] += 1
    return {
        "ok": True,
        "errors": [],
        "profiles": profile_postures,
        "discovered_unregistered": discovered,
        "totals": totals,
    }
