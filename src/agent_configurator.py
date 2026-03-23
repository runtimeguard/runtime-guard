import json
import os
import platform
import pathlib
import hashlib
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from datetime import UTC, datetime
from typing import Any


CLAUDE_NATIVE_TOOLS = ["Bash", "Glob", "Grep", "Read", "Write", "Edit", "MultiEdit"]
CLAUDE_SCOPES = {"local", "project", "user"}
CLAUDE_TIER1_TOOLS = ["Bash", "Write", "Edit", "MultiEdit"]
CLAUDE_TIER2_TOOLS = ["Read", "Glob", "Grep"]
CODEX_SANDBOX_MODES = {"read-only", "workspace-write", "danger-full-access"}
CODEX_APPROVAL_POLICIES = {"untrusted", "on-request", "never"}
CODEX_AGENT_DOC_BEGIN = "<!-- AIRG_CODEX_TIER1_BEGIN -->"
CODEX_AGENT_DOC_END = "<!-- AIRG_CODEX_TIER1_END -->"
CODEX_RULES_BEGIN = "# AIRG_CODEX_TIER2_BEGIN"
CODEX_RULES_END = "# AIRG_CODEX_TIER2_END"
_CODEX_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _state_dir(paths: dict[str, pathlib.Path]) -> pathlib.Path:
    return paths["approval_db_path"].expanduser().resolve().parent


def _state_path(paths: dict[str, pathlib.Path]) -> pathlib.Path:
    return _state_dir(paths) / "mcp-configs" / "agent-config-state.json"


def _read_json_file(path: pathlib.Path) -> dict[str, Any]:
    try:
        if not path.exists() or not path.is_file():
            return {}
        payload = json.loads(path.read_text())
    except Exception as exc:
        raise ValueError(f"Invalid JSON at {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object at {path}, found {type(payload).__name__}")
    return payload


def _read_json_file_optional(path: pathlib.Path) -> dict[str, Any]:
    try:
        return _read_json_file(path)
    except Exception:
        return {}


def _merge_list_union(base: list[Any], overlay: list[Any]) -> list[Any]:
    out: list[Any] = list(base)
    seen = {_canonical(item) for item in out}
    for item in overlay:
        marker = _canonical(item)
        if marker in seen:
            continue
        out.append(item)
        seen.add(marker)
    return out


def _deep_merge_union(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        out = {k: _deep_copy(v) for k, v in base.items()}
        for key, value in overlay.items():
            if key in out:
                out[key] = _deep_merge_union(out[key], value)
            else:
                out[key] = _deep_copy(value)
        return out
    if isinstance(base, list) and isinstance(overlay, list):
        return _merge_list_union(base, overlay)
    return _deep_copy(overlay)


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _summarize_diff(before: Any, after: Any, *, _path: str = "") -> list[str]:
    path = _path or "root"
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[str] = []
        keys = sorted(set(before.keys()) | set(after.keys()))
        for key in keys:
            child_path = f"{path}.{key}" if _path else key
            if key not in before:
                out.append(f"+ {child_path}")
                continue
            if key not in after:
                out.append(f"- {child_path}")
                continue
            out.extend(_summarize_diff(before[key], after[key], _path=child_path))
        return out
    if isinstance(before, list) and isinstance(after, list):
        if _canonical(before) == _canonical(after):
            return []
        before_set = {_canonical(item): item for item in before}
        after_set = {_canonical(item): item for item in after}
        added = [after_set[k] for k in after_set.keys() - before_set.keys()]
        removed = [before_set[k] for k in before_set.keys() - after_set.keys()]
        out: list[str] = []
        if added:
            out.append(f"+ {path} ({len(added)} added)")
        if removed:
            out.append(f"- {path} ({len(removed)} removed)")
        if not out:
            out.append(f"~ {path} (order/values updated)")
        return out
    if _canonical(before) == _canonical(after):
        return []
    return [f"~ {path}"]


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


def _shared_env(_paths: dict[str, pathlib.Path], workspace: pathlib.Path, agent_id: str) -> dict[str, str]:
    return {
        "AIRG_AGENT_ID": str(agent_id).strip() or "default",
        "AIRG_WORKSPACE": str(workspace),
    }


def _airg_server_block(paths: dict[str, pathlib.Path], workspace: pathlib.Path, agent_id: str) -> dict[str, Any]:
    command, args = _server_process()
    return {
        "command": command,
        "args": args,
        "env": _shared_env(paths, workspace, agent_id),
    }


def _contains_airg_mcp(payload: dict[str, Any]) -> bool:
    servers = payload.get("mcpServers", {})
    return _contains_airg_mcp_servers(servers)


def _contains_airg_mcp_servers(servers: Any) -> bool:
    if not isinstance(servers, dict):
        return False
    if "ai-runtime-guard" in servers:
        return True
    for value in servers.values():
        if not isinstance(value, dict):
            continue
        cmd = str(value.get("command", "")).strip().lower()
        args = [str(v).strip().lower() for v in (value.get("args") or []) if str(v).strip()]
        if "airg" in cmd or any("airg" in item for item in args):
            return True
    return False


def _home() -> pathlib.Path:
    return pathlib.Path.home().expanduser().resolve()


def _workspace_path(profile: dict[str, Any]) -> pathlib.Path:
    raw = str(profile.get("workspace", "")).strip()
    if not raw:
        raise ValueError("Profile workspace is required")
    workspace = pathlib.Path(raw).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise ValueError(f"Workspace does not exist or is not a directory: {workspace}")
    return workspace


def _settings_local_path(workspace: pathlib.Path) -> pathlib.Path:
    return workspace / ".claude" / "settings.local.json"


def _settings_project_path(workspace: pathlib.Path) -> pathlib.Path:
    return workspace / ".claude" / "settings.json"


def _settings_user_path() -> pathlib.Path:
    return _home() / ".claude" / "settings.json"


def _claude_settings_path_for_scope(workspace: pathlib.Path, scope: str) -> pathlib.Path:
    normalized = str(scope or "").strip().lower()
    if normalized == "project":
        return _settings_project_path(workspace)
    if normalized == "user":
        return _settings_user_path()
    return _settings_local_path(workspace)


def _normalize_claude_scope(raw_scope: Any) -> str:
    requested = str(raw_scope or "").strip().lower()
    if requested in CLAUDE_SCOPES:
        return requested
    return "local"


def _normalize_claude_hardening_options(profile: dict[str, Any], options: dict[str, Any] | None) -> dict[str, Any]:
    payload = options if isinstance(options, dict) else {}
    selected_scope = _normalize_claude_scope(payload.get("scope") or profile.get("agent_scope") or "local")

    legacy_hook_enabled = bool(payload.get("hook_enabled", True))
    legacy_restrict_native = bool(payload.get("restrict_native_tools", True))

    raw_tools = payload.get("native_tools", CLAUDE_NATIVE_TOOLS)
    selected_tools: set[str] = set()
    if isinstance(raw_tools, dict):
        selected_tools = {
            tool for tool in CLAUDE_NATIVE_TOOLS if bool(raw_tools.get(tool, False))
        }
    elif isinstance(raw_tools, list):
        selected_tools = {
            tool for tool in CLAUDE_NATIVE_TOOLS if tool in {str(t).strip() for t in raw_tools}
        }

    if "basic_enforcement" in payload:
        basic_enforcement = bool(payload.get("basic_enforcement"))
    else:
        basic_enforcement = legacy_hook_enabled and legacy_restrict_native

    if "advanced_enforcement" in payload:
        advanced_enforcement = bool(payload.get("advanced_enforcement"))
    else:
        advanced_enforcement = bool(selected_tools.intersection(set(CLAUDE_TIER2_TOOLS)))

    return {
        "scope": selected_scope,
        "basic_enforcement": basic_enforcement,
        "advanced_enforcement": advanced_enforcement,
        "sandbox_enabled": bool(payload.get("sandbox_enabled", True)),
        "sandbox_escape_closed": bool(payload.get("sandbox_escape_closed", True)),
    }


def _normalize_codex_hardening_options(options: dict[str, Any] | None) -> dict[str, Any]:
    payload = options if isinstance(options, dict) else {}
    sandbox_mode = str(payload.get("tier3_sandbox_mode", "workspace-write")).strip().lower()
    if sandbox_mode not in CODEX_SANDBOX_MODES:
        sandbox_mode = "workspace-write"
    approval_policy = str(payload.get("tier3_approval_policy", "on-request")).strip().lower()
    if approval_policy not in CODEX_APPROVAL_POLICIES:
        approval_policy = "on-request"
    roots_raw = payload.get("tier3_workspace_write_writable_roots", [])
    roots: list[str] = []
    if isinstance(roots_raw, str):
        roots = [part.strip() for part in roots_raw.split(",") if part.strip()]
    elif isinstance(roots_raw, list):
        roots = [str(part).strip() for part in roots_raw if str(part).strip()]
    dedup_roots = list(dict.fromkeys(roots))
    return {
        "tier1_guidance": bool(payload.get("tier1_guidance", True)),
        "tier2_mirror": bool(payload.get("tier2_mirror", True)),
        "tier2_include_requires_confirmation": bool(payload.get("tier2_include_requires_confirmation", False)),
        "tier3_sandbox_mode": sandbox_mode,
        "tier3_approval_policy": approval_policy,
        "tier3_workspace_write_network_access": bool(payload.get("tier3_workspace_write_network_access", False)),
        "tier3_workspace_write_writable_roots": dedup_roots,
    }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _codex_config_path() -> pathlib.Path:
    return _home() / ".codex" / "config.toml"


def _codex_rules_path() -> pathlib.Path:
    return _home() / ".codex" / "rules" / "default.rules"


def _codex_agents_doc_path() -> pathlib.Path:
    return _home() / ".codex" / "AGENTS.md"


def _read_text_optional(path: pathlib.Path) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        return path.read_text()
    except Exception:
        return ""


def _find_managed_block(text: str, begin_token: str, end_token: str) -> tuple[int, int]:
    begin_index = text.find(begin_token)
    if begin_index < 0:
        return -1, -1
    end_index = text.find(end_token, begin_index)
    if end_index < 0:
        return -1, -1
    end_index = end_index + len(end_token)
    while end_index < len(text) and text[end_index] in {"\r", "\n"}:
        end_index += 1
    return begin_index, end_index


def _upsert_managed_block(text: str, begin_token: str, end_token: str, block: str) -> str:
    start, end = _find_managed_block(text, begin_token, end_token)
    if start >= 0 and end >= 0:
        before = text[:start].rstrip()
        after = text[end:].lstrip("\r\n")
        merged = "\n\n".join(part for part in [before, block.strip(), after] if part)
        return (merged.rstrip() + "\n") if merged else ""
    if not block.strip():
        return (text.rstrip() + "\n") if text.strip() else ""
    if not text.strip():
        return block.strip() + "\n"
    return text.rstrip() + "\n\n" + block.strip() + "\n"


def _write_text_with_backup(paths: dict[str, pathlib.Path], target: pathlib.Path, after_text: str, agent_id: str) -> dict[str, Any]:
    before_exists = target.exists()
    before_text = _read_text_optional(target) if before_exists else ""
    if before_text == after_text:
        return {
            "target_path": str(target),
            "backup_path": "",
            "original_missing": not before_exists,
            "before": before_text,
            "after": after_text,
            "diff_summary": [],
            "changed": False,
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = _backup_path_for(target)
    if before_exists:
        shutil.copy2(target, backup)
    else:
        backup.write_text("")
    target.write_text(after_text)
    verify = _read_text_optional(target)
    if verify != after_text:
        if before_exists:
            shutil.copy2(backup, target)
        else:
            try:
                target.unlink()
            except Exception:
                pass
        raise RuntimeError(f"Verification failed after writing {target}")
    diff_summary = _summarize_diff(before_text, after_text, _path=str(target))
    return {
        "target_path": str(target),
        "backup_path": str(backup),
        "original_missing": not before_exists,
        "before": before_text,
        "after": after_text,
        "diff_summary": diff_summary,
        "changed": True,
    }


def _deep_merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = _deep_copy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = _deep_copy(value)
    return out


def _effective_policy_for_agent(paths: dict[str, pathlib.Path], agent_id: str) -> dict[str, Any]:
    policy_path = paths.get("policy_path")
    if not isinstance(policy_path, pathlib.Path):
        return {}
    policy_payload = _read_json_file_optional(policy_path)
    if not isinstance(policy_payload, dict):
        return {}
    overrides = policy_payload.get("agent_overrides")
    if isinstance(overrides, dict):
        override_entry = overrides.get(agent_id, {})
        if isinstance(override_entry, dict):
            overlay = override_entry.get("policy", {})
            if isinstance(overlay, dict) and overlay:
                return _deep_merge_dict(policy_payload, overlay)
    return policy_payload


def _split_command_tokens(command: str) -> list[str]:
    raw = str(command or "").strip()
    if not raw:
        return []
    try:
        return [token for token in shlex.split(raw) if str(token).strip()]
    except Exception:
        return [raw]


def _compile_codex_rules(
    policy: dict[str, Any],
    *,
    include_requires_confirmation: bool,
) -> tuple[list[str], list[list[str]], list[list[str]]]:
    blocked_section = policy.get("blocked", {}) if isinstance(policy, dict) else {}
    confirm_section = policy.get("requires_confirmation", {}) if isinstance(policy, dict) else {}
    blocked_commands_raw = blocked_section.get("commands", []) if isinstance(blocked_section, dict) else []
    confirm_commands_raw = confirm_section.get("commands", []) if isinstance(confirm_section, dict) else []
    blocked_commands = sorted(
        {
            str(cmd).strip()
            for cmd in (blocked_commands_raw if isinstance(blocked_commands_raw, list) else [])
            if str(cmd).strip()
        }
    )
    confirm_commands = sorted(
        {
            str(cmd).strip()
            for cmd in (confirm_commands_raw if isinstance(confirm_commands_raw, list) else [])
            if str(cmd).strip()
        }
    )
    lines: list[str] = []
    blocked_tokens: list[list[str]] = []
    prompted_tokens: list[list[str]] = []
    for cmd in blocked_commands:
        tokens = _split_command_tokens(cmd)
        if not tokens:
            continue
        blocked_tokens.append(tokens)
        lines.append(
            f'prefix_rule(pattern={json.dumps(tokens)}, decision="forbidden", '
            f'justification={json.dumps("Blocked by AIRG policy. Use mcp__ai-runtime-guard__execute_command instead.")})'
        )
    if include_requires_confirmation:
        blocked_set = {tuple(tokens) for tokens in blocked_tokens}
        for cmd in confirm_commands:
            tokens = _split_command_tokens(cmd)
            if not tokens:
                continue
            if tuple(tokens) in blocked_set:
                continue
            prompted_tokens.append(tokens)
            lines.append(
                f'prefix_rule(pattern={json.dumps(tokens)}, decision="prompt", '
                f'justification={json.dumps("Confirmation required by AIRG policy. Prefer mcp__ai-runtime-guard__execute_command.")})'
            )
    return lines, blocked_tokens, prompted_tokens


def _render_codex_rules_block(metadata: dict[str, Any], rule_lines: list[str]) -> tuple[str, str]:
    rules_body = ("\n".join(rule_lines).strip() + "\n") if rule_lines else ""
    rules_hash = _sha256_text(rules_body)
    meta = dict(metadata)
    meta["generated_rules_hash"] = rules_hash
    begin_line = f"{CODEX_RULES_BEGIN} {json.dumps(meta, sort_keys=True)}"
    block_parts = [begin_line]
    if rules_body:
        block_parts.append(rules_body.rstrip())
    block_parts.append(CODEX_RULES_END)
    return "\n".join(block_parts).strip() + "\n", rules_hash


def _parse_codex_rules_metadata(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(CODEX_RULES_BEGIN):
            continue
        tail = stripped[len(CODEX_RULES_BEGIN) :].strip()
        if not tail:
            return {}
        try:
            payload = json.loads(tail)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def _extract_codex_rules_body(text: str) -> str:
    start, end = _find_managed_block(text, CODEX_RULES_BEGIN, CODEX_RULES_END)
    if start < 0 or end < 0:
        return ""
    block = text[start:end]
    lines = block.splitlines()
    body_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(CODEX_RULES_BEGIN):
            continue
        if stripped == CODEX_RULES_END:
            continue
        body_lines.append(line)
    return ("\n".join(body_lines).strip() + "\n") if body_lines else ""


def _validate_codex_rules_file(path: pathlib.Path, blocked_tokens: list[list[str]], prompted_tokens: list[list[str]]) -> list[str]:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return ["Codex binary not found; skipped execpolicy validation."]

    checks: list[tuple[list[str], str]] = []
    checks.extend((tokens, "forbidden") for tokens in blocked_tokens[:12] if tokens)
    checks.extend((tokens, "prompt") for tokens in prompted_tokens[:12] if tokens)
    warnings: list[str] = []
    for tokens, expected in checks:
        proc = subprocess.run(
            [codex_bin, "execpolicy", "check", "--rules", str(path), "--pretty", "--", *tokens],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"codex execpolicy check failed for {' '.join(tokens)}: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        raw = proc.stdout.strip()
        if not raw:
            raise RuntimeError(f"codex execpolicy check produced no output for {' '.join(tokens)}")
        try:
            payload = json.loads(raw)
        except Exception as exc:
            raise RuntimeError(f"codex execpolicy check returned invalid JSON for {' '.join(tokens)}: {raw}") from exc
        decision = str(payload.get("decision", "")).strip().lower()
        if decision != expected:
            raise RuntimeError(
                f"codex execpolicy mismatch for {' '.join(tokens)}: expected '{expected}', got '{decision or 'none'}'"
            )
    if not checks:
        warnings.append("No blocked/confirmation commands available to validate compiled Codex rules.")
    return warnings


def _build_codex_tier1_block() -> str:
    lines = [
        CODEX_AGENT_DOC_BEGIN,
        "## AIRG Managed Guidance (Tier 1)",
        "",
        "- Use `mcp__ai-runtime-guard__execute_command` instead of native shell commands.",
        "- Use `mcp__ai-runtime-guard__read_file` for file reads.",
        "- Use `mcp__ai-runtime-guard__write_file` for create/overwrite writes.",
        "- Use `mcp__ai-runtime-guard__edit_file` for in-place file edits.",
        "- Use `mcp__ai-runtime-guard__delete_file` for deletions.",
        "- Use `mcp__ai-runtime-guard__list_directory` for directory listing.",
        "",
        "AIRG note: this section is managed by Runtime Guard hardening and may be regenerated.",
        CODEX_AGENT_DOC_END,
    ]
    return "\n".join(lines).strip() + "\n"


def _codex_has_airg_mcp(path: pathlib.Path) -> bool:
    text = _read_text_optional(path)
    if not text.strip():
        return False
    for line in text.splitlines():
        match = _CODEX_SECTION_RE.match(line)
        if not match:
            continue
        if str(match.group(1)).strip() == "mcp_servers.ai-runtime-guard":
            return True
    return False


def _detect_codex_mcp_locations(workspace: pathlib.Path) -> list[dict[str, str]]:
    candidates = [
        ("global", _home() / ".codex" / "config.toml"),
        ("project", workspace / ".codex" / "config.toml"),
    ]
    found: list[dict[str, str]] = []
    for scope, path in candidates:
        if _codex_has_airg_mcp(path):
            found.append({"scope": scope, "path": str(path)})
    return found


def _remove_codex_airg_sections(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    skip = False
    for line in lines:
        match = _CODEX_SECTION_RE.match(line)
        if match:
            section = str(match.group(1)).strip()
            if skip and section != "mcp_servers.ai-runtime-guard.env":
                skip = False
            if section in {"mcp_servers.ai-runtime-guard", "mcp_servers.ai-runtime-guard.env"}:
                skip = True
                continue
            if skip:
                continue
        if skip:
            continue
        out.append(line)
    cleaned = "\n".join(out).rstrip()
    return (cleaned + "\n") if cleaned else ""


def _codex_airg_mcp_block(paths: dict[str, pathlib.Path], workspace: pathlib.Path, agent_id: str) -> str:
    server_entry = _airg_server_block(paths, workspace, agent_id)
    command = str(server_entry.get("command", "")).strip()
    args = [str(v) for v in (server_entry.get("args") or [])]
    env = server_entry.get("env") if isinstance(server_entry.get("env"), dict) else {}
    lines = [
        "[mcp_servers.ai-runtime-guard]",
        f"command = {_toml_string(command)}",
        f"args = {_toml_string_list(args)}",
        "",
        "[mcp_servers.ai-runtime-guard.env]",
    ]
    for key in sorted(env.keys()):
        lines.append(f"{key} = {_toml_string(str(env[key]))}")
    return "\n".join(lines).rstrip() + "\n"


def _apply_codex_mcp(paths: dict[str, pathlib.Path], workspace: pathlib.Path, agent_id: str, scope: str) -> dict[str, Any]:
    target = (workspace / ".codex" / "config.toml") if scope == "project" else _codex_config_path()
    before = _read_text_optional(target)
    cleaned = _remove_codex_airg_sections(before)
    block = _codex_airg_mcp_block(paths, workspace, agent_id)
    after = (cleaned.rstrip() + "\n\n" + block) if cleaned.strip() else block
    change = _write_text_with_backup(paths, target, after, agent_id)
    change["scope"] = scope
    return change


def _strip_codex_tier3_config(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    in_workspace_write = False
    for line in lines:
        section_match = _CODEX_SECTION_RE.match(line)
        if section_match:
            section_name = str(section_match.group(1)).strip()
            if in_workspace_write:
                in_workspace_write = False
            if section_name == "sandbox_workspace_write":
                in_workspace_write = True
                continue
        if in_workspace_write:
            continue
        stripped = line.strip()
        if stripped.startswith("sandbox_mode"):
            continue
        if stripped.startswith("approval_policy"):
            continue
        out.append(line)
    cleaned = "\n".join(out).rstrip()
    return (cleaned + "\n") if cleaned else ""


def _toml_bool(value: bool) -> str:
    return "true" if bool(value) else "false"


def _toml_string(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_string_list(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(v) for v in values) + "]"


def _render_codex_tier3_config(options: dict[str, Any]) -> str:
    sandbox_mode = str(options.get("tier3_sandbox_mode", "workspace-write")).strip().lower()
    approval_policy = str(options.get("tier3_approval_policy", "on-request")).strip().lower()
    network_access = bool(options.get("tier3_workspace_write_network_access", False))
    writable_roots = options.get("tier3_workspace_write_writable_roots", [])
    roots = writable_roots if isinstance(writable_roots, list) else []
    lines = [
        f"sandbox_mode = {_toml_string(sandbox_mode)}",
        f"approval_policy = {_toml_string(approval_policy)}",
    ]
    if sandbox_mode == "workspace-write":
        lines.extend(
            [
                "",
                "[sandbox_workspace_write]",
                f"network_access = {_toml_bool(network_access)}",
                f"writable_roots = {_toml_string_list([str(r) for r in roots if str(r).strip()])}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _inject_codex_tier3_top_level(config_text: str, tier3_block: str) -> str:
    lines = config_text.splitlines()
    first_section = None
    for idx, line in enumerate(lines):
        if _CODEX_SECTION_RE.match(line):
            first_section = idx
            break
    tier3_lines = tier3_block.strip().splitlines()
    if first_section is None:
        merged = lines + ([""] if lines and lines[-1].strip() else []) + tier3_lines
        return ("\n".join(merged).rstrip() + "\n") if merged else ""

    prefix = lines[:first_section]
    suffix = lines[first_section:]
    merged_prefix = prefix + ([""] if prefix and prefix[-1].strip() else []) + tier3_lines + [""]
    merged = merged_prefix + suffix
    return "\n".join(merged).rstrip() + "\n"


def _resolve_airg_hook_command() -> str:
    explicit = str(os.environ.get("AIRG_HOOK_COMMAND", "")).strip()
    if explicit:
        parts = shlex.split(explicit)
        if parts:
            cmd = parts[0]
            if os.path.isabs(cmd):
                return cmd
            resolved = shutil.which(cmd)
            if resolved:
                return str(pathlib.Path(resolved).resolve())
            return cmd
        return explicit

    venv = str(os.environ.get("VIRTUAL_ENV", "")).strip()
    if venv:
        candidate = pathlib.Path(venv) / "bin" / "airg-hook"
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())

    exe_dir = pathlib.Path(sys.executable).resolve().parent
    candidate = exe_dir / "airg-hook"
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())

    resolved = shutil.which("airg-hook")
    if resolved:
        return str(pathlib.Path(resolved).resolve())
    return "airg-hook"


def _is_airg_hook_command(value: Any) -> bool:
    cmd = str(value or "").strip()
    if not cmd:
        return False
    if cmd == "airg-hook":
        return True
    return pathlib.Path(cmd).name == "airg-hook"


def _set_deny_tools(settings: dict[str, Any], *, enabled: bool, tools: list[str]) -> None:
    permissions = settings.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
        settings["permissions"] = permissions
    deny = permissions.get("deny")
    deny_list = [str(item).strip() for item in deny if str(item).strip()] if isinstance(deny, list) else []
    managed_set = {tool for tool in tools if tool in set(CLAUDE_NATIVE_TOOLS)}
    if enabled:
        for tool in managed_set:
            if tool not in deny_list:
                deny_list.append(tool)
    else:
        deny_list = [item for item in deny_list if item not in managed_set]
    if deny_list:
        permissions["deny"] = deny_list
    else:
        permissions.pop("deny", None)
    if not permissions:
        settings.pop("permissions", None)


def _set_airg_hook(
    settings: dict[str, Any],
    *,
    basic_enforcement: bool,
    advanced_enforcement: bool,
) -> None:
    hook_command = _resolve_airg_hook_command()
    hooks_payload = settings.get("hooks")
    hooks = hooks_payload if isinstance(hooks_payload, dict) else {}
    pre = hooks.get("PreToolUse")
    pre_list = pre if isinstance(pre, list) else []
    requested_matchers: list[str] = []
    if basic_enforcement:
        requested_matchers.extend(CLAUDE_TIER1_TOOLS)
    if advanced_enforcement:
        requested_matchers.extend(CLAUDE_TIER2_TOOLS)
    requested_matchers = list(dict.fromkeys(requested_matchers))

    # Remove any previously managed AIRG hook entries first (including legacy matcher="*").
    cleaned_pre: list[dict[str, Any]] = []
    for matcher in pre_list:
        if not isinstance(matcher, dict):
            continue
        hook_list = matcher.get("hooks")
        if not isinstance(hook_list, list):
            continue
        filtered_hooks = [
            hook
            for hook in hook_list
            if not (isinstance(hook, dict) and _is_airg_hook_command(hook.get("command")))
        ]
        if filtered_hooks:
            next_matcher = dict(matcher)
            next_matcher["hooks"] = filtered_hooks
            cleaned_pre.append(next_matcher)

    if requested_matchers:
        for tool_matcher in requested_matchers:
            target = None
            for matcher in cleaned_pre:
                if str(matcher.get("matcher", "")).strip() == tool_matcher:
                    target = matcher
                    break
            if target is None:
                target = {"matcher": tool_matcher, "hooks": []}
                cleaned_pre.append(target)
            hook_list = target.get("hooks")
            if not isinstance(hook_list, list):
                hook_list = []
                target["hooks"] = hook_list
            if not any(isinstance(hook, dict) and _is_airg_hook_command(hook.get("command")) for hook in hook_list):
                hook_list.append({"type": "command", "command": hook_command})

        hooks["PreToolUse"] = cleaned_pre
        settings["hooks"] = hooks
        return

    if cleaned_pre:
        hooks["PreToolUse"] = cleaned_pre
    else:
        hooks.pop("PreToolUse", None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)


def _set_sandbox_flags(
    settings: dict[str, Any],
    *,
    sandbox_enabled: bool,
    sandbox_escape_closed: bool,
) -> None:
    sandbox_payload = settings.get("sandbox")
    sandbox = sandbox_payload if isinstance(sandbox_payload, dict) else {}
    if sandbox_enabled:
        sandbox["enabled"] = True
        sandbox["autoAllowBashIfSandboxed"] = False
    else:
        sandbox.pop("enabled", None)
        sandbox.pop("autoAllowBashIfSandboxed", None)
    if sandbox_escape_closed:
        sandbox["allowUnsandboxedCommands"] = False
    else:
        sandbox.pop("allowUnsandboxedCommands", None)
    if sandbox:
        settings["sandbox"] = sandbox
    else:
        settings.pop("sandbox", None)


def _apply_claude_hardening_to_settings(before: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    after = _deep_copy(before)
    basic_enforcement = bool(options.get("basic_enforcement", True))
    advanced_enforcement = bool(options.get("advanced_enforcement", False))
    _set_airg_hook(
        after,
        basic_enforcement=basic_enforcement,
        advanced_enforcement=advanced_enforcement,
    )
    _set_deny_tools(
        after,
        enabled=basic_enforcement,
        tools=list(CLAUDE_TIER1_TOOLS),
    )
    _set_sandbox_flags(
        after,
        sandbox_enabled=bool(options.get("sandbox_enabled", True)),
        sandbox_escape_closed=bool(options.get("sandbox_escape_closed", True)),
    )
    return after


def _cursor_mcp_path(workspace: pathlib.Path) -> pathlib.Path:
    return workspace / ".cursor" / "mcp.json"


def _workspace_mcp_path(workspace: pathlib.Path) -> pathlib.Path:
    return workspace / ".mcp.json"


def _mcp_probe_paths(workspace: pathlib.Path) -> list[pathlib.Path]:
    return [
        workspace / ".mcp.json",
        _home() / ".claude.json",
        *_claude_managed_paths(),
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


def _detect_claude_mcp_locations(workspace: pathlib.Path) -> list[dict[str, str]]:
    home = _home()
    found: list[dict[str, str]] = []

    workspace_mcp = workspace / ".mcp.json"
    if _contains_airg_mcp(_read_json_file_optional(workspace_mcp)):
        found.append({"scope": "project", "path": str(workspace_mcp)})

    claude_local = home / ".claude.json"
    claude_payload = _read_json_file_optional(claude_local)
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
        if _contains_airg_mcp(_read_json_file_optional(managed)):
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


def _backup_path_for(target: pathlib.Path) -> pathlib.Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = target.parent / ".airg-backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir / f"{target.name}.{ts}.bak"


def _write_with_backup(target: pathlib.Path, merged_payload: dict[str, Any]) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    before_exists = target.exists()
    before_payload: dict[str, Any] = _read_json_file(target) if before_exists else {}

    backup_path = _backup_path_for(target)
    if before_exists:
        shutil.copy2(target, backup_path)
    else:
        backup_path.write_text("{}\n")

    target.write_text(json.dumps(merged_payload, indent=2) + "\n")

    verify_payload = _read_json_file(target)
    if _canonical(verify_payload) != _canonical(merged_payload):
        if before_exists:
            shutil.copy2(backup_path, target)
        else:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        raise RuntimeError(f"Verification failed after writing {target}")

    return {
        "target_path": str(target),
        "backup_path": str(backup_path),
        "original_missing": not before_exists,
        "before": before_payload,
        "after": merged_payload,
        "diff_summary": _summarize_diff(before_payload, merged_payload),
    }


def _load_state(paths: dict[str, pathlib.Path]) -> dict[str, Any]:
    state_file = _state_path(paths)
    payload = {"profiles": {}}
    if not state_file.exists():
        return payload
    try:
        data = json.loads(state_file.read_text())
        if isinstance(data, dict) and isinstance(data.get("profiles"), dict):
            return data
    except Exception:
        pass
    return payload


def _save_state(paths: dict[str, pathlib.Path], payload: dict[str, Any]) -> None:
    state_file = _state_path(paths)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(payload, indent=2) + "\n")


def _update_profile_state(paths: dict[str, pathlib.Path], profile_id: str, record: dict[str, Any]) -> None:
    state = _load_state(paths)
    profiles = state.setdefault("profiles", {})
    profiles[profile_id] = record
    _save_state(paths, state)


def _clear_profile_state(paths: dict[str, pathlib.Path], profile_id: str) -> None:
    state = _load_state(paths)
    profiles = state.setdefault("profiles", {})
    profiles.pop(profile_id, None)
    _save_state(paths, state)


def _profile_state(paths: dict[str, pathlib.Path], profile_id: str) -> dict[str, Any]:
    state = _load_state(paths)
    profiles = state.get("profiles", {})
    if not isinstance(profiles, dict):
        return {}
    record = profiles.get(profile_id, {})
    return record if isinstance(record, dict) else {}


def undo_available(paths: dict[str, pathlib.Path], profile_id: str) -> bool:
    return bool(_profile_state(paths, profile_id).get("changes"))


def _restore_change(change: dict[str, Any]) -> None:
    target = pathlib.Path(str(change.get("target_path", "")).strip())
    backup = pathlib.Path(str(change.get("backup_path", "")).strip())
    original_missing = bool(change.get("original_missing", False))
    if not str(target):
        return
    if original_missing:
        if target.exists():
            target.unlink()
        return
    if not backup.exists():
        raise FileNotFoundError(f"Backup not found for restore: {backup}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, target)


def _mcp_present_for_claude(workspace: pathlib.Path) -> bool:
    return bool(_detect_claude_mcp_locations(workspace))


def _apply_claude_mcp_for_scope(
    paths: dict[str, pathlib.Path],
    workspace: pathlib.Path,
    agent_id: str,
    scope: str,
) -> dict[str, Any]:
    normalized_scope = _normalize_claude_scope(scope)
    server_block = _airg_server_block(paths, workspace, agent_id)

    if normalized_scope == "project":
        target = _workspace_mcp_path(workspace)
        before = _read_json_file(target) if target.exists() else {}
        after = _deep_merge_union(
            before,
            {"mcpServers": {"ai-runtime-guard": server_block}},
        )
        change = _write_with_backup(target, after)
        change["scope"] = normalized_scope
        return change

    claude_local = _home() / ".claude.json"
    before = _read_json_file(claude_local) if claude_local.exists() else {}
    after = _deep_copy(before)
    if normalized_scope == "user":
        servers = after.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
        servers["ai-runtime-guard"] = server_block
        after["mcpServers"] = servers
    else:
        projects = after.get("projects")
        if not isinstance(projects, dict):
            projects = {}
        workspace_key = str(workspace)
        project_entry = projects.get(workspace_key)
        if not isinstance(project_entry, dict):
            project_entry = {}
        servers = project_entry.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
        servers["ai-runtime-guard"] = server_block
        project_entry["mcpServers"] = servers
        projects[workspace_key] = project_entry
        after["projects"] = projects
    change = _write_with_backup(claude_local, after)
    change["scope"] = normalized_scope
    return change


def _apply_claude(
    paths: dict[str, pathlib.Path],
    profile: dict[str, Any],
    *,
    options: dict[str, Any] | None,
    auto_add_mcp: bool,
) -> dict[str, Any]:
    workspace = _workspace_path(profile)
    agent_id = str(profile.get("agent_id", "")).strip() or "default"
    profile_id = str(profile.get("profile_id", "")).strip()
    selected_options = _normalize_claude_hardening_options(profile, options)
    selected_scope = selected_options["scope"]

    changes: list[dict[str, Any]] = []
    hardening_changes: list[dict[str, Any]] = []
    preflight = {
        "mcp_locations": _detect_claude_mcp_locations(workspace),
        "mcp_probe_paths": [str(p) for p in _mcp_probe_paths(workspace)],
        "selected_scope": selected_scope,
        "settings_target": str(_claude_settings_path_for_scope(workspace, selected_scope)),
    }
    preflight["mcp_present"] = bool(preflight["mcp_locations"])
    preflight["mcp_detected_scopes"] = list(
        dict.fromkeys(
            [
                str(item.get("scope", "")).strip()
                for item in preflight["mcp_locations"]
                if isinstance(item, dict) and str(item.get("scope", "")).strip()
            ]
        )
    )

    try:
        if not preflight["mcp_present"]:
            if not auto_add_mcp:
                return {
                    "ok": False,
                    "requires_mcp": True,
                    "errors": [
                        "AIRG MCP server was not detected in Claude MCP config. Add it first or allow auto-add in this action."
                    ],
                    "preflight": preflight,
                }
            auto_add_scope = selected_scope if selected_scope == "project" else "project"
            mcp_change = _apply_claude_mcp_for_scope(paths, workspace, agent_id, auto_add_scope)
            changes.append(mcp_change)
            preflight["mcp_present"] = True
            preflight["mcp_auto_added"] = True
            preflight["mcp_auto_add_scope"] = auto_add_scope
            preflight["mcp_locations"] = [
                {"scope": str(mcp_change.get("scope", auto_add_scope)), "path": str(mcp_change.get("target_path", ""))}
            ]
            preflight["mcp_detected_scopes"] = [str(mcp_change.get("scope", auto_add_scope))]

        target = _claude_settings_path_for_scope(workspace, selected_scope)
        before = _read_json_file(target) if target.exists() else {}
        after = _apply_claude_hardening_to_settings(before, selected_options)
        settings_change = _write_with_backup(target, after)
        changes.append(settings_change)
        hardening_changes.append(settings_change)
    except Exception:
        for change in reversed(changes):
            _restore_change(change)
        raise

    summary: list[str] = []
    for change in changes:
        summary.extend(change.get("diff_summary", []))

    record = {
        "profile_id": profile_id,
        "agent_type": "claude_code",
        "applied_at": _now_iso(),
        "changes": [
            {
                "target_path": c["target_path"],
                "backup_path": c["backup_path"],
                "original_missing": c["original_missing"],
            }
            for c in hardening_changes
        ],
        "diff_summary": summary,
        "applied_options": selected_options,
    }
    _update_profile_state(paths, profile_id, record)

    return {
        "ok": True,
        "profile_id": profile_id,
        "agent_type": "claude_code",
        "target_path": str(target),
        "changes": changes,
        "diff_summary": summary,
        "preflight": preflight,
        "applied_options": selected_options,
        "undo_available": True,
    }


def _apply_codex(
    paths: dict[str, pathlib.Path],
    profile: dict[str, Any],
    *,
    options: dict[str, Any] | None,
    auto_add_mcp: bool,
) -> dict[str, Any]:
    workspace = _workspace_path(profile)
    agent_id = str(profile.get("agent_id", "")).strip() or "default"
    profile_id = str(profile.get("profile_id", "")).strip()
    selected_options = _normalize_codex_hardening_options(options)

    changes: list[dict[str, Any]] = []
    hardening_changes: list[dict[str, Any]] = []
    warnings: list[str] = []

    preflight = {
        "selected_scope": str(profile.get("agent_scope", "")).strip().lower() or "global",
        "mcp_locations": _detect_codex_mcp_locations(workspace),
    }
    preflight["mcp_present"] = bool(preflight["mcp_locations"])
    preflight["mcp_detected_scopes"] = list(
        dict.fromkeys(
            [
                str(item.get("scope", "")).strip()
                for item in preflight["mcp_locations"]
                if isinstance(item, dict) and str(item.get("scope", "")).strip()
            ]
        )
    )

    try:
        if not preflight["mcp_present"]:
            if not auto_add_mcp:
                return {
                    "ok": False,
                    "requires_mcp": True,
                    "errors": [
                        "AIRG MCP server was not detected in Codex config.toml. Add it first or allow auto-add in this action."
                    ],
                    "preflight": preflight,
                }
            scope = "project" if str(profile.get("agent_scope", "")).strip().lower() == "project" else "global"
            mcp_change = _apply_codex_mcp(paths, workspace, agent_id, scope)
            if mcp_change.get("changed"):
                mcp_change["diff_summary"] = [f"+ {mcp_change.get('target_path', '')} (MCP auto-add)"]
                changes.append(mcp_change)
            preflight["mcp_present"] = True
            preflight["mcp_auto_added"] = True
            preflight["mcp_auto_add_scope"] = scope
            preflight["mcp_locations"] = _detect_codex_mcp_locations(workspace)
            preflight["mcp_detected_scopes"] = list(
                dict.fromkeys(
                    [
                        str(item.get("scope", "")).strip()
                        for item in preflight["mcp_locations"]
                        if isinstance(item, dict) and str(item.get("scope", "")).strip()
                    ]
                )
            )

        agents_path = _codex_agents_doc_path()
        tier1_before = _read_text_optional(agents_path)
        tier1_after = _upsert_managed_block(
            tier1_before,
            CODEX_AGENT_DOC_BEGIN,
            CODEX_AGENT_DOC_END,
            _build_codex_tier1_block() if bool(selected_options.get("tier1_guidance", True)) else "",
        )
        tier1_change = _write_text_with_backup(paths, agents_path, tier1_after, agent_id)
        if tier1_change.get("changed"):
            hardening_changes.append(tier1_change)
            changes.append(tier1_change)

        rules_path = _codex_rules_path()
        rules_before = _read_text_optional(rules_path)
        include_confirmation = bool(selected_options.get("tier2_include_requires_confirmation", False))
        policy = _effective_policy_for_agent(paths, agent_id)
        policy_hash = _sha256_text(_canonical(policy))
        if bool(selected_options.get("tier2_mirror", True)):
            rule_lines, blocked_tokens, prompted_tokens = _compile_codex_rules(
                policy,
                include_requires_confirmation=include_confirmation,
            )
            meta = {
                "agent_id": agent_id,
                "workspace": str(workspace),
                "policy_hash": policy_hash,
                "include_requires_confirmation": include_confirmation,
                "generated_at": _now_iso(),
                "generator": "airg",
            }
            rules_block, generated_rules_hash = _render_codex_rules_block(meta, rule_lines)
            rules_after = _upsert_managed_block(rules_before, CODEX_RULES_BEGIN, CODEX_RULES_END, rules_block)
            rules_change = _write_text_with_backup(paths, rules_path, rules_after, agent_id)
            if rules_change.get("changed"):
                hardening_changes.append(rules_change)
                changes.append(rules_change)
            warnings.extend(_validate_codex_rules_file(rules_path, blocked_tokens, prompted_tokens))
        else:
            rules_after = _upsert_managed_block(rules_before, CODEX_RULES_BEGIN, CODEX_RULES_END, "")
            rules_change = _write_text_with_backup(paths, rules_path, rules_after, agent_id)
            if rules_change.get("changed"):
                hardening_changes.append(rules_change)
                changes.append(rules_change)
            generated_rules_hash = ""

        codex_cfg_path = _codex_config_path()
        config_before = _read_text_optional(codex_cfg_path)
        stripped = _strip_codex_tier3_config(config_before)
        tier3_block = _render_codex_tier3_config(selected_options)
        config_after = _inject_codex_tier3_top_level(stripped, tier3_block)
        try:
            tomllib.loads(config_after)
        except Exception as exc:
            raise RuntimeError(f"Generated Codex config.toml is invalid: {exc}") from exc
        config_change = _write_text_with_backup(paths, codex_cfg_path, config_after, agent_id)
        if config_change.get("changed"):
            hardening_changes.append(config_change)
            changes.append(config_change)

    except Exception:
        for change in reversed(changes):
            _restore_change(change)
        raise

    summary: list[str] = []
    for change in changes:
        summary.extend(change.get("diff_summary", []))

    record = {
        "profile_id": profile_id,
        "agent_type": "codex",
        "applied_at": _now_iso(),
        "changes": [
            {
                "target_path": c["target_path"],
                "backup_path": c["backup_path"],
                "original_missing": c["original_missing"],
            }
            for c in hardening_changes
        ],
        "diff_summary": summary,
        "applied_options": selected_options,
        "policy_hash": policy_hash,
        "generated_rules_hash": generated_rules_hash,
    }
    _update_profile_state(paths, profile_id, record)

    return {
        "ok": True,
        "profile_id": profile_id,
        "agent_type": "codex",
        "changes": changes,
        "diff_summary": summary,
        "preflight": preflight,
        "applied_options": selected_options,
        "warnings": warnings,
        "undo_available": True,
    }


def _apply_cursor(paths: dict[str, pathlib.Path], profile: dict[str, Any]) -> dict[str, Any]:
    workspace = _workspace_path(profile)
    agent_id = str(profile.get("agent_id", "")).strip() or "default"
    profile_id = str(profile.get("profile_id", "")).strip()

    target = _cursor_mcp_path(workspace)
    overlay = {
        "mcpServers": {
            "ai-runtime-guard": _airg_server_block(paths, workspace, agent_id),
        }
    }

    before = _read_json_file(target) if target.exists() else {}
    after = _deep_merge_union(before, overlay)
    change = _write_with_backup(target, after)
    record = {
        "profile_id": profile_id,
        "agent_type": "cursor",
        "applied_at": _now_iso(),
        "changes": [
            {
                "target_path": change["target_path"],
                "backup_path": change["backup_path"],
                "original_missing": change["original_missing"],
            }
        ],
        "diff_summary": change.get("diff_summary", []),
    }
    _update_profile_state(paths, profile_id, record)

    return {
        "ok": True,
        "profile_id": profile_id,
        "agent_type": "cursor",
        "target_path": str(target),
        "changes": [change],
        "diff_summary": change.get("diff_summary", []),
        "undo_available": True,
    }


def apply_hardening(
    paths: dict[str, pathlib.Path],
    profile: dict[str, Any],
    *,
    options: dict[str, Any] | None = None,
    auto_add_mcp: bool = False,
) -> dict[str, Any]:
    agent_type = str(profile.get("agent_type", "")).strip().lower()
    profile_id = str(profile.get("profile_id", "")).strip()
    if not profile_id:
        return {"ok": False, "errors": ["profile_id is required"]}

    try:
        if agent_type == "claude_code":
            return _apply_claude(paths, profile, options=options, auto_add_mcp=auto_add_mcp)
        if agent_type == "codex":
            return _apply_codex(paths, profile, options=options, auto_add_mcp=auto_add_mcp)
        if agent_type == "cursor":
            return _apply_cursor(paths, profile)
        return {
            "ok": False,
            "errors": [f"Agent type '{agent_type or 'unknown'}' is not supported for config hardening in dev2."],
        }
    except Exception as exc:
        return {"ok": False, "errors": [str(exc)]}


def undo_hardening(paths: dict[str, pathlib.Path], profile: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(profile.get("profile_id", "")).strip()
    if not profile_id:
        return {"ok": False, "errors": ["profile_id is required"]}

    record = _profile_state(paths, profile_id)
    changes = record.get("changes", []) if isinstance(record, dict) else []
    if not isinstance(changes, list) or not changes:
        return {"ok": False, "errors": ["No hardening backup record found for this profile."]}

    try:
        for change in reversed(changes):
            if not isinstance(change, dict):
                continue
            _restore_change(change)
    except Exception as exc:
        return {"ok": False, "errors": [f"Undo failed: {exc}"]}

    _clear_profile_state(paths, profile_id)
    return {
        "ok": True,
        "profile_id": profile_id,
        "undone_changes": len(changes),
        "undo_available": False,
    }
