# Telemetry

AIRG supports optional anonymous telemetry to help improve product quality and prioritization.

## What Is Collected
When enabled, AIRG sends one aggregate payload per UTC day to:

- `https://telemetry.runtime-guard.ai/v1/telemetry`

Example payload:

```json
{
  "airg_version": "2.1.1",
  "platform": "macos",
  "python_version": "3.12.3",
  "install_method": "unknown",
  "agents_bucket": "1",
  "agent_types": ["cursor"],
  "events_bucket": "11-50",
  "blocked_bucket": "2-5",
  "approvals_bucket": "0",
  "sentinel_enabled": true,
  "sentinel_flagged_bucket": "1",
  "sentinel_blocked_bucket": "0",
  "period_days": 1
}
```

## What Is Not Collected

- No command text.
- No file contents or file paths.
- No prompt/completion text.
- No usernames, emails, hostnames, or machine identifiers.
- No install ID or persistent telemetry identifier.
- No high-resolution timestamps (daily aggregate only).

## Opt In / Opt Out

- Setup prompt asks whether to enable telemetry (default is Yes).
- You can toggle telemetry in `Policy -> Advanced -> Anonymous telemetry`.
- Global hard opt-out: set `AIRG_TELEMETRY_OPTOUT=1`.

## Payload Preview

- In `Policy -> Advanced -> Anonymous telemetry`, click `See Payload`.
- AIRG shows the exact JSON shape/value that would be sent.

## Endpoint And Delivery

- Default endpoint: `https://telemetry.runtime-guard.ai/v1/telemetry`
- Method: `POST` JSON (`Content-Type: application/json`)
- Timeout: 5 seconds total
- Retries: none
- Failures are silently dropped (no queue/persist)
- Success is HTTP `204 No Content`

To point to a different endpoint, set either:

- `policy.telemetry.endpoint`
- or env var `AIRG_TELEMETRY_ENDPOINT` (takes precedence)

## Worker

Telemetry ingestion/validation worker reference:

- [runtime-guard telemetry worker](https://github.com/runtimeguard/runtime-guard-telemetry-worker)
