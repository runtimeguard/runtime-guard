# STATUS

Last updated: 2026-02-24 (state ownership + policy logging/simulation refactor follow-up)

## Current branch
- `refactor` (tracking `origin/refactor`)

## What was just changed
- Split monolithic `server.py` into focused modules:
  - `config.py`, `models.py`, `audit.py`, `policy_engine.py`, `approvals.py`, `budget.py`, `backup.py`, `executor.py`
  - `tools/command_tools.py`, `tools/file_tools.py`, `tools/restore_tools.py`
- Kept `server.py` as a thin MCP entrypoint wiring tool registrations.
- Added a refactor-compatible in-repo test suite under `tests/`:
  - `tests/test_attacker_suite.py`
  - `tests/test_retry_clamp.py`
  - `tests/test_helpers.py`
- Updated test execution to `python3 -m unittest discover -s tests -p 'test_*.py'`.
- Moved mutable runtime state ownership out of `config.py` into owning modules:
  - approvals/session state in `approvals.py`
  - retry counters in `policy_engine.py`
  - cumulative budget state in `budget.py`
- Updated policy conflict logging to use shared audit schema construction.
- Removed duplicate blast-radius simulation in `execute_command` by reusing one computed simulation result across policy and budget checks.

## Current known issues
- `execute_command` still uses `shell=True` for compatibility; this remains the largest residual command-parsing risk surface.
- Network policy currently focuses on domain-level command checks; payload-size and protocol-depth enforcement are not yet comprehensive.
- Backup target detection for shell commands remains heuristic (`PATH_TOKEN_RE` + existing-path checks) and can miss some shell expansion edge cases.
- Multiple modules still use deprecated UTC datetime helpers (`utcnow` / `utcfromtimestamp`).
- Runtime constants are still imported by multiple modules at load time (`WORKSPACE_ROOT`, `MAX_RETRIES`, `LOG_PATH`, `BACKUP_DIR`), so dynamic runtime reconfiguration remains non-centralized and requires careful patching in tests.

## Core use cases (from README; do not edit without explicit product decision)
1. Block destructive commands and sensitive path/extension access.
2. Simulation-gate wildcard destructive operations and enforce blast-radius thresholds.
3. Require explicit confirmation handshake for configured risky commands.
4. Create backups before destructive/overwrite actions and validate recovery.

## Recommended next steps and TODO backlog (merged, deduplicated)

### Policy/code audit follow-ups
1. Complete policy-to-code parity (remaining keys still unused/partial as of current modular code): `allowed.max_files_per_operation`, `network.max_payload_size_kb`, `audit.log_level`, cumulative budget `counting.mode`, `reset.mode`, `reset_on_server_restart`, `audit.log_budget_state`, `audit.fields`, `on_exceed.decision_tier`, and override metadata fields (`require_confirmation_tool`, `token_ttl_seconds`, `audit_reason_required`, `allowed_roles`). For each key, either wire enforcement/behavior in the relevant runtime modules (`config.py`, `policy_engine.py`, `budget.py`, `tools/*`, `audit.py`) or remove it from policy schema.
2. Unify backup policy behavior across tools: enforce `audit.backup_enabled` consistently for `write_file` and `delete_file` (not only `execute_command`), and keep backup access controls consistent between file tools and `execute_command`.
3. Harden command execution model: reduce dependence on `shell=True` with structured execution where feasible, and isolate a tightly-scoped legacy shell mode for cases that need pipes/redirection.
4. Strengthen network control depth: keep domain controls and add payload/protocol-aware enforcement so `network.max_payload_size_kb` and related policy fields become meaningful.
5. Improve backup mutation detection: replace or augment regex path extraction with parser-aware target resolution for shell expansions (`find -exec`, `xargs`, loops, substitutions).
6. Improve restore ergonomics and safety: add restore conflict strategies (`overwrite/skip/fail`) and clearer per-file restore result reporting.
7. Replace deprecated UTC datetime calls with timezone-aware UTC (`datetime.now(datetime.UTC)` / `datetime.fromtimestamp(..., datetime.UTC)`).
8. Add CI checks for policy parity regressions and run `python3 -m unittest discover -s tests -p 'test_*.py'` as a required check.
9. Strengthen release hygiene: dependency vulnerability checks (`pip-audit`), reproducible constraints/lock workflow, and branch protection (`dev` -> `main` with required checks).
10. Formalize the two-layer test strategy: keep `tests/` for deterministic unit/security regressions and `tests.md` prompts for MCP-agent integration validation, with a documented minimum pre-merge gate for both layers.

### Command policy rollout items (Unix/macOS now, Linux-ready)
11. Validate expanded command sets against real agent workflows to tune false-positive rate (especially for `find`, `xargs`, `sed`, `perl` in simulation tier).
12. Add focused integration tests for multi-command shell constructs (`find -exec`, `xargs`, loops, substitutions) that are now represented in policy but only partially modeled by current simulation logic.
