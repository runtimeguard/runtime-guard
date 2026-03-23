# Architecture

## 1. Overview
`ai-runtime-guard` is a local-first Python MCP server that enforces policy before shell and file actions execute.

Core flow:
1. Client calls AIRG MCP tool.
2. AIRG loads effective policy (base + optional per-agent overlay).
3. AIRG evaluates policy and safety gates.
4. AIRG executes allowed action.
5. AIRG logs event to `activity.log` and reports pipeline ingests into `reports.db`.

## 2. Main Components
1. `src/server.py`: MCP entrypoint and tool registration.
2. `src/config.py`: runtime paths, policy load/normalize, hot-reload.
3. `src/policy_engine.py`: command/path decision logic.
4. `src/tools/command_tools.py`: command gate + execution path.
5. `src/tools/file_tools.py`: file operations + backup + sentinel scans.
6. `src/tools/restore_tools.py`: restore flow and confirmation tokens.
7. `src/approvals.py`: approval store and token handling.
8. `src/backup.py`: backup creation and retention handling.
9. `src/script_sentinel.py`: write-time tagging and execute-time checks.
10. `src/reports.py`: log ingestion and analytics storage.
11. `src/agent_configurator.py` + `src/agent_posture.py`: agent profile/config/posture orchestration.

## 3. Effective Policy Model
1. Base policy loads from runtime `policy.json`.
2. If `policy.agent_overrides.<AIRG_AGENT_ID>` exists, overlay is deep-merged.
3. Effective policy is normalized and applied in-memory.
4. Policy hot-reloads on file mtime change.

Supported override sections:
1. `blocked`
2. `requires_confirmation`
3. `allowed`
4. `network`
5. `execution`

## 4. Enforcement Model
Active command tiers:
1. `blocked`
2. `requires_confirmation`
3. `allowed`

Additional gates:
1. Network policy.
2. Shell workspace containment.
3. Backup-storage protection.
4. Script Sentinel execute-time decision continuity.

Removed from active runtime enforcement:
1. Simulation tier as a first-class decision stage.
2. Cumulative budget enforcement logic.

## 5. Data and State
Runtime state (user-local by default):
1. `policy.json`
2. `approvals.db`
3. `approvals.db.hmac.key`
4. `activity.log`
5. `reports.db`
6. backup directories

Agent profile state:
1. MCP config registry under runtime state (`mcp-configs/*`).
2. Applied MCP metadata (scope/path/timestamp) for safe cleanup and drift detection.

## 6. Trust Boundary
AIRG enforces only AIRG MCP tool calls.

Implications:
1. Native client tools outside AIRG MCP are outside AIRG enforcement.
2. AIRG hardening guidance/configuration in clients improves coverage but is agent-dependent.
3. In local STDIO mode, instance identity is profile/environment driven, not authenticated per-connection identity.
