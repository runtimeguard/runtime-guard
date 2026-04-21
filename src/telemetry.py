import datetime
import json
import os
import pathlib
import re
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from contextlib import closing
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any

import agent_configs
import reports

DEFAULT_ENDPOINT = "https://telemetry.runtime-guard.ai/v1/telemetry"
REQUEST_TIMEOUT_SECONDS = 5
_VERSION_ALLOWED_RE = re.compile(r"[^0-9A-Za-z.\-+]+")
_VERSION_VALIDATE_RE = re.compile(r"^[0-9A-Za-z.\-+]+$")
_BUCKET_VALUES = {"0", "1", "2-5", "6-10", "11-50", "51-100", "101-1000", "1000+"}
_AGENT_TYPES_ALLOWED = {"claude_code", "cursor", "codex", "windsurf", "cline", "aider", "other"}
_INSTALL_METHOD_ALLOWED = {"pip", "pipx", "editable", "docker", "unknown"}
_PLATFORM_ALLOWED = {"linux", "macos", "windows", "unknown"}


def _debug(message: str) -> None:
    if os.environ.get("AIRG_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}:
        print(f"[airg][telemetry][debug] {message}", file=sys.stderr)


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _utc_today(now: datetime.datetime | None = None) -> str:
    current = now or _utc_now()
    return current.strftime("%Y-%m-%d")


def bucket(value: int) -> str:
    n = max(0, int(value))
    if n == 0:
        return "0"
    if n == 1:
        return "1"
    if n <= 5:
        return "2-5"
    if n <= 10:
        return "6-10"
    if n <= 50:
        return "11-50"
    if n <= 100:
        return "51-100"
    if n <= 1000:
        return "101-1000"
    return "1000+"


def _sanitize_version(raw: str, *, max_len: int) -> str:
    cleaned = _VERSION_ALLOWED_RE.sub("", str(raw or ""))[:max_len]
    if cleaned and _VERSION_VALIDATE_RE.fullmatch(cleaned):
        return cleaned
    return "unknown"


def _airg_version() -> str:
    try:
        raw = package_version("ai-runtime-guard")
    except PackageNotFoundError:
        raw = "unknown"
    return _sanitize_version(raw, max_len=32)


def _platform_string(system_name: str | None = None) -> str:
    if system_name is None:
        import platform as _platform

        system = _platform.system().lower()
    else:
        system = str(system_name).lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    if system == "windows":
        return "windows"
    return "unknown"


def _python_version() -> str:
    raw = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"[:16]
    if raw and _VERSION_VALIDATE_RE.fullmatch(raw):
        return raw
    return "unknown"


def _normalize_agent_type(agent_type: str) -> str:
    normalized = str(agent_type or "").strip().lower()
    mapped = {
        "claude_code": "claude_code",
        "claude_desktop": "claude_code",
        "cursor": "cursor",
        "codex": "codex",
        "windsurf": "windsurf",
        "cline": "cline",
        "aider": "aider",
        "custom": "other",
    }.get(normalized, "other")
    return mapped if mapped in _AGENT_TYPES_ALLOWED else "other"


def normalize_agent_types(agent_types: list[str]) -> list[str]:
    values = sorted({_normalize_agent_type(item) for item in (agent_types or [])})
    return values[:16]


def _load_policy(policy_path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(policy_path.read_text())
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _save_policy(policy_path: pathlib.Path, policy: dict[str, Any]) -> None:
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = policy_path.with_suffix(f"{policy_path.suffix}.tmp")
    tmp.write_text(json.dumps(policy, indent=2) + "\n")
    tmp.replace(policy_path)


def _telemetry_section(policy: dict[str, Any]) -> dict[str, Any]:
    section = policy.get("telemetry")
    if not isinstance(section, dict):
        section = {}
    return section


def _telemetry_enabled(policy: dict[str, Any]) -> bool:
    return bool(_telemetry_section(policy).get("enabled", True))


def _telemetry_endpoint(policy: dict[str, Any]) -> str:
    configured = str(_telemetry_section(policy).get("endpoint", "")).strip()
    return configured or DEFAULT_ENDPOINT


def _last_sent_date(policy: dict[str, Any]) -> str:
    return str(_telemetry_section(policy).get("last_sent_date", "")).strip()


def _load_profiles(approval_db_path: pathlib.Path) -> list[dict[str, Any]]:
    try:
        registry = agent_configs.load_registry({"approval_db_path": approval_db_path})
    except Exception:
        return []
    profiles = registry.get("profiles") if isinstance(registry, dict) else []
    return [row for row in profiles if isinstance(row, dict)] if isinstance(profiles, list) else []


def _sync_reports(policy: dict[str, Any], reports_db_path: pathlib.Path, log_path: pathlib.Path) -> None:
    try:
        reports.sync_from_log(
            db_path=reports_db_path,
            log_path=log_path,
            policy_reports=policy.get("reports", {}) if isinstance(policy, dict) else {},
        )
    except Exception as exc:
        _debug(f"reports sync failed: {exc}")


def _window_counts(reports_db_path: pathlib.Path, now: datetime.datetime | None = None) -> dict[str, int]:
    cutoff = (now or _utc_now()) - datetime.timedelta(days=1)
    cutoff_iso = cutoff.isoformat().replace("+00:00", "Z")

    with closing(sqlite3.connect(reports_db_path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS total_events,
              SUM(CASE WHEN policy_decision='blocked' THEN 1 ELSE 0 END) AS blocked_events,
              SUM(CASE WHEN tool='approve_command' AND event='command_approved' THEN 1 ELSE 0 END) AS approvals_approved,
              SUM(CASE WHEN event='script_sentinel_execute_checked' THEN 1 ELSE 0 END) AS sentinel_flagged,
              SUM(CASE WHEN event='script_sentinel_blocked' THEN 1 ELSE 0 END) AS sentinel_blocked
            FROM events
            WHERE timestamp >= ?
            """,
            (cutoff_iso,),
        ).fetchone()

    if row is None:
        return {
            "total_events": 0,
            "blocked_events": 0,
            "approvals_approved": 0,
            "sentinel_flagged": 0,
            "sentinel_blocked": 0,
        }
    return {
        "total_events": int(row["total_events"] or 0),
        "blocked_events": int(row["blocked_events"] or 0),
        "approvals_approved": int(row["approvals_approved"] or 0),
        "sentinel_flagged": int(row["sentinel_flagged"] or 0),
        "sentinel_blocked": int(row["sentinel_blocked"] or 0),
    }


def build_payload(
    *,
    policy: dict[str, Any],
    reports_db_path: pathlib.Path,
    approval_db_path: pathlib.Path,
    log_path: pathlib.Path,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    _sync_reports(policy, reports_db_path, log_path)
    counts = _window_counts(reports_db_path, now=now)
    profiles = _load_profiles(approval_db_path)
    agent_types = normalize_agent_types([str(row.get("agent_type", "")) for row in profiles])

    sentinel_policy = policy.get("script_sentinel") if isinstance(policy.get("script_sentinel"), dict) else {}
    payload = {
        "airg_version": _airg_version(),
        "platform": _platform_string(),
        "python_version": _python_version(),
        "install_method": "unknown",
        "agents_bucket": bucket(len(profiles)),
        "agent_types": agent_types,
        "events_bucket": bucket(counts["total_events"]),
        "blocked_bucket": bucket(counts["blocked_events"]),
        "approvals_bucket": bucket(counts["approvals_approved"]),
        "sentinel_enabled": bool(sentinel_policy.get("enabled", False)),
        "sentinel_flagged_bucket": bucket(counts["sentinel_flagged"]),
        "sentinel_blocked_bucket": bucket(counts["sentinel_blocked"]),
        "period_days": 1,
    }
    validate_payload(payload)
    return payload


def validate_payload(payload: dict[str, Any]) -> None:
    required_string_keys = [
        "airg_version",
        "platform",
        "python_version",
        "install_method",
        "agents_bucket",
        "events_bucket",
        "blocked_bucket",
        "approvals_bucket",
        "sentinel_flagged_bucket",
        "sentinel_blocked_bucket",
    ]
    for key in required_string_keys:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"telemetry payload field '{key}' must be a non-empty string")

    if payload["platform"] not in _PLATFORM_ALLOWED:
        raise ValueError("telemetry payload field 'platform' is invalid")
    if payload["install_method"] not in _INSTALL_METHOD_ALLOWED:
        raise ValueError("telemetry payload field 'install_method' is invalid")

    for key in ["agents_bucket", "events_bucket", "blocked_bucket", "approvals_bucket", "sentinel_flagged_bucket", "sentinel_blocked_bucket"]:
        if payload.get(key) not in _BUCKET_VALUES:
            raise ValueError(f"telemetry payload field '{key}' is invalid")

    agent_types = payload.get("agent_types")
    if not isinstance(agent_types, list) or len(agent_types) > 16:
        raise ValueError("telemetry payload field 'agent_types' is invalid")
    if any((not isinstance(item, str) or item not in _AGENT_TYPES_ALLOWED) for item in agent_types):
        raise ValueError("telemetry payload field 'agent_types' contains invalid entries")

    sentinel_enabled = payload.get("sentinel_enabled")
    if not isinstance(sentinel_enabled, bool):
        raise ValueError("telemetry payload field 'sentinel_enabled' must be bool")

    period_days = payload.get("period_days")
    if not isinstance(period_days, int) or period_days < 1 or period_days > 30:
        raise ValueError("telemetry payload field 'period_days' must be int in [1,30]")

    airg_version = str(payload.get("airg_version", ""))
    python_version = str(payload.get("python_version", ""))
    if len(airg_version) > 32 or not _VERSION_VALIDATE_RE.fullmatch(airg_version):
        raise ValueError("telemetry payload field 'airg_version' is invalid")
    if len(python_version) > 16 or not _VERSION_VALIDATE_RE.fullmatch(python_version):
        raise ValueError("telemetry payload field 'python_version' is invalid")


def build_payload_from_paths(
    *,
    policy_path: pathlib.Path,
    reports_db_path: pathlib.Path,
    approval_db_path: pathlib.Path,
    log_path: pathlib.Path,
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    policy = _load_policy(policy_path)
    return build_payload(
        policy=policy,
        reports_db_path=reports_db_path,
        approval_db_path=approval_db_path,
        log_path=log_path,
        now=now,
    )


def _send_once(endpoint: str, payload: dict[str, Any], timeout_seconds: int) -> int | None:
    body = json.dumps(payload).encode("utf-8")
    user_agent = f"ai-runtime-guard/{payload.get('airg_version', 'unknown')}"
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(getattr(response, "status", 0))
            if status != 204:
                _debug(f"telemetry send returned status={status}")
            return status
    except urllib.error.HTTPError as exc:
        _debug(f"telemetry send failed with HTTP {exc.code}")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _debug(f"telemetry send failed: {exc}")
        return None


def _update_last_sent_date(policy_path: pathlib.Path, today: str) -> None:
    policy = _load_policy(policy_path)
    telemetry = _telemetry_section(policy)
    updated = dict(telemetry)
    updated["last_sent_date"] = today
    policy = dict(policy)
    policy["telemetry"] = updated
    _save_policy(policy_path, policy)


def maybe_send_daily(
    *,
    policy_path: pathlib.Path,
    reports_db_path: pathlib.Path,
    approval_db_path: pathlib.Path,
    log_path: pathlib.Path,
    now: datetime.datetime | None = None,
) -> bool:
    policy = _load_policy(policy_path)
    if not _telemetry_enabled(policy):
        return False

    today = _utc_today(now)
    if _last_sent_date(policy) == today:
        return False

    payload = build_payload(
        policy=policy,
        reports_db_path=reports_db_path,
        approval_db_path=approval_db_path,
        log_path=log_path,
        now=now,
    )
    endpoint = _telemetry_endpoint(policy)

    def _worker() -> None:
        status = _send_once(endpoint, payload, REQUEST_TIMEOUT_SECONDS)
        if status == 204:
            _update_last_sent_date(policy_path, today)

    threading.Thread(target=_worker, daemon=True, name="airg-telemetry").start()
    return True
