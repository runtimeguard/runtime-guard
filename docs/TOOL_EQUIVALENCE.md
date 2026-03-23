# Tool Equivalence and Tradeoffs

This guide explains how AIRG MCP tools compare to common native agent tools, and what changes when native tools are redirected through AIRG hardening.

## Quick mapping
1. `Bash` -> `execute_command`
2. `Write` / `Edit` / `MultiEdit` -> `write_file`
3. `Read` -> `read_file` (Tier 2 policy checks can apply in hook)
4. `Glob` / `Grep` -> native tools remain native, but Tier 2 hook can allow/deny based on AIRG path/extension policy.

## Capability comparison
### `list_directory` vs native `Glob`
1. `list_directory` is single-level and returns metadata (`type`, `size`, `modified`).
2. `Glob` is recursive/pattern-heavy and better for cross-tree discovery.
3. Tradeoff: AIRG gives stronger control and audit context; native `Glob` is broader for discovery workflows.

### `read_file` vs native `Read`
1. For plain text files, behavior is close enough for most workflows.
2. Native `Read` can support richer reader features (offset/limit and some client-specific formats).
3. Tradeoff: AIRG is sufficient for enforcement continuity; native `Read` can be more ergonomic.

### `write_file` vs native `Write`
1. AIRG `write_file` integrates with backup/restore controls and Script Sentinel write-time scanning.
2. Native `Write` has no AIRG backup/audit guarantees.
3. Nuance: backup creation depends on policy and operation type:
   - creating a new file has no prior content to back up,
   - overwrites/backups depend on configured backup behavior.

### `execute_command` vs native `Bash`
1. AIRG `execute_command` is the enforcement path for command policy, confirmation, audit logging, and Script Sentinel execute checks.
2. Native `Bash` is outside MCP unless blocked/redirected by hook + client permissions.

### `delete_file` vs native `rm`
1. AIRG `delete_file` supports recoverability through backup-aware delete handling.
2. Native `rm` is outside AIRG safeguards.

### `restore_backup`
1. No direct native equivalent in AIRG context.
2. AIRG restore is scoped and token-gated to reduce accidental restores.

## Hardening guidance
### Tier 1 (recommended baseline)
1. Redirect/deny native mutation tools:
   - `Bash`, `Write`, `Edit`, `MultiEdit`
2. Goal: preserve policy intent for command and file mutation paths.

### Tier 2 (optional, stronger coverage)
1. Add hook checks for native read/search tools:
   - `Read`, `Glob`, `Grep`
2. Goal: enforce blocked path/extension policy for native discovery/read surfaces.
3. Tradeoff: more hook checks can increase overhead in read/search-heavy sessions.

## Positioning
AIRG is runtime policy-intent continuity, not intent classification:
1. Keep policy outcomes consistent when actions are direct or indirect.
2. Prefer deterministic enforcement + audit over semantic interpretation of agent intent.
