## Context

On 2026-09-19 the second user's guided OpenRouter callback and free-model
approval completed on production `bcac8d1a2503`, but the first greeting failed
before inference. The server and route reader both required an obsolete env
allowlist. Claude Fable 5.1 independently reviewed the shape (terminal exit 0,
317 seconds; retained review: `docs/reviews/2026-09-19-engine-owner-admission-shape.md`). Verdict:
ADAPT, with owner-ACL verification, live resource sizing and sandbox verification
required. This change implements that general admission shape, not an exception.

## Goals / Non-Goals

**Goals:** Every current serving owner can use the same confined engine tool
surface with their own authorized model, for conversation and background work.
Revoked owners, deleted principals and ambiguous bindings fail closed.

**Non-Goals:** User workflow edits, new provider spend authority, account-specific
allowlisting, tool-free greeting fallbacks, shared multi-tenant MCP processes,
changes to source approvals or outbound consent.

## Decisions

Use one shared read-only authority check over existing agent bindings, universe
admin ACL and account deletion records. Read in a transaction; require exactly
one distinct serving creator and that creator's current admin ACL. Multiple
bindings for the same owner are valid; competing owners refuse instead of taking
the first row. Do not impose home-only access: an owner can administer multiple
universes. Missing/malformed storage refuses; never create authority from an env
pin or route-file record.

Apply the check to supervisor publication, every route read and every tool entry
(including stdio fallback), alongside the deployment engine flag. This closes
the supervisor's reconciliation delay for revoked identities. Canonical handlers
still enforce their own graph, branch, consent and provider permissions.

Remove all six engine vetted-membership gates, but do not remove the separate
source-approval allowlist. The process remains pinned to a single actor/graph
and its private loopback bearer. No protocol or storage schema migration.

## Risks / Trade-offs

- Process-per-universe memory: measured 2026-09-19 on production using `docker
  stats --no-stream`, `docker top ... -eo pid,rss,args`, `free -m` and read-only
  distinct-serving count. Five serving universes; two existing engine processes
  used 198 and 77 MiB RSS; daemon container 531 MiB of 4 GiB, host 7.75 GiB with
  6.61 GiB available. Three additional processes at observed high RSS fit with
  substantial headroom. This is current deployment evidence, not unlimited-scale
  proof; idle retirement/on-demand capacity remains follow-up hardening.
- Authority reads add local SQLite reads: use read-only transactions and no
  mutable cache. Failure is an explicit unavailable tool surface.
- Code isolation: current compiler invokes NodeSandbox and fails closed without
  sandbox support; production contains `/usr/bin/bwrap`. Require Linux sandbox
  regression evidence before release. Do not rely on stale in-process concern.
  Production supporting probe at 2026-09-19 04:47UTC: `docker exec -i
  tinyassets-daemon python -` invoking NodeSandbox.run_sync on a constant
  arithmetic-only node returned success=True, output_state={'answer':3}, empty
  error. No universe/workflow/user data was touched; this is not user acceptance.

## Migration Plan

Ship code plus regressions and remove obsolete env-setting workflow option.
Existing env value becomes irrelevant; no production user key or binding changes
are required by the fix. Owner acceptance directive September19: reset/disconnect
the failed test account and, only after the general fix is deployed and supporting
verification passes, restart the entire sign-in-to-free-reply journey. Do not
resume a partially connected account, repair it midway, add an account exception,
paste keys or replay the failed greeting to obtain a passing claim. Every failed
attempt is reset before repair and another complete end-to-end attempt.
Owner-directed isolation 2026-09-19 04:37UTC: the test user's OpenRouter key
`OAuth: TinyAssets free models` was disabled in the provider UI, verified by the
row offering Enable. This disables access only, not the stored TinyAssets record.
Do not touch the test account again until the fix is ready; full reset remains
required before the fresh acceptance run.
Verify deployment receipt, canary and second-user rendered reply/tool use.
Roll back the
image if isolation or availability fails; retain honest acceptance status.

## Reviewed startup readiness

Fresh source inspection found a first-send startup race: the supervisor notices
new serving universes at 15-second intervals and publishes immediately after
Popen, before the listener is ready. The HTTP tool client immediately refuses a
missing route; workflow_agent has the same premature route check. Removing the
membership gate alone does not make a fresh first-send journey reliable.

Independent Fable5.1 shape review completed September19 (terminal exit0,
195seconds, ADAPT; docs/reviews/2026-09-19-engine-startup-shape.md). Implement
bounded readiness BEFORE provider inference or tool dispatch in the shared
HTTP tool client: notify only the existing matching-root supervisor, wait at
most min(caller timeout,30seconds) for current authorized route and a no-bytes
loopback TCP probe, and refuse if authority disappears. Without an in-process
supervisor keep immediate route-read behavior. Readers and per-tool authority
checks remain nonwaiting; async cancellation propagates. After readiness perform
ONE MCP handshake/discovery; never replay failed discovery, tools or conversation.
Background work prechecks authority rather than transient route readiness and
settles any reserved invocation as cancelled-before-launch on startup failure.
Serving-binding commit only wakes the existing supervisor; it never creates a
per-request server. Focused readiness regressions were proven red before code.
Exact-head cross-family review and live acceptance remain release gates.
