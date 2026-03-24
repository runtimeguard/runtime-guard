# Installation Guide

This guide covers the standard AIRG install: runtime + Web GUI service.

## Requirements
1. Python `>=3.10` (recommended `3.12+`).
2. Git.
3. Node.js 18+ only if you are actively rebuilding frontend assets in development.

## Runtime Model
1. Install folder: package/repo location.
2. Workspace (`AIRG_WORKSPACE`): where guarded agent operations run.
3. Runtime state folder: policy, approvals DB/HMAC key, activity log, reports DB, backups.

Default runtime state roots:
1. macOS: `~/Library/Application Support/ai-runtime-guard/`
2. Linux config: `${XDG_CONFIG_HOME:-~/.config}/ai-runtime-guard/`
3. Linux state: `${XDG_STATE_HOME:-~/.local/state}/ai-runtime-guard/`

## Quick Start (package install)
```bash
python3 -m venv .venv-airg
source .venv-airg/bin/activate
python -m pip install --upgrade pip
python -m pip install ai-runtime-guard
airg-setup
airg-doctor
```

Then open `http://127.0.0.1:5001` and add agents from `Settings -> Agents`.
Guided setup asks for workspace and creates it if missing.

## Source Install
```bash
git clone --branch main https://github.com/runtimeguard/runtime-guard.git
cd ai-runtime-guard
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install .
airg-setup
airg-doctor
```

For unattended automation/CI only:
```bash
airg-setup --defaults --yes --workspace /absolute/path/to/workspace
```

## Service Commands
```bash
airg-service install --workspace /absolute/path/to/airg-workspace
airg-service start
airg-service status
airg-service stop
airg-service restart
airg-service uninstall
```

## Policy Lifecycle
1. Edit policy in GUI and click Apply.
2. Runtime hot-reloads policy on subsequent tool calls.
3. If client behavior looks stale, restart the AI client (and service if needed).

## Troubleshooting
1. AIRG not detected by client:
   - ensure MCP command path is absolute or resolvable in client PATH.
   - verify with `airg-doctor`.
2. GUI does not reflect frontend changes:
   - rebuild frontend only if source changed: `cd ui_v3 && npm install && npm run build`.
3. Repeated approval prompts:
   - verify approvals DB and HMAC key paths/permissions via `airg-doctor`.
4. Wrong policy file being edited:
   - runtime reads `AIRG_POLICY_PATH` if set, otherwise user runtime config path.
