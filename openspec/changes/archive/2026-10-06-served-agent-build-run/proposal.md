# Served agent can BUILD and RUN its own automations

## Why

The universe you talk to in the app (the served agent) can currently READ graphs,
browse the commons, read/write its brain, and register compute providers
(`connect_compute`) — but it CANNOT build or run automations. Its engine-MCP
allowlist (`_ENGINE_MCP_ENABLED_TOOLS` in `codex_provider.py`; `_ENGINE_MCP_TOOLS`
in `universe_intelligence.py`) deliberately EXCLUDES `write_graph`, `run_graph`,
and `remix_shape`, with the code comment: *"excluded above for the unsanitized
cross-universe invoke path."*

That blocker is now gone. PR #2498 (deployed on `dafa2b5c`, `running_healthy`)
sanitized `invoke_branch`: delegated child-authority (own→own/public;
public-foreign→public-only), fail-closed actor from the immutable
`BranchExecutionContext`, foreign-edge input/output confidentiality, and
await/poll run-id binding to the acting principal + universe. A served run can no
longer reach another universe's private branches or spoof the actor — the exact
risk that justified withholding run/build.

So this change closes the founder's surface-parity gap: **all surfaces do the same
things**. In a chatbot connector a user already has `write_graph`/`run_graph`; the
app (web/desktop/phone chat with the universe) must reach parity so a user can ask
their universe to build a workflow, run it, and add channels we haven't tried
(Slack, etc.) via the channel-agnostic node — without leaving the app.

## What changes

Add BUILD + RUN verbs to the served agent's engine-MCP allowlist, scoped to the
universe's OWN authored, approved shapes and acting as the verified founder
principal:

1. **`write_graph` (target=branch)** — the served agent may create/patch branch
   graphs in its OWN universe only. No cross-universe write; storage + author
   resolve against `ctx.universe_id` / the verified principal (never the actor_id
   param, never an env fallback).
2. **`run_graph`** — the served agent may execute its own universe's approved
   automations. Reuses current serving-owner admission and the hardening
   already shipped (per-request HTTP bearer auth, crash-supervisor + dynamic
   reconcile, effect-spam rate-limit, budget boot-reconcile + retention,
   immutable-version and OS code isolation). Execution rides the sanitized
   `invoke_branch` path (#2498) for any sub-branch invocation.
3. **`remix_shape`** — re-includable now that invoke is sanitized; remix stays
   execution-closure-gated (re-approval before a remixed shape can run/publish).
4. **Channel + consent verbs** — `grant_effector_consent` / `approve_source_channel`
   / the generic authenticated-external-call + named-connection primitive, so the
   served agent can add a user-built channel (Slack/GitHub/…) through the ONE
   channel-agnostic node system rather than any hard-coded per-channel effector.

Selection stays deterministic and owner-gated. Every added verb is DARK unless it
is in BOTH the engine-MCP server (`@mcp.tool`) AND the provider allowlist.

## Boundaries (defer, do not duplicate)

- General engine admission is owned by `admit-owner-bound-engine-tools`: current
  serving creator/admin/deletion checks replace the vetted-universe env list.
  Still-owed hardening here: explicit same-author branch↔universe binding,
  one-use ingress permit and true rolling cumulative budget. These do not grant
  cross-user authority and are not grounds for provider-specific tool denial.
- The compute SDK access method (`compute-sdk-access-method`) is a parallel lane.
- No new authority model: reuse the `serve-open-compute-provider` connection-grant
  path + the invoke_branch `BranchExecutionContext` from #2498.

## Security invariants (must hold)

- Served build/run acts ONLY as the verified request principal in
  `ctx.universe_id`; no cross-universe write/read/execute; no env-actor fallback.
- `run_graph` stays behind current serving-owner admission and OS code isolation;
  nothing EXECUTES unless attached to the acting universe's own authored + approved
  shape (founder hard rule 2026-08-22).
- Sub-branch invocation uses the sanitized #2498 path (delegated authz, fail-closed
  actor, mapping/await confidentiality) — re-verified, not assumed.
- Effect execution stays consent-gated + rate-limited; a served turn cannot spend
  beyond the universe's budget or emit unbounded effects.
- Parallel grounding-file / allowlist lists (`codex_provider` and
  `universe_intelligence`) MUST stay in sync — extend the existing drift guard so
  the two served allowlists cannot diverge silently.

## Gate (before merge; before any served run/build exposure)

Authority-sensitive (served build+run = RCE-risk area):
- Codex SHAPE review of this proposal before build; Codex exact-diff before merge.
- Full run/branch/graph-compiler/provider suite: zero new failures + new negative
  tests (cross-universe write refused, non-founder refused, run gate holds,
  effect/budget caps hold, invoke sub-branch stays sanitized).
- §14 concurrency/load-test proof (Forever Rule): the added run path has concurrent
  boundary coverage.
- Rebuild plugin mirror + parity; `mcp_public_canary --assert-handles` unchanged
  for the public connector.
- Final chatbot-surface `ui-test`: through the live app, ask the universe to build
  a small workflow, run it, and confirm the result — plus a cross-universe-refusal
  probe. Log to `output/user_sim_session.md`.
