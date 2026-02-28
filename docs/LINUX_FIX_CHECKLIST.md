# Linux Friction Fix Checklist

This checklist converts the findings from Linux Validation v2 into implementation tasks.

## Phase 1 - Runtime Path and Key Reliability
1. Move default `activity.log` to runtime state path (Linux and macOS), not repo/site-packages.
2. Preserve explicit env override support for log path.
3. Update setup/init flow to create non-empty HMAC key material.
4. Add startup self-heal: if HMAC key file exists but is empty, regenerate once and emit warning event.
5. Add test coverage for:
   - empty-key file regeneration
   - stable approval signatures across UI and MCP processes
   - expected log-path resolution in packaged installs

## Phase 2 - UI Startup Determinism
1. Make v3 UI build the primary and required web UI target.
2. Remove silent fallback to legacy UI in normal flow.
3. If v3 build is missing, return explicit actionable error:
   - what is missing
   - exact build command
   - how to set `AIRG_UI_DIST_PATH`
4. Add test coverage for UI dist resolution order and missing-dist behavior.

## Phase 3 - Doctor and Diagnostics
1. Extend `airg-doctor` output with resolved runtime paths:
   - workspace
   - policy path
   - approval DB path
   - approval HMAC key path
   - log path
   - UI dist path
2. Add consistency checks:
   - warn when UI is serving legacy assets
   - warn when key file is empty
   - warn when policy path differs from expected runtime initialization path
3. Add clear pass/fail markers for each check.

## Phase 4 - Documentation Clarity
1. Update Linux install docs with one-shell and two-shell patterns.
2. Add explicit section on shell env scope (exports only affect current shell/process tree).
3. Add explicit Claude project-scoped MCP behavior and verification:
   - `claude mcp list`
   - in-session MCP check
4. Clarify command-tier vs tool-surface behavior:
   - `requires_confirmation.commands` affects shell commands
   - file tools are governed separately
5. Add troubleshooting section:
   - missing MCP in session
   - UI serving legacy assets
   - approval loop due to key/signature mismatch

## Phase 5 - Optional UX Simplification
1. Add `airg-ui --with-runtime-env` option.
2. Behavior:
   - load resolved runtime defaults from initialized AIRG paths
   - set missing `AIRG_*` vars automatically for UI process
   - print resolved paths at startup for operator verification
3. Keep current explicit-env behavior unchanged when flags/env are already provided.
4. Add tests for flag behavior and precedence rules.

## Retest Gate (Linux)
1. Fresh user with no prior AIRG artifacts.
2. Install from `dev`.
3. `airg-setup --quickstart --yes`.
4. `airg-doctor` shows expected paths and UI readiness.
5. `airg-ui` launches v3 without accidental legacy fallback.
6. Approval loop test passes without manual HMAC secret.
7. `activity.log` writes to runtime state location.
