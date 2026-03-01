# Linux Validation v2 (Fresh Install Retest)

## Scope
This validation was run on a clean Linux user profile with no prior AIRG install, no runtime files, and no Claude config files.

Goals:
1. Validate end-to-end install from `dev`.
2. Validate MCP connectivity from Claude Code.
3. Validate UI startup and approval flow.
4. Capture all friction points with exact symptoms.

## Environment
1. User: `liviu` (non-root shell).
2. OS: Ubuntu VM.
3. Python: `3.12.3`.
4. Node: `18.19.1`.
5. npm: `9.2.0`.
6. Git: `2.43.0`.
7. Branch under test: `dev` at commit `be89fea`.

## Reset and Cleanup Performed
All previous state removed before reinstall:
1. AIRG install folder.
2. AIRG runtime folders:
   - `~/.config/ai-runtime-guard`
   - `~/.local/state/ai-runtime-guard`
3. Claude config files:
   - `~/.claude`
   - `~/.claude.json`
   - project `.claude` files under workspace.
4. Workspace recreated as user-owned directory.

## Installation Steps Executed
1. Clone `dev`:
```bash
cd /home/liviu
git clone --branch dev https://github.com/jimmyracheta/ai-runtime-guard.git
cd /home/liviu/ai-runtime-guard
```
2. Create and activate venv:
```bash
python3 -m venv venv
source venv/bin/activate
```
3. Install package:
```bash
pip install --upgrade pip
pip install .
```
4. Initialize runtime:
```bash
airg-setup --defaults --yes
```
5. Confirm runtime files:
```bash
ls -l /home/liviu/.config/ai-runtime-guard/policy.json
ls -l /home/liviu/.local/state/ai-runtime-guard/approvals.db
ls -l /home/liviu/.local/state/ai-runtime-guard/approvals.db.hmac.key
```
6. Configure Claude MCP (project-scoped):
```bash
cd /home/liviu/airg-workspace
claude mcp add ai-runtime-guard \
  --env AIRG_WORKSPACE=/home/liviu/airg-workspace \
  --env AIRG_POLICY_PATH=/home/liviu/.config/ai-runtime-guard/policy.json \
  --env AIRG_APPROVAL_DB_PATH=/home/liviu/.local/state/ai-runtime-guard/approvals.db \
  --env AIRG_APPROVAL_HMAC_KEY_PATH=/home/liviu/.local/state/ai-runtime-guard/approvals.db.hmac.key \
  -- /home/liviu/ai-runtime-guard/venv/bin/airg-server
```
7. Validate MCP server health:
```bash
claude mcp list
```

## Functional Tests Executed
1. MCP tools basic operations passed:
   - `list_directory`
   - `write_file`
   - `read_file`
   - `execute_command ls -la`
2. Added `rm` to `requires_confirmation.commands`.
3. Approval flow retested through GUI.
4. Post-fix approval flow succeeded for:
   - `rm 10.tmp`
   - `rm smoke1.txt`

## Friction Points Found
### 1) Log path defaults to installed package directory
Observed:
1. `activity.log` not found in repo root.
2. Runtime wrote log to:
   - `/home/liviu/ai-runtime-guard/venv/lib/python3.12/site-packages/activity.log`

Impact:
1. Log location is non-obvious.
2. Troubleshooting and docs become misleading.
3. Install path ownership can break logging.

### 2) UI silently falls back to legacy assets
Observed:
1. If `ui_v3/dist` is missing, `airg-ui` serves legacy static UI (`/app.js`, `/styles.css`).
2. Resulting page appears partially broken relative to expected v3 behavior.

Impact:
1. Users think UI is broken.
2. Policy edits can target wrong experience.
3. Debugging is confusing without clear error.

### 3) `AIRG_UI_DIST_PATH` shell-scope confusion
Observed:
1. Exporting vars in one shell does not apply to other shells.
2. Starting `airg-ui` from a shell without exports ignores intended paths.

Impact:
1. Non-deterministic startup behavior.
2. Easy to accidentally run wrong policy/UI path combination.

### 4) Empty HMAC key file from setup breaks approval handshake
Observed:
1. `approvals.db.hmac.key` was created as zero-byte file.
2. Logs showed:
   - `approval_hmac_key_fallback`
   - `approval_store_tamper_detected`
3. Approval entered infinite token loop after GUI approval.

Impact:
1. Human approval flow can fail even when UI and MCP are configured correctly.
2. Security logic reports tamper mismatch due to ephemeral fallback keys.

### 5) Claude config location and scope confusion
Observed:
1. Claude uses project-scoped config with file reporting that can be non-intuitive (`~/.claude.json [project: ...]`).
2. MCP may appear missing in new sessions if scope assumptions are wrong.

Impact:
1. Users assume AIRG is broken when issue is config scope.
2. Inconsistent behavior across sessions/projects.

### 6) Command-tier vs tool-surface expectation mismatch
Observed:
1. `requires_confirmation.commands = ["rm"]` gates shell `execute_command rm ...`.
2. It does not automatically gate `delete_file`.

Impact:
1. Users expect all delete behavior to be uniformly gated.
2. Current behavior is correct by implementation but under-documented.

## Temporary Workarounds Used During Validation
1. Built v3 manually:
```bash
cd /home/liviu/ai-runtime-guard/ui_v3
npm install
npm run build
```
2. Forced v3 path:
```bash
export AIRG_UI_DIST_PATH=/home/liviu/ai-runtime-guard/ui_v3/dist
```
3. Stabilized approval signatures by setting shared secret in both UI and Claude launch shells:
```bash
export AIRG_APPROVAL_HMAC_SECRET='airg-test-shared-secret-20260228'
```

## Proposed Actions to Remove Friction
### A) Runtime path hardening
1. Move default `activity.log` to user state path:
   - Linux: `~/.local/state/ai-runtime-guard/activity.log`
   - macOS: `~/Library/Application Support/ai-runtime-guard/activity.log`
2. Keep explicit override support via env.

### B) Setup key generation fix
1. Update setup to generate non-empty HMAC key file at init time.
2. On startup, if key file exists but is empty, auto-regenerate and log one warning.

### C) UI startup behavior fix
1. Prefer v3 build first.
2. If v3 build is missing, return clear API/HTML error with actionable command.
3. Remove silent legacy fallback in standard flow.

### D) Unified env validation
1. Extend `airg-doctor` to print all resolved paths and whether each exists.
2. Add explicit warning when UI serves legacy assets.
3. Add warning when MCP env and UI env are inconsistent.

### E) Documentation improvements
1. Add Linux quickstart with one-shell and two-shell patterns.
2. Add section on Claude project-scoped MCP behavior and verification commands.
3. Add section clarifying:
   - `requires_confirmation.commands` applies to shell commands.
   - file tools have separate policy surfaces.

### F) Optional UX improvement
1. Add `airg-ui --with-runtime-env` mode that loads `AIRG_*` paths from initialized defaults automatically.
2. Reduce manual export burden for non-expert users.

## Retest Checklist (after fixes)
1. Fresh Linux user, no state.
2. `airg-setup --defaults --yes`.
3. `airg-doctor` shows runtime paths and v3-ready status.
4. `airg-ui` loads v3 without manual `AIRG_UI_DIST_PATH` override.
5. Approval flow works without manual `AIRG_APPROVAL_HMAC_SECRET`.
6. `activity.log` writes to user state path, not site-packages.
