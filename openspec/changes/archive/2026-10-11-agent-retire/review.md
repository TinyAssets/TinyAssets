# Agent retirement: Claude cross-family review

Lane: `feat/agent-retire`; draft PR #4528; resolves #4521.
Reviewer: Claude, dispatched through `scripts/peer_agent.py claude`, read-only,
900-second timeout, completed successfully in 184 seconds. No subagents or
second review round. The review covered the working tree, including the durable
retirement revision added during review. Final reviewer verdict: **ADAPT**.

## Findings and disposition

1. **AGREE — main while disconnected.** The original guard protected `main`
   and serving bindings but allowed configured platform/provider bindings.
   Retirement now refuses both current and legacy platform definition authors
   and any binding carrying `provider_ref`, even while configured. Regression
   cases cover all three shapes and assert unchanged rows.
2. **AGREE — internal enumeration.** Hiding retired rows by default could make
   bootstrap and ambiguity checks overlook existing bindings. Internal
   `list_bindings` keeps its all-row default; public binding lists and the
   command-center roster explicitly filter retired rows. Tests assert both.
3. **AGREE — activity creation race.** Custom-agent activity validation and
   insertion now hold shared cross-process binding admission. Retirement holds
   exclusive admission through its activity fence, so a validated insertion
   cannot appear after that fence. Retired activities remain terminal after
   restore; new activity creation is refused while retired.
4. **AGREE — failed lifecycle reads.** The native cancellation watcher now
   propagates lifecycle-read errors and awaits inference cleanup. Exception
   handling does not reread the failing store or replace its error with an
   owner-stop claim. A regression injects a database failure during inference.

Additional correctness check: configuration/provider mutations require restore
first, preserving the ability to undo retirement. Turns capture the last
retirement revision, so a quick retire/restore in another process cannot revive
an older turn.

## Verification

The initial integrated Linux oracle run passed 346 tests with zero skips,
including Chromium, prompt budgets, owner isolation, account deletion, the ta
client, jail capabilities, and turn interruption. After review adaptations,
the focused regression file passed 18 tests with zero skips. Final expanded
verification passed 391 tests with zero skips, including the further guard
requiring restore before configuration/provider mutations. The exact suites
and delivery checks are recorded in the change tasks and PR body.
The three affected heavy suites passed another 146 tests with zero skips:
MCP instruction surfaces, universe-server isolation and scoped identity reset.
Total final Linux evidence: **537 passed, zero skips**.

The Chromium test imports Playwright within the test and renders the shipped
switcher function against the real roster: Evidence Weaver is present, absent
after retirement, and present after restore. No feature editor was added.

## Delivery boundary

This lane delivers a draft PR, not a production deployment. No production
agents were retired. Deployment SHA assertion and a live authenticated app-agent
pass remain for the eventual merge/deploy. Ordinary branch automations do not
carry agent-binding identity; scheduled activities that do carry an `agent_id`
are fenced and cancelled along with that agent's running turns.
# Round 3 fixture review (2026-10-06)

Peer: Claude via `scripts/peer_agent.py claude`, read-only, 181 seconds.
**VERDICT: APPROVE**. **AGREE**: no floor/correctness findings.

Confirmed the app-live fixture's published definition and binding match the
activity's base, universe and owner; the dynamic-ID assertion retains its
meaning and all other assertions remain. No production checks changed.

An independent AST sweep found four direct activity-create calls with explicit
agent IDs: two retirement tests with real bindings and the two repaired live
fixtures. Reviewed indirect `bound_requests.capture` and
`connection_continuations.bind` paths, the non-main activity runner fence, and
turn retirement checks. Remaining placeholder IDs are mocks, storage keys or
negative/refusal paths. The peer ran only the external-call effector file
(53 passed); the lead's Linux oracle supplies delivery verification.
