import datetime
import os
import pathlib
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context
else:
    Context = Any

from audit import append_log_entry, build_log_entry
from backup import backup_paths
from config import AGENT_ID, POLICY, WORKSPACE_ROOT, refresh_policy_if_changed
from models import PolicyResult
from policy_engine import check_path_policy, relative_depth
from runtime_context import activate_runtime_context, reset_runtime_context
import script_sentinel


def read_file(path: str, ctx: Context | None = None) -> str:
    """Read a text file from the workspace after path-policy enforcement."""
    context_tokens = activate_runtime_context(ctx)
    path = str(pathlib.Path(WORKSPACE_ROOT) / path) if not os.path.isabs(path) else path

    try:
        refresh_policy_if_changed()
        path_check = check_path_policy(path, tool="read_file")
        if path_check:
            result = PolicyResult(allowed=False, reason=path_check[0], decision_tier="blocked", matched_rule=path_check[1])
        else:
            result = PolicyResult(allowed=True, reason="allowed", decision_tier="allowed", matched_rule=None)

        append_log_entry(build_log_entry("read_file", result, path=path))
        if not result.allowed:
            return f"[POLICY BLOCK] {result.reason}"

        try:
            with open(path, "r", errors="replace") as f:
                return f.read()
        except FileNotFoundError:
            return f"Error: file not found: {path}"
        except OSError as e:
            return f"Error reading file: {e}"
    finally:
        reset_runtime_context(context_tokens)


def write_file(path: str, content: str, ctx: Context | None = None) -> str:
    """Write full file content with policy checks, logging, and backup support."""
    context_tokens = activate_runtime_context(ctx)
    path = str(pathlib.Path(WORKSPACE_ROOT) / path) if not os.path.isabs(path) else path

    try:
        refresh_policy_if_changed()
        path_check = check_path_policy(path, tool="write_file")
        if path_check:
            result = PolicyResult(allowed=False, reason=path_check[0], decision_tier="blocked", matched_rule=path_check[1])
        else:
            result = PolicyResult(allowed=True, reason="allowed", decision_tier="allowed", matched_rule=None)

        log_entry = build_log_entry("write_file", result, path=path)
        append_log_entry(log_entry)
        if not result.allowed:
            return f"[POLICY BLOCK] {result.reason}"

        backup_location = None
        backup_enabled = bool(POLICY.get("audit", {}).get("backup_enabled", True))
        if backup_enabled and os.path.exists(path):
            backup_location = backup_paths([path])
            if backup_location:
                append_log_entry(
                    {
                        **log_entry,
                        "source": "mcp-server",
                        "backup_location": backup_location,
                        "event": "backup_created",
                    }
                )

        try:
            with open(path, "w") as f:
                f.write(content)
        except OSError as e:
            return f"Error writing file: {e}"

        sentinel_scan = script_sentinel.scan_and_record_write(path, content, writer_agent_id=AGENT_ID)
        if sentinel_scan.get("flagged"):
            append_log_entry(
                {
                    **log_entry,
                    "source": "mcp-server",
                    "event": "script_sentinel_flagged",
                    "content_hash": sentinel_scan.get("content_hash", ""),
                    "matched_signatures": sentinel_scan.get("matched_signatures", []),
                    "script_sentinel_mode": POLICY.get("script_sentinel", {}).get("mode", "match_original"),
                    "script_sentinel_scan_mode": sentinel_scan.get("scan_mode", POLICY.get("script_sentinel", {}).get("scan_mode", "exec_context")),
                }
            )

        msg = f"Successfully wrote {len(content)} characters to {path}"
        if backup_location:
            msg += f" (previous version backed up to {backup_location})"
        else:
            msg += " (no content-change backup needed)"
        if sentinel_scan.get("flagged"):
            msg += " (Script Sentinel flagged content)"
        return msg
    finally:
        reset_runtime_context(context_tokens)


def edit_file(
    path: str,
    old_text: str = "",
    new_text: str = "",
    replace_all: bool = False,
    edits: list[dict[str, Any]] | None = None,
    ctx: Context | None = None,
) -> str:
    """Apply targeted text replacements in an existing file with backups."""
    context_tokens = activate_runtime_context(ctx)
    path = str(pathlib.Path(WORKSPACE_ROOT) / path) if not os.path.isabs(path) else path

    try:
        refresh_policy_if_changed()
        path_check = check_path_policy(path, tool="edit_file")
        if path_check:
            result = PolicyResult(allowed=False, reason=path_check[0], decision_tier="blocked", matched_rule=path_check[1])
        else:
            result = PolicyResult(allowed=True, reason="allowed", decision_tier="allowed", matched_rule=None)

        log_entry = build_log_entry("edit_file", result, path=path)
        append_log_entry(log_entry)
        if not result.allowed:
            return f"[POLICY BLOCK] {result.reason}"

        target = pathlib.Path(path)
        if not target.exists():
            return f"Error: file not found: {path}"
        if not target.is_file():
            return f"Error: '{path}' is not a regular file"

        try:
            original = target.read_text(errors="replace")
        except OSError as e:
            return f"Error reading file for edit: {e}"

        operations: list[tuple[str, str, bool]] = []
        if edits is not None:
            if not isinstance(edits, list):
                return "Error: edits must be a list of {old_text, new_text, replace_all?} objects"
            for idx, item in enumerate(edits, start=1):
                if not isinstance(item, dict):
                    return f"Error: edit #{idx} is not an object"
                item_old = str(item.get("old_text", ""))
                item_new = str(item.get("new_text", ""))
                item_replace_all = bool(item.get("replace_all", False))
                if not item_old:
                    return f"Error: edit #{idx} has empty old_text"
                operations.append((item_old, item_new, item_replace_all))
        else:
            if not old_text:
                return "Error: old_text is required when edits is not provided"
            operations.append((str(old_text), str(new_text), bool(replace_all)))

        updated = original
        total_replacements = 0
        for idx, (needle, replacement, replace_everywhere) in enumerate(operations, start=1):
            matches = updated.count(needle)
            if matches == 0:
                return f"Error: edit #{idx} old_text not found in file"
            if not replace_everywhere and matches > 1:
                return (
                    f"Error: edit #{idx} old_text matched {matches} times; "
                    "set replace_all=true for this edit to apply all matches"
                )
            if replace_everywhere:
                updated = updated.replace(needle, replacement)
                total_replacements += matches
            else:
                updated = updated.replace(needle, replacement, 1)
                total_replacements += 1

        if updated == original:
            return f"No changes made to {path}"

        backup_location = None
        backup_enabled = bool(POLICY.get("audit", {}).get("backup_enabled", True))
        if backup_enabled:
            backup_location = backup_paths([path])
            if backup_location:
                append_log_entry(
                    {
                        **log_entry,
                        "source": "mcp-server",
                        "backup_location": backup_location,
                        "event": "backup_created",
                    }
                )

        try:
            target.write_text(updated)
        except OSError as e:
            return f"Error writing edited file: {e}"

        sentinel_scan = script_sentinel.scan_and_record_write(path, updated, writer_agent_id=AGENT_ID)
        if sentinel_scan.get("flagged"):
            append_log_entry(
                {
                    **log_entry,
                    "source": "mcp-server",
                    "event": "script_sentinel_flagged",
                    "content_hash": sentinel_scan.get("content_hash", ""),
                    "matched_signatures": sentinel_scan.get("matched_signatures", []),
                    "script_sentinel_mode": POLICY.get("script_sentinel", {}).get("mode", "match_original"),
                    "script_sentinel_scan_mode": sentinel_scan.get("scan_mode", POLICY.get("script_sentinel", {}).get("scan_mode", "exec_context")),
                }
            )

        msg = (
            f"Successfully edited {path} "
            f"({total_replacements} replacement{'s' if total_replacements != 1 else ''} across {len(operations)} edit operation{'s' if len(operations) != 1 else ''})"
        )
        if backup_location:
            msg += f" (previous version backed up to {backup_location})"
        else:
            msg += " (no content-change backup needed)"
        if sentinel_scan.get("flagged"):
            msg += " (Script Sentinel flagged content)"
        return msg
    finally:
        reset_runtime_context(context_tokens)


def delete_file(path: str, ctx: Context | None = None) -> str:
    """Delete a single file after policy checks and optional pre-delete backup."""
    context_tokens = activate_runtime_context(ctx)
    path = str(pathlib.Path(WORKSPACE_ROOT) / path) if not os.path.isabs(path) else path

    try:
        refresh_policy_if_changed()
        path_check = check_path_policy(path, tool="delete_file")
        if path_check:
            result = PolicyResult(allowed=False, reason=path_check[0], decision_tier="blocked", matched_rule=path_check[1])
        else:
            result = PolicyResult(allowed=True, reason="allowed", decision_tier="allowed", matched_rule=None)

        if result.allowed:
            if not os.path.exists(path):
                append_log_entry(build_log_entry("delete_file", result, path=path, error="file not found"))
                return f"Error: file not found: {path}"

            if os.path.isdir(path):
                result = PolicyResult(
                    allowed=False,
                    reason=f"'{path}' is a directory  -  delete_file only removes individual files. Use execute_command for directory operations (note: bulk/recursive deletions are also subject to policy).",
                    decision_tier="blocked",
                    matched_rule=None,
                )

        log_entry = build_log_entry("delete_file", result, path=path)
        if not result.allowed:
            append_log_entry(log_entry)
            return f"[POLICY BLOCK] {result.reason}"

        backup_enabled = bool(POLICY.get("audit", {}).get("backup_enabled", True))
        backup_location = backup_paths([path]) if backup_enabled else ""
        if backup_location:
            log_entry["backup_location"] = backup_location

        append_log_entry(log_entry)

        try:
            os.remove(path)
        except OSError as e:
            return f"Error deleting file: {e}"

        return f"Successfully deleted {path}. " + (
            f"Backup saved to {backup_location}  -  the file can be recovered from there."
            if backup_location
            else "No content-change backup was needed."
        )
    finally:
        reset_runtime_context(context_tokens)


def list_directory(path: str, ctx: Context | None = None) -> str:
    """List directory entries with metadata, honoring path and depth policy."""
    context_tokens = activate_runtime_context(ctx)
    path = str(pathlib.Path(WORKSPACE_ROOT) / path) if not os.path.isabs(path) else path

    try:
        refresh_policy_if_changed()
        path_check = check_path_policy(path, tool="list_directory")
        if path_check:
            result = PolicyResult(allowed=False, reason=path_check[0], decision_tier="blocked", matched_rule=path_check[1])
        else:
            result = PolicyResult(allowed=True, reason="allowed", decision_tier="allowed", matched_rule=None)

        if result.allowed:
            if not os.path.exists(path):
                append_log_entry(build_log_entry("list_directory", result, path=path, error="path not found"))
                return f"Error: path not found: {path}"

            if not os.path.isdir(path):
                append_log_entry(build_log_entry("list_directory", result, path=path, error="not a directory"))
                return f"Error: '{path}' is a file, not a directory"

            depth = relative_depth(path)
            max_depth = POLICY.get("allowed", {}).get("max_directory_depth", 5)
            if depth > max_depth:
                result = PolicyResult(
                    allowed=False,
                    reason=f"Directory depth {depth} exceeds the policy limit of {max_depth} (allowed.max_directory_depth): '{path}'",
                    decision_tier="blocked",
                    matched_rule="allowed.max_directory_depth",
                )

        append_log_entry(build_log_entry("list_directory", result, path=path))
        if not result.allowed:
            return f"[POLICY BLOCK] {result.reason}"

        lines = [f"Contents of {path}:"]
        try:
            entries = sorted(os.scandir(path), key=lambda e: (e.is_file(), e.name))
        except OSError as e:
            return f"Error reading directory: {e}"

        for entry in entries:
            try:
                stat = entry.stat(follow_symlinks=False)
                mtime = datetime.datetime.fromtimestamp(stat.st_mtime, datetime.UTC).isoformat().replace("+00:00", "Z")
                kind = "file" if entry.is_file(follow_symlinks=False) else "directory"
                size = f"{stat.st_size} bytes" if kind == "file" else "-"
                lines.append(f"  {entry.name}  [{kind}]  size={size}  modified={mtime}")
            except OSError:
                lines.append(f"  {entry.name}  [unreadable]")

        if len(lines) == 1:
            lines.append("  (empty)")

        return "\n".join(lines)
    finally:
        reset_runtime_context(context_tokens)
