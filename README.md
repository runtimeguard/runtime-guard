# ai-runtime-guard

> Your agent can say anything. It can only do what policy allows.

AI agents with filesystem and shell access can delete files, leak credentials, or execute destructive commands, often without the user realizing it until it is too late.

`ai-runtime-guard` is an MCP server that sits between your AI agent and your system, enforcing a policy layer before any file or shell action takes effect. No retraining, no prompt engineering, no changes to your agent workflow.

[![runtime-guard MCP server](https://glama.ai/mcp/servers/runtimeguard/runtime-guard/badges/score.svg)](https://glama.ai/mcp/servers/runtimeguard/runtime-guard)

## What It Does
1. **Blocks dangerous operations**: `rm -rf`, sensitive file access, privilege escalation, and other risky actions are denied before execution.
2. **Gates risky commands behind human approval**: configurable commands require explicit operator sign-off via the local GUI/API before execution.
3. **Controls network behavior**: supports command-level network policy with monitor/enforce behavior, allowlists, and denylists.
4. **Enforces workspace boundaries**: file and command operations are evaluated against `AIRG_WORKSPACE` and path policy.
5. **Backs up before it acts**: destructive and overwrite operations create recoverable backups automatically.
6. **Provides robust logging and reporting**: all allowed/blocked actions are written to `activity.log` and indexed into `reports.db`.
7. **Supports per-agent policy overlays**: applies policy overrides keyed by `AIRG_AGENT_ID`.
8. **Script Sentinel**: flags dangerous command patterns at write time in executable context (for example scripts) to preserve policy intent and reduce bypass attempts.
9. **Universal AI agent Policy Orchestrator**: comprehensive GUI-driven security posture enforcement for AI agents (hooks, sandboxing, native tool restrictions, and policy mirroring depending on agent support).

## Current State
1. Policy management is available in the local GUI (commands, paths, extensions, network, script sentinel, advanced policy).
2. Agent management is available in the GUI (`Settings -> Agents`), including profile-based MCP configuration and posture checks.
3. Full runtime visibility is available through `activity.log` and reports views backed by `reports.db`.
4. Stable release notes are tracked in `CHANGELOG.md`, with in-progress work in `docs/CHANGELOG_DEV.md`.

## Who It Is For
Developers and power users running AI agents (Claude Code, Claude Desktop, Cursor, Codex, and other MCP-compatible clients) who want guardrails on what an agent can do to their system.

## Known Boundary
1. AIRG enforces policy only for actions that pass through AIRG MCP tools.
2. Native client tools outside MCP (for example Claude Code `Glob`, `Read`, `Write`, `Edit`, `Bash`) are outside AIRG enforcement and can bypass policy.
3. Clients differ in what hardening controls they support (hooks, native tool restrictions, sandbox settings).
4. Enforcement options and posture controls are available in AIRG GUI under `Settings -> Agents`.
5. For strict enforcement, configure the client to route operations through AIRG MCP tools and disable risky native tools where supported.

## Design Scope
1. AIRG is designed to reduce accidental damage from AI agent mistakes and policy-evasion patterns.
2. AIRG is not positioned as a full malicious-actor containment platform.
3. Core controls:
   - block high-risk destructive and exfiltration actions
   - enforce workspace and path boundaries
   - require explicit approval for selected risky actions
   - auto-backup destructive and overwrite targets
   - preserve policy intent through Script Sentinel for indirect execution attempts
   - maintain an auditable trail of agent and operator actions

## Requirements
Python:
1. Required: Python `>=3.10` (project package metadata enforces this).
2. Recommended on macOS: Python `3.12+` (Homebrew or python.org install).
3. macOS system Python `3.9` is often too old and may fail dependency install.

## Official Support
Platforms:
1. macOS
2. Linux

Supported agent integrations on both platforms:
1. Claude Code
2. Claude Desktop
3. Codex
4. Cursor

Notes:
1. Enforcement depth is agent-dependent (for example hooks/sandbox controls differ by client).
2. AIRG MCP policy enforcement remains the primary universal layer across supported clients.
3. Cursor MCP config is supported in both `project` (`<workspace>/.cursor/mcp.json`) and `global` (`~/.cursor/mcp.json`) scope.

## How To Run
Environment isolation recommendation:
1. Use one of:
   - Python virtual environment (`venv`)
   - `pipx` isolated app install
2. Avoid system-wide `pip install` without isolation to reduce dependency conflicts.

Recommended quick start (`pipx`, isolated global CLI):
1. `pipx install ai-runtime-guard`
2. If prompted: `pipx ensurepath` and then open a new terminal session
3. `airg-setup` (guided, recommended: select/create workspace during setup; includes telemetry opt-in prompt, default Yes)
4. `airg-doctor`

Alternative quick start (`venv`):
1. `python3 -m venv .venv-airg && source .venv-airg/bin/activate`
2. `python -m pip install --upgrade pip`
3. `python -m pip install ai-runtime-guard`
4. `airg-setup` (guided, recommended: select/create workspace during setup; includes telemetry opt-in prompt, default Yes)
5. `airg-doctor`
6. Open GUI `Settings -> Agents`, add agents manually, and apply MCP config/hardening from there.

Source-clone path:
1. `git clone --branch main https://github.com/runtimeguard/runtime-guard.git`
2. `cd runtime-guard`
3. `python3 -m venv .venv-airg && source .venv-airg/bin/activate`
4. `python -m pip install --upgrade pip`
5. `python -m pip install .`
6. `airg-setup`
7. `airg-doctor`

Unattended automation-only setup (CI/non-interactive):
1. `airg-setup --defaults --yes --workspace /absolute/path/to/workspace`

For source-clone setup, TestPyPI flow, and service details, see [`docs/INSTALL.md`](docs/INSTALL.md).

## Web GUI
AIRG includes a local web control plane for:
1. policy editing and per-agent overrides
2. approval management
3. agent profile/config management (`Settings -> Agents`)
4. reports dashboard and event log
5. telemetry toggle and payload preview (`Policy -> Advanced -> Anonymous telemetry`)

Open:
1. `http://127.0.0.1:5001` (service started by `airg-setup`)

Manual lifecycle commands:
```bash
airg-service install --workspace /absolute/path/to/airg-workspace
airg-service start
airg-service status
airg-service stop
airg-service restart
airg-service uninstall
```

## Privacy
1. AIRG telemetry is optional and can be disabled in `Policy -> Advanced -> Anonymous telemetry`.
2. Setup/update prompts default to enable telemetry, and you can opt out at any time.
3. Telemetry endpoint is configurable in policy (`telemetry.endpoint`) and can be set to a custom endpoint.
4. Telemetry details and payload example: [`docs/telemetry.md`](docs/telemetry.md).

## AIRG_WORKSPACE (Important)
`AIRG_WORKSPACE` is the default project root for guarded agent operations.

How it works:
1. `execute_command` runs from `AIRG_WORKSPACE`.
2. File tools (`read_file`, `write_file`, `edit_file`, `delete_file`, `list_directory`) enforce path/workspace policy relative to that root.
3. Traversal attempts outside this root are blocked by policy checks.

Workspace model:
1. You can use an existing folder as workspace.
2. Multiple workspaces are supported.
3. You can run multiple agents against one workspace or one agent per workspace.
4. Each agent profile should set workspace explicitly in generated MCP config.

## AIRG_AGENT_ID (Important)
`AIRG_AGENT_ID` is the runtime identity key used for:
1. activity and report attribution (`activity.log`, `reports.db`)
2. per-agent policy override resolution (`policy.agent_overrides`)
3. posture and hardening state in `Settings -> Agents`

## Multi-Agent Setup (STDIO Limitation)
AIRG currently runs as a local STDIO MCP server. Under current MCP client behavior, identity is usually tied to profile config, not per-instance runtime auth.

Practical implication:
1. The effective identity is typically the configured combination of `AIRG_AGENT_ID` + `AIRG_WORKSPACE`.
2. Multiple instances of the same client type in the same workspace commonly share the same MCP registration and therefore the same `AIRG_AGENT_ID`.
3. This is a known limitation for local STDIO deployments.
4. Per-instance identity and stronger separation require an authenticated HTTP/SSE model (future architecture direction).

## Deployment Model FAQ
1. Do I need to run `source scripts/setup_runtime_env.sh`?
   - With packaged flow (`airg-setup`), no. Setup initializes secure runtime paths and service env.
   - For direct source/manual runs, it is still useful.
2. What folders are involved?
   - Install folder: where package/repo code lives.
   - Runtime state folder: `policy.json`, approvals DB/HMAC key, logs, reports DB, backups.
   - Workspace folder (`AIRG_WORKSPACE`): where guarded agent operations run.
3. Does setup auto-create an agent profile?
   - No. Agents are added manually in GUI `Settings -> Agents`.
4. Does AIRG hot-reload policy?
   - Yes, policy changes are picked up on subsequent tool calls.
5. Is restart still needed sometimes?
   - MCP clients can cache process/env state. Restart the client or AIRG service after major config changes if behavior looks stale.
