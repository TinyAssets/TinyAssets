---
title: Acceptance Probe Catalog
date: 2026-04-20
author: navigator
status: living doc — add validated probes here; remove if they go stale
---

# Acceptance Probe Catalog

Named, validated probes for testing the full System → Chatbot → User chain.
Each entry records the exact prompt, what it exercises, and the evidence of
its validation. Use these as reference probes for cutover acceptance, regression
checks, and new-connector verification.

> **Authentication update (2026-09-03).** Canonical `/mcp` has no anonymous
> read path. Operational probes run as the named, pre-dispatch-allowlisted
> `canary` service principal; user-facing probes require a signed-in connector.
> Historical anonymous probe designs below are not current instructions.

---

## PROBE-001 — Full-stack smoke (cutover acceptance)

**Validated:** 2026-04-20 (DO-cutover acceptance mission)
**Source audit:** `2026-04-20-do-cutover-acceptance.md (archived in git)`
**Persona:** bare-curious-user (no persona framing; minimal context)
**Connector URL under test:** `https://tinyassets.io/mcp`
**Apex URL under test:** `https://tinyassets.io/` (Layer-1 wrapper requires HTTP 200)
**Rendered chatbot client:** Claude.ai, ChatGPT Developer Mode, or another
browser chatbot with the TinyAssets connector visibly installed. Record which
client produced the evidence.

### Prompt (paste verbatim)

```
hey i want to use the tool i set up to design a workflow for writing a research
paper on deep space population — can you walk me through it?
```

### What it exercises

| Layer | What's tested |
|---|---|
| System | MCP endpoint reachable via browser chatbot → Cloudflare Worker → tunnel → daemon |
| System | Apex site `/` returns HTTP 200 alongside the `/mcp` endpoint |
| Chatbot | `control_station` Hard Rule 10 (anti-fabrication): assume → tool-call → correct-if-wrong |
| Chatbot | Chatbot-assumes-TinyAssets directive (rule 7): no disambiguation picker |
| Chatbot | User-vocabulary discipline (rule 9): no engine-vocab in first response |
| User | Full pipeline recommendation grounded in real tool output, not fabrication |

### Green criteria

- Chatbot invokes at least one TinyAssets MCP tool (visible in thinking-block or response).
- Response references real daemon state (e.g., empty workspace, named primitives from actual tool output).
- Layer-1 wrapper confirms `https://tinyassets.io/` returns HTTP 200 while `/mcp` is green.
- No fabricated workflow JSON, no fabricated prior-session history.
- Settle time under 150s. (>150s with tool invocation = soft-yellow; >150s without tool invocation = suspected fabrication-mode.)
- Zero "Session terminated" errors.

### Red signals

- "Session terminated" on any tool call.
- Chatbot produces a workflow spec without invoking a tool.
- Chatbot claims prior-session context that was never established in this chat.
- No tool call at all (chatbot responded from memory only).
- Layer-1 wrapper reports apex `/` non-200 or unreachable while `/mcp` is otherwise green.
- Settle time >180s with no tool call confirmed.

### Baseline evidence

| Date | Condition | Settle (ms) | Result |
|---|---|---|---|
| 2026-04-19 | P0 outage (pre-fix) | >180,000 | RED — 3x Session terminated, 6-node JSON fabricated, hallucinated history |
| 2026-04-20 | Post-cutover | 116,000 | GREEN — live tool cycle, empty-workspace reported correctly, Hard Rule 10 held |

### When to use

- After any change to the Cloudflare Worker, tunnel config, or daemon deploy.
- After any `control_station` prompt edit.
- As the final acceptance gate before marking a cutover "done."
- As a regression check if Layer-1 canary is green but user-reports suggest broken behavior.

### Limitations

- Exercises the full path but not deeply (1 prompt, 1 tool call). For deep
  persona-specific validation, use the per-persona mission drafts.
- "Deep space population" topic is now known to the system; a future probe
  rotation may want a different topic to avoid any cached warm-path effects.
- **Single-URL architecture (2026-04-20):** this probe targets `tinyassets.io/mcp` only.
  The former dual-URL localization trick (green canonical + red `mcp.` = Worker OK, tunnel broken)
  is retired. Layer diagnosis now uses Cloudflare Worker logs + cloudflared tunnel logs
  (see `docs/ops/dns-tunnel-single-entry-cutover.md` § Observability).

---

## PROBE-002 — Layer-2 liveness (minimal)

**Current disposition:** SUSPENDED for public/anonymous use as of 2026-07-24;
the prompt below is historical and MUST NOT be executed against public `/mcp`.

**Validated:** code-fix landed 2026-04-28 (`_real_browser_probe` reimplemented as `claude_chat ask` subprocess + trace-block parser); still awaits host `--once` smoke + Task Scheduler `TinyAssets-Canary-L2` activation for live status. Freshness check 2026-05-01: `Get-ScheduledTask -TaskName TinyAssets-Canary-L2` returned no task. Original implementation (`lead_browser.navigate` + `claude_chat.send_and_wait`) referenced symbols that never existed — see `2026-04-19-layer2-canary-scope.md (archived in git) §Wiring runbook` for the recovery + API contract.
**Source script:** `scripts/uptime_canary_layer2.py` (canary) + `scripts/claude_chat.py` (subprocess driver — `cmd_ask` is the canonical entry point).
**Persona:** `uptime_canary` (dedicated automated persona; CDP profile at `C:\Users\Jonathan\.claude-ai-profile`).
**Connector URL under test:** `https://tinyassets.io/mcp`

### Prompt (paste verbatim)

```
Are you there? Call get_status and tell me the llm_endpoint_bound value.
```

### What it exercises

- MCP connector reachable from Claude.ai.
- `get_status` tool invocable (lowest-dep read-only tool).
- `evidence.llm_endpoint_bound` field readable and surfaced in response.

### Green criteria

- Chatbot invokes `get_status`.
- Response contains a reference to `llm_endpoint_bound` OR `endpoint` OR `bound` (case-insensitive).
- Settle time within normal range.

### Red signals

- No tool call (exit 10 — `tool_not_invoked`).
- Tool called but response empty (exit 11).
- Tool called but field not referenced in response (exit 12).
- Browser could not reach Claude.ai (exit 13).

### When to use

- Automated hourly Layer-2 canary (Windows Task Scheduler entry `TinyAssets-Canary-L2` invokes `python scripts/uptime_canary_layer2.py`). Currently UNWIRED on host as of 2026-05-01; host activates after `--once` GREEN smoke.
- Quick manual liveness check when Layer-1 is green but something feels wrong.

### Cross-host caveat

Layer-2 binds to host availability — the persona's CDP profile + browser run on the host machine. CI (GHA) runs the script every 5 min and exits 14 SKIP cleanly (no browser). A long-term host-independent Layer-2 (e.g. Browserbase or hosted Playwright) is a separate follow-on; today's PROBE-002 is "Layer-2 when host is up; SKIP otherwise."

---

## PROBE-003 — Wiki write-roundtrip with gate fallback

**Validated:** wiki canary script live since 2026-04-22; logs probes to `.agents/uptime.log`. The Layer-1e step in `.github/workflows/uptime-canary.yml` runs every 5 minutes. **Upgraded 2026-09-03:** the named `canary` service principal performs the exact reserved write/read roundtrip at `drafts/notes/uptime-probe.md`. If the CI secret is absent, the script exits 2 before network access; there is no anonymous fallback.
**Source script:** `scripts/wiki_canary.py`
**Persona:** `wiki-canary` (automated; client name `wiki-canary/1.0`)
**Connector URL under test:** `https://tinyassets.io/mcp`

### Invocation

```
python scripts/wiki_canary.py
python scripts/wiki_canary.py --url https://tinyassets.io/mcp --verbose
python scripts/wiki_canary.py --once --format=gha
```

`TINYASSETS_WIKI_CANARY_TOKEN` must be installed in the production server
environment and the GitHub Actions secret. It resolves to the named `canary`
identity and is confined by the pre-dispatch allowlist; it grants no general
universe capability. Missing credentials are red/unknown, never a fallback.

### What it exercises

| Layer | What's tested |
|---|---|
| System | MCP `initialize` handshake reaches the daemon. |
| System | The canary bearer initializes the MCP session and scoped `write_page` reports exactly `drafts/notes/uptime-probe.md`. |
| System | The same named principal's `read_page` returns the fresh per-run content marker just written to the reserved draft. |
| Security | A separate tokenless initialize must return HTTP 401 with a non-empty `WWW-Authenticate` challenge before the authenticated roundtrip. |
| User-impact | Detects wiki write, storage, read, and scoped-auth regressions without an anonymous data path. |

### Green criteria

- Exit code 0.
- Tokenless initialize returns HTTP 401 with a non-empty OAuth challenge.
- The canary-authenticated `write_page` returns `status=drafted|updated` and the
  exact reserved path, then the canary-authenticated `read_page` contains the
  same fresh per-run marker.

### Red signals

- Exit 2 — MCP handshake failed (initialize or session establishment).
- Exit 6 — scoped write rejected, wrong response path/status, present credential invalid, fallback challenge invalid, or write network failure.
- Exit 7 — wiki read failed or canary draft content mismatched.
- Exit 99 — unexpected error.

### Why this probe earns a catalog slot

BUG-028 demonstrated that a slug-normalization bug could silently break wiki writes while the MCP handshake stayed green. The scoped roundtrip now exercises persistence as the confined `canary` service identity. The separate tokenless initialize proves the OAuth challenge.

### When to use

- After any change to wiki write/read tool handlers, slug normalization, or wiki storage backend.
- After any deploy that touches `_wiki_file_bug`, `write_page`/`read_page` routing, scoped canary authorization, or the no-anonymous transport gate.
- As a continuous P0 canary alongside PROBE-002.

---

## PROBE-004 — MCP tool-invocation end-to-end (handshake-vs-handler gap)

**Validated:** mcp_tool_canary script live since 2026-04-22; closes the gap flagged in canary task #6.
**Source script:** `scripts/mcp_tool_canary.py`
**Persona:** `mcp-tool-canary` (automated; client name `mcp-tool-canary/1.0`)
**Connector URL under test:** `https://tinyassets.io/mcp`

### Invocation

```
python scripts/mcp_tool_canary.py
python scripts/mcp_tool_canary.py --url https://tinyassets.io/mcp
python scripts/mcp_tool_canary.py --url http://127.0.0.1:8001/mcp
python scripts/mcp_tool_canary.py --verbose --timeout 20
```

### What it exercises

| Layer | What's tested |
|---|---|
| System | `initialize` handshake (same as PROBE-002). |
| System | `notifications/initialized` (MCP-protocol mandatory before tool calls). |
| System | `tools/list` returns a non-empty tools array. |
| System | `tools/call` for the strongest advertised read-only probe returns valid JSON: canonical `read_graph target=status` requires `schema_version`. |

### Green criteria

- Exit code 0 — all four steps passed.

### Red signals

- Exit 2 — handshake failed (initialize error, network, TLS, non-200).
- Exit 3 — session establishment failed (no `mcp-session-id` header, or `notifications/initialized` POST errored).
- Exit 4 — `tools/list` failed or returned an empty tools array.
- Exit 5 — the selected probe `tools/call` failed or returned an invalid response (missing expected fields, `isError` set, etc.).

### Why this probe earns a catalog slot

`mcp_public_canary.py` (PROBE-001/002 layer) only probes `initialize`, which proves the daemon answers the MCP handshake but NOT that any tool handler actually works. The "handshake green, tool handler crashed" failure class would go undetected without this probe. Catches a distinct failure mode: handshake passes but a tool handler raises uncaught, an export is broken, or `universe_server.py` boot succeeds but a registered tool fails on first call.

### When to use

- After any code change to `universe_server.py` tool registration or handler bodies.
- After any deploy that adds, renames, or removes MCP tools on canonical `/mcp`.
- As a continuous Layer-1.5 canary between handshake-only and full chatbot probes.

---

## PROBE-005 — Last-activity freshness (node execution liveness)

**Validated:** registration 2026-04-26 — script live since task #15 landed (tests at `tests/test_last_activity_canary.py`). Scheduled in CI as the `activity_probe` step in `.github/workflows/uptime-canary.yml`, runs every 5 min on the GHA cron after handshake + tool canaries pass.
**Source script:** `scripts/last_activity_canary.py`
**Persona:** `last-activity-canary` (automated; client name `workflow-last-activity-canary/1.0`)
**Connector URL under test:** `https://tinyassets.io/mcp`

### Invocation

```
python scripts/last_activity_canary.py
python scripts/last_activity_canary.py --url http://127.0.0.1:8001/mcp
python scripts/last_activity_canary.py --threshold-min 60 --verbose
```

Env override: `TINYASSETS_LAST_ACTIVITY_THRESHOLD_MIN` sets the default threshold (minutes).

### What it exercises

| Layer | What's tested |
|---|---|
| System | MCP `initialize` + `notifications/initialized` handshake. |
| System | `read_graph target=graph` (same inspect payload; the deprecated `universe` fat tool refuses anonymous calls since #1441) returns a `daemon.last_activity_at` timestamp. |
| System | The timestamp is fresh — within `--threshold-min` (default 30) of now. |
| User-impact | "MCP green but node execution stalled" failure class — exactly the live-2026-04-22 state before the cloud-side worker landed. |

### Green criteria

- Exit code 0.
- `daemon.last_activity_at` is within the threshold (FRESH).

### Red signals

- Exit 2 — `last_activity_at` exceeds the threshold (STALE — paged regardless of queue depth; persistent stale-but-empty IS pageable per script docstring).
- Exit 3 — daemon responded but no parseable `last_activity_at` (unexpected tool shape, null/malformed field).
- Exit 4 — handshake / connectivity failure (distinct exit so operators can tell stale-execution from dark-daemon at a glance; overlaps with PROBE-001 deliberately).

### Why this probe earns a catalog slot

PROBE-001/002 prove the daemon answers MCP. PROBE-004 proves a tool handler runs. NEITHER proves the daemon is doing actual work — node execution can stall while every probe above stays green. The 2026-04-22 cloud-worker outage is the worked example: green Layer-1, green tool calls, zero scenes shipped. This probe is the only one in the catalog that catches that class.

### When to use

- After any change to dispatcher, executor, or the cloud worker.
- As a continuous Layer-1.5 canary alongside PROBE-002.
- Investigating user reports of "the daemon is up but nothing's happening."

---

## PROBE-006 — Revert-loop detection (busy-broken pathology)

**Current disposition:** SUSPENDED against public `/mcp` as of 2026-07-24.
The invocation below is historical/internal evidence only; restore it only
through an authenticated operator surface or after safe status projection.

**Validated:** registration 2026-04-26 — script live since revert-loop spec landed; spec at `2026-04-23-revert-loop-canary-spec.md (archived in git)`. Scheduled in CI as the `revert_loop_probe` step in `.github/workflows/uptime-canary.yml`, runs every 5 min on the GHA cron after handshake + tool canaries pass.
**Source script:** `scripts/revert_loop_canary.py`
**Persona:** `revert-loop-canary` (automated; client name `revert-loop-canary/1.0`)
**Connector URL under test:** `https://tinyassets.io/mcp`

### Invocation

```
python scripts/revert_loop_canary.py
python scripts/revert_loop_canary.py --url http://127.0.0.1:8001/mcp
python scripts/revert_loop_canary.py --verbose
```

Env overrides:
- `TINYASSETS_REVERT_CANARY_N` — WARN threshold (default 3)
- `TINYASSETS_REVERT_CANARY_T_MIN` — WARN window minutes (default 10)
- `TINYASSETS_REVERT_CANARY_N_CRITICAL` — CRITICAL threshold (default 5)
- `TINYASSETS_REVERT_CANARY_T_CRITICAL` — CRITICAL window minutes (default 20)

### What it exercises

| Layer | What's tested |
|---|---|
| System | MCP handshake + `get_status.evidence.activity_log_tail` retrieval. |
| System | Terminal REVERT-verdict count within a sliding time window. |
| User-impact | "Daemon IS making progress but every scene REVERTs" — the 2026-04-23 P0 class (67 reverts on `concordance` before host noticed). |

### Green criteria

- Exit code 0 — REVERT count below WARN threshold.

### Red signals

- Exit 2 — WARN: ≥N_WARN REVERTs within T_WARN minutes (page priority=0).
- Exit 3 — CRITICAL: ≥N_CRIT REVERTs within T_CRIT minutes (trigger auto-repair via p0-outage-triage).
- Exit 4 — handshake / connectivity failure (distinct from stale/dark for diagnostics).
- Exit 5 — daemon responded but `activity_log_tail` absent / unparseable.

### Why this probe earns a catalog slot

The 2026-04-23 P0 revert-loop concern is currently top-of-STATUS. PROBE-001 (handshake) and PROBE-005 (last_activity) both stay GREEN during a revert-loop because the daemon IS making progress — it's just throwing every commit away. Only this probe catches the busy-broken state. Spec-driven (Q2 mandate: count terminal REVERT verdicts only; Draft-FAILED retry-recovers within-scene and was explicitly rejected as noise).

### When to use

- After any change to the commit pipeline, scoring rubric, or provider-routing code.
- As a continuous P0 canary — pair with PROBE-005 to cover both "stalled" and "busy-broken" failure modes.
- After provider-quota / model-config changes (a degraded provider is a leading indicator of revert cascade).

---

## PROBE-007 — DNS resolution canary (host-independent vantage)

**Validated:** registration 2026-04-27 — workflow live since `.github/workflows/dns-canary.yml` landed; runs every 15 min from GitHub Actions infra (out-of-band from the host machine).
**Source workflow:** `.github/workflows/dns-canary.yml`
**Persona:** automated GHA runner (uses `socket.gethostbyname` from a Python inline block; no script wrapper).
**Hostnames under test:** `tinyassets.io`, `mcp.tinyassets.io`.

### Invocation

Runs automatically on the `*/15 * * * *` cron schedule + manual `workflow_dispatch` from the Actions tab.

### What it exercises

| Layer | What's tested |
|---|---|
| System | DNS A-record (or CNAME chain) resolution for the canonical apex plus the Access-gated tunnel origin from a vantage independent of the host machine. |
| User-impact | Catches NXDOMAIN regressions invisible to host-local resolvers — exactly the 2026-04-19 P0 class where a Cloudflare tunnel reshuffle silently dropped the apex CNAME. |

### Green criteria

- Both `tinyassets.io` and `mcp.tinyassets.io` resolve to an IP from the GHA runner.
- TinyAssets exits 0; `overall=green` in step output.

### Red signals

- Either hostname fails to resolve (`socket.gaierror` or other exception).
- TinyAssets exits non-zero; `overall=red`.
- Two consecutive reds → opens GitHub Issue labeled `dns-red`.
- GREEN after open issue → comments RECOVERED + closes the issue.

### Why this probe earns a catalog slot

The 2026-04-19 P0 outage-postmortem (`2026-04-20-public-mcp-outage-postmortem.md (archived in git)`) §3 named "no out-of-band probe" as the load-bearing gap that allowed a 69-min silent outage. PROBE-001/002/004 all probe `/mcp` over HTTP; if the apex CNAME vanishes (NXDOMAIN), HTTP probes fail in distinguishable but similar-looking ways. PROBE-007 narrows diagnosis: a DNS-red without HTTP-red is a name-resolution / propagation issue; a DNS-red with HTTP-red corroborates a tunnel/dashboard-state regression. Distinct from `uptime-canary.yml` which probes Cloudflare-Worker-fronted HTTP — DNS-canary probes one layer below.

### When to use

- Continuous out-of-band tunnel-state monitoring (catches CNAME deletions during Cloudflare-dashboard automation sessions).
- Post-DNS-record-change verification (godaddy-ops + cloudflare-ops skills both reference this).
- Investigating user reports of "tinyassets.io is down" — first-look layer to differentiate DNS from HTTP from MCP-handler causes.

---

## PROBE-008 — LLM binding freshness canary

**Current disposition:** RETIRED 2026-09-24. The platform has no LLM (AGENTS.md
Hard Rule 15), so a production daemon reporting no bound model is the invariant,
not a failure; `.github/workflows/llm-binding-canary.yml` was deleted. The
description below is historical only.

**Validated:** registration 2026-04-27 — workflow live since `.github/workflows/llm-binding-canary.yml` landed; runs every 6h from GitHub Actions.
**Source script:** `scripts/verify_llm_binding.py` (invoked from GHA workflow).
**Source workflow:** `.github/workflows/llm-binding-canary.yml`
**Connector URL under test:** `https://tinyassets.io/mcp`

### Invocation

Runs automatically on the `0 */6 * * *` cron + manual `workflow_dispatch`.

### What it exercises

| Layer | What's tested |
|---|---|
| System | Live daemon `get_status` call — `evidence.llm_endpoint_bound` field readable. |
| System | Field is NOT the literal string `"unset"` — daemon has at least one provider bound and reachable. |
| User-impact | "MCP green, tools call cleanly, but no LLM is bound" silent-failure class (token rotation expiry, codex CLI version drift, docker restart that lost env injection). |

### Green criteria

- Exit code 0 — `verify_llm_binding.py` confirms `llm_endpoint_bound != "unset"`.
- TinyAssets exits 0; `overall=green`.

### Red signals

- Exit non-zero — handshake failed or `llm_endpoint_bound == "unset"`.
- Two consecutive reds → opens GitHub Issue labeled `llm-binding-red`.
- GREEN after open issue → comments RECOVERED + closes.

### Why this probe earns a catalog slot

PROBE-002 (Layer-2 chatbot liveness) overlaps on the `llm_endpoint_bound` field semantically — but PROBE-002 routes via Claude.ai (chatbot vantage, hourly cadence, designed-only as of catalog filing) while PROBE-008 calls the daemon directly from GHA on a slow-drift cadence appropriate to binding regressions. Different vantage, different cadence, different failure-mode focus (binding drift, not chatbot-tool invocation). Closes the "host-machine offline = silent binding loss for hours" gap.

### When to use

- Continuous host-independent binding monitoring.
- After provider-token rotation, codex CLI upgrade, or any deploy that touches the daemon's provider env.
- Investigating user reports of "the daemon answered but said no LLM was bound" — confirms whether the report is current vs cached.

---

## PROBE-009 — Tier-3 OSS fresh-clone smoke

**Validated:** registration 2026-04-27 — workflow live since `.github/workflows/tier3-oss-clone-nightly.yml` landed; runs nightly from GitHub Actions.
**Source workflow:** `.github/workflows/tier3-oss-clone-nightly.yml`
**Source script:** `scripts/tier3_smoke.py` + `scripts/import_graph_smoke.py` (called inside the workflow).
**Persona:** automated GHA runner simulating a fresh tier-3 OSS contributor path.

### Invocation

Runs automatically on the `17 7 * * *` cron (07:17 UTC daily) + manual `workflow_dispatch`.

### What it exercises

| Layer | What's tested |
|---|---|
| System | `git clone --depth 1` of the repo on a fresh GHA runner (no cache, no host artifacts). |
| System | `python -m venv .venv` + `pip install -e .` succeeds from `pyproject.toml` alone. |
| System | `import workflow` works at top level after install. |
| System | `scripts/tier3_smoke.py` structural smoke + `scripts/import_graph_smoke.py` import-graph smoke (catches missing-symbol / dropped-file regressions at package import). |
| System | `pytest tests/smoke/ -x` — fast smoke-test subset green on a fresh clone. |
| User-impact | AGENTS.md Forever Rule "tier-3 OSS contributors `git clone` and run cleanly" — directly probes the contributor onboarding surface. |

### Green criteria

- All 6 workflow steps (clone, venv, install, top-level import, structural smoke, import-graph smoke, smoke pytest) exit 0.
- TinyAssets conclusion = success.

### Red signals

- Any step exits non-zero.
- Failure path opens GitHub Issue labeled `tier3-broken` with the failing commit SHA + run URL.
- No auto-recovery / auto-close — issue stays open until manual triage + fix-forward (debug order documented in the workflow body).

### Why this probe earns a catalog slot

Per `2026-04-20-public-mcp-outage-postmortem.md (archived in git)` §6: "Highest-priority post-canary addition: tier-3 OSS clone nightly probe. That surface is just as public as the MCP connector and has zero monitoring today — the project is one bad commit away from 'fresh contributors silently bounce because pip install -e . fails.'" PROBE-001..008 all probe the running daemon; PROBE-009 alone probes the contributor onboarding surface. Catches dirty-tree commits, missing files, broken pyproject.toml, dropped symbols, and import-graph regressions that running-daemon probes cannot see.

### When to use

- Continuous monitoring of the OSS-contributor entrypoint (passive — fires nightly).
- After any commit that touches `pyproject.toml`, package layout, top-level imports, or `__all__` declarations.
- Before merging structural refactors (manual `workflow_dispatch` to run the smoke against a PR's HEAD before the daily cron).

---

## Borderline scripts — intentionally not catalogued

These canary-adjacent scripts exist in the repo but do NOT earn standalone catalog slots per the audit's admission criteria. Listed here so future audits can confirm their omission is intentional, not accidental.

| Script | Why no slot |
|---|---|
| `scripts/uptime_canary.py` | Thin wrapper around `mcp_public_canary.probe_result` that adds local-log persistence and production apex `/` HTTP-200 coverage. Same surface as PROBE-001 — duplicating it as a slot would double-count. The wrapper is the Task-Scheduler-invoked form; PROBE-001 is the user-invocable form. |
| `scripts/uptime_alarm.py` | Escalation **action**, not a probe. Tails `.agents/uptime.log` and emits alarm lines. Note: per task #20, prod alarm log moved to `/var/log/tinyassets/uptime_alarms.log` (env-overridable). |
| `scripts/selfhost_smoke.py` | Time-bounded Row-F acceptance script (48h offline trial — hour 1, 24, 47). Default mode targets the canonical endpoint and confirms the public direct tunnel origin is Access-gated (401/403). `--internal-parity` preserves the old parity comparison only for internal/service-token tunnel paths. Not a steady-state public probe; once Row F closes, this script is archived. |
| `.github/workflows/p0-outage-triage.yml` | Escalation **action**, not a probe. Auto-fired by `uptime-canary.yml` on CRITICAL alarm; runs forensics + opens triage issue. Same shape as `uptime_alarm.py` (action, not green/red signal). |
| `.github/workflows/dr-drill.yml` | Quarterly disaster-recovery rehearsal triggered by `workflow_dispatch` only. Acceptance-test-as-CI rather than steady-state probe; same family as `selfhost_smoke.py`. STATUS Work table tracks drill cadence. |

A "post-fix clean-use evidence" verification primitive (AGENTS.md Quality Gates) also has no automated probe today. This is intentional: the primitive is evidence-gathering against real-user traces, not a parseable green/red script.

---

## Adding new probes

A probe earns a catalog entry when:
1. It has been run live (not just designed) against the production endpoint.
2. It has at least one green baseline AND one red baseline (or a documented pre-fix red state).
3. Its green/red criteria are parseable without human judgment.

Draft probes (designed but not yet live-validated) may be kept in the
mission-draft files until they earn a live run.
