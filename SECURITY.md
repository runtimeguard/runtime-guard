# Security Policy

## Supported Versions
Security fixes are prioritized for:
1. The latest stable release on `main`
2. The active integration branch (`dev`) when a fix has not yet been released

Older tags may not receive patches.

## Reporting a Vulnerability
Report vulnerabilities privately.

Preferred contact:
1. Open a private security advisory in GitHub
2. Or email the maintainer: `avram.liviu@gmail.com`
3. Or use Discord: `https://discord.gg/NJECm4fY`

Include:
1. Affected version or commit
2. AI client and MCP setup details
3. Reproduction steps
4. Expected vs actual behavior
5. Impact assessment
6. Relevant policy snippets with secrets removed

Do not open public issues for exploitable vulnerabilities before triage.

## Response Process
1. Acknowledgement target: within 3 business days
2. Initial triage target: within 7 business days
3. Remediation timeline depends on severity and exploitability
4. Coordinated disclosure is preferred after a fix is available

## Scope and Threat Model
`ai-runtime-guard` enforces policy on AIRG MCP tool calls.

In scope:
1. Policy bypasses for blocked or confirmation-gated actions through AIRG MCP tools
2. Approval workflow bypasses
3. Workspace/path boundary bypasses in AIRG tools
4. Runtime-state tampering that weakens approvals, logs, reports, backups, or restore safeguards
5. Script Sentinel bypasses within its declared coverage boundary

Out of scope by design:
1. Direct native client tools outside AIRG MCP routing
2. Host compromise unrelated to AIRG runtime and policy path
3. Adversarial intent classification (AIRG is policy-enforcement-first, not malware detection)

## Severity Guidance
High severity examples:
1. Executing blocked destructive commands through AIRG MCP tools
2. Approval token bypass or self-approval path
3. Unauthorized access to protected runtime files through guarded tools
4. Cross-workspace access that violates configured policy boundaries

Medium severity examples:
1. Incorrect enforcement order causing unintended allow outcomes
2. Missing or wrong attribution fields that materially degrade incident investigation

Low severity examples:
1. Cosmetic logging inconsistencies without enforcement impact
2. Documentation mismatches without exploitable behavior

## Operator Hardening Recommendations
1. Keep runtime state paths outside the agent workspace
2. Keep approval actions out-of-band and operator controlled
3. Disable or restrict native client tools that bypass MCP where supported
4. Keep blocked command and sensitive path policies strict for destructive operations
5. Keep AIRG and client tooling updated

## Safe Harbor
Good-faith security research and responsible disclosure are welcome.
Do not exfiltrate real secrets, do not disrupt production systems, and do not run destructive testing outside controlled environments.
