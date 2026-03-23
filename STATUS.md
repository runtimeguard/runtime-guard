# STATUS

Last updated: 2026-03-23 (v2.0.0)

## Branches
1. Active integration branch: `dev`
2. Stable release branch: `main`

## Release state
1. Current release target: `v2.0.0`
2. Package version in source: `2.0.0`
3. Stable release notes: `CHANGELOG.md`
4. Development history: `docs/CHANGELOG_DEV.md`

## Runtime snapshot
1. AIRG is a local STDIO MCP policy enforcement server with Web GUI included.
2. Active policy tiers are `blocked`, `requires_confirmation`, and `allowed`.
3. Script Sentinel is active in runtime policy model (write-time detection, execute-time enforcement continuity).
4. Per-agent overrides are supported through `policy.agent_overrides` keyed by `AIRG_AGENT_ID`.
5. Reports ingest from `activity.log` into `reports.db` for dashboard and log views.
6. Setup flow is `airg-setup` plus manual agent onboarding in `Settings -> Agents`.

## Known boundary
1. AIRG enforces only operations routed through AIRG MCP tools.
2. Native client tools outside MCP can bypass AIRG policy unless client hardening is applied.
3. In STDIO deployments, identity separation is effectively the MCP profile tuple (`AIRG_AGENT_ID` + workspace), not per-instance authenticated identity.

## Next focus areas
1. Agent hardening quality and posture signal accuracy across supported clients.
2. Continued documentation/runtime parity as v2.x features evolve.
3. Future research: authenticated HTTP/SSE transport for stronger per-instance identity.
