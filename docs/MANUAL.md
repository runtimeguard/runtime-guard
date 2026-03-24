# AI Runtime Guard Manual

This manual documents current runtime behavior for v2.0.0.

## 1. Runtime Surface
AIRG MCP tools:
1. `server_info`
2. `execute_command`
3. `read_file`
4. `write_file`
5. `edit_file`
6. `delete_file`
7. `list_directory`
8. `restore_backup`

Policy is enforced before side effects. Events are written to `activity.log` and ingested into `reports.db`.

## 2. Active Policy Tiers
Tier precedence for command policy:
1. `blocked`
2. `requires_confirmation`
3. `allowed`

Notes:
1. Legacy `requires_simulation` is no longer an active runtime tier.
2. Global cumulative budget enforcement is removed from active runtime logic.

## 3. Command Enforcement Flow (`execute_command`)
Order of checks:
1. Reject unsafe control characters (newline/carriage-return/NUL) in command payload.
2. Protect backup storage paths from direct shell targeting.
3. Apply network policy (`off` | `monitor` | `enforce`).
4. Apply shell workspace containment policy (`off` | `monitor` | `enforce`).
5. Evaluate command tier policy (`blocked` / `requires_confirmation` / `allowed`).
6. Apply Script Sentinel execute-time checks.
7. If allowed, optionally back up destructive targets and execute command.
8. Log decision and execution telemetry.

Retry behavior:
1. Blocked non-confirmation decisions use server-side retry tracking.
2. Confirmation-tier decisions do not consume that retry bucket.

## 4. Script Sentinel
Purpose: preserve policy intent across indirect execution patterns.

Model:
1. Flag at write time (`write_file` and `edit_file`): scan content against blocked and approval-gated policy patterns.
2. Check at execute time (`execute_command`): detect script invocation targets and enforce decision continuity.

Modes:
1. `match_original`: keeps original pattern tier (`blocked` or `requires_confirmation`).
2. `block`: any hit blocks execution.
3. `requires_confirmation`: any hit requires approval.

Scan modes:
1. `exec_context` (default): executable-context signatures only.
2. `exec_context_plus_mentions`: includes mention-only signatures for audit visibility.

Boundary:
1. Coverage is limited to content written through AIRG file-edit/write tools.
2. This feature targets policy-enforcement evasion patterns, not malicious intent classification.

## 5. File and Path Enforcement
1. Path checks apply to all file tools and enforce workspace boundary plus blocked paths/extensions.
2. `allowed.paths_whitelist` can extend permitted roots.
3. `allowed.max_directory_depth` applies to directory listing depth controls.

## 6. Approvals
1. `requires_confirmation` commands return tokenized block responses.
2. Approval is out-of-band through GUI/API.
3. Approved command grants are session + command scoped.
4. Approval state is persisted in `approvals.db` with HMAC-backed safeguards.

## 7. Backup and Restore
1. Backups are created automatically before destructive or overwrite operations when enabled.
2. Backup retention and version count are policy controlled.
3. Restore supports dry-run token workflow and optional apply confirmation TTL.
4. Backup storage can be protected from direct agent tools by policy.

## 8. Reports and Audit
1. `activity.log` is the canonical event stream.
2. `reports.db` is an indexed analytics store built from `activity.log`.
3. Reports retention and DB-size pruning are configurable.
4. Hook events (for supported clients) are written into the same `activity.log` stream.

## 9. Agent Profiles and Posture
`Settings -> Agents` provides:
1. Agent profiles and MCP config generation/apply/remove.
2. Security posture signals by agent type.
3. Enforcement controls (agent-dependent): MCP presence, hooks/rules guidance, native tool restrictions, sandbox posture.

Identity model:
1. Per-agent policy overlays resolve by `AIRG_AGENT_ID`.
2. In STDIO deployments, practical separation is usually the tuple (`AIRG_AGENT_ID`, workspace profile).

## 10. Known Boundaries
1. AIRG enforces only AIRG MCP tool calls.
2. Native client tools outside MCP can bypass AIRG unless separately restricted in the client.
3. Client capabilities differ; some hardening controls are unavailable on some agents.
