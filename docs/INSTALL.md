# Installation Guide

This guide covers the standard AIRG install: runtime + Web GUI service.

## Requirements
1. Python `>=3.10` (recommended `3.12+`).
2. Git (only for source-clone install).
3. Node.js 18+ only if you are actively rebuilding frontend assets in development.
4. Recommended install isolation: `venv` or `pipx`.

## Supported platforms and agents
Platforms officially supported:
1. macOS
2. Linux

Agents supported on both platforms:
1. Claude Code
2. Claude Desktop
3. Codex
4. Cursor

Note: security posture depth is client-dependent; AIRG MCP enforcement is the universal base layer.

## Isolation options (recommended)
Choose one:
1. `pipx` (recommended for operators): install AIRG in an isolated app environment with global CLI shims.
2. `venv` (recommended for development/source workflows): install AIRG in a project/user virtual environment.

`pipx` may not be installed by default on Linux. Example install options:
1. Ubuntu/Debian: `sudo apt install pipx` (or `python3 -m pip install --user pipx`)
2. Fedora/RHEL: `sudo dnf install pipx`
3. macOS (Homebrew): `brew install pipx`

After running `pipx ensurepath`, open a new terminal (or restart your shell session) before using `airg*` commands.

Why isolation matters:
1. Prevents conflicts with system Python and unrelated packages.
2. Makes upgrades/uninstalls cleaner and predictable.
3. Reduces permission/sudo friction on Linux and macOS.

## Runtime Model
1. Install folder: package/repo location.
2. Workspace (`AIRG_WORKSPACE`): where guarded agent operations run.
3. Runtime state folder: policy, approvals DB/HMAC key, activity log, reports DB, backups.

Default runtime state roots:
1. macOS: `~/Library/Application Support/ai-runtime-guard/`
2. Linux config: `${XDG_CONFIG_HOME:-~/.config}/ai-runtime-guard/`
3. Linux state: `${XDG_STATE_HOME:-~/.local/state}/ai-runtime-guard/`

## Quick Start (package install via pipx)
```bash
pipx install ai-runtime-guard
pipx ensurepath   # run once if needed
# open a new terminal after ensurepath
airg-setup
airg-doctor
```

## Alternative Quick Start (package install via venv)
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
cd runtime-guard
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
2. `airg`/`airg-setup` command not found after `pipx ensurepath`:
   - open a new terminal or re-login so PATH changes are loaded.
   - verify with `which airg`.
3. Agent still points to old AIRG build/location:
   - reapply MCP config from `Settings -> Agents`.
   - verify active server path/version using `server_info` from the MCP client.
4. Multiple AIRG installs caused path confusion:
   - remove old installs and keep one install method per host (`pipx` or one dedicated `venv`).
5. GUI does not reflect frontend changes:
   - rebuild frontend only if source changed: `cd ui_v3 && npm install && npm run build`.
6. Repeated approval prompts:
   - verify approvals DB and HMAC key paths/permissions via `airg-doctor`.
7. Wrong policy file being edited:
   - runtime reads `AIRG_POLICY_PATH` if set, otherwise user runtime config path.
