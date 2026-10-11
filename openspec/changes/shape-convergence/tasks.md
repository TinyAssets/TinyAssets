## 1. Contract

- [x] 1.1 Proposal, design and spec delta. The registry merges separately in
      #4619. Verify: `npx openspec validate shape-convergence --strict`.

## 2. Convergence items (in order; one PR each; deletions ship in the same PR)

C1 and C2 are tasks of this change. C3–C12 are the order, not tasks: each
ships as its own change (C3 is `one-model-call-path`) and is ticked there.
The reconciler lists their state.

Each net figure is an estimate of executable plus document lines, mirror
excluded. The PR measures the real figure, and a positive net does not merge.

- [ ] 2.1 **C1 Guards, document homes, document prune.** Add
      `scripts/check_shapes.py` with one `SHAPES` table keyed S1–S27, plus
      `tests/test_shape_registry.py`. Register it in
      `scripts/ci_structural_guards.py`. Checks: registry-parity, ceiling,
      converged, second-path, opt-in-flags, doc-homes, adr-has-spec,
      superseded-open, public-routes (design §3). Move
      `test_one_agent_definition`, `test_one_extension_unit`,
      `test_platform_has_no_llm` and `test_private_by_default` into the
      structural set.
      **Deletes:**
      - `docs/design-notes/` (61 files). Before deleting, fold the
        2026-04-29 token boundary into an ADR, and fold the lean-CI notes
        into ADR-003's successor.
      - `docs/specs/` (52), `docs/exec-plans/` (73), `docs/historical/`
        (28) and `docs/reviews/` (89; lower its aggregate pin).
      - `docs/audits/` (267), except the incident records still cited.
      - Root `docs/*.md` clutter (`PAID-MARKET-START-HERE`, `SUCCESSION-*`,
        `USER-PATH-*`, `WORK-*`, `reality_audit`, `planning_sweep_*`,
        `conway_readiness_strategy`, ...), plus `docs/planning/`,
        `docs/plan-drafts/`, `docs/handoffs/`, `docs/milestones/`,
        `docs/notes/`, `docs/research/` and `docs/archive/root-scratch/`.
      - AGENTS.md Facts 3 and 15. The guards now enforce them; leave the
        numbers as gaps.

      Repoint the about 40 code comments that cite deleted paths.
      Gate-file change: one cross-family round.
      Guard: itself. Net about −100k. Depends on #4619.
- [ ] 2.2 **C2 Reconciler and queue fold.** Add
      `.github/workflows/shape-reconcile.yml` and
      `scripts/shape_reconcile.py` (design §3.2), which maintain the one
      "Shape reconciliation" issue. Replace AGENTS.md's `openspec_flow.py
      audit` orient pointer with the issue, byte-neutral.
      **Deletes and folds:**
      - Archive the 16 fully checked changes.
      - Delete `command-center-publish-repair`.
      - Delete the six changes `one-extension-unit` supersedes, after moving
        any open task into it.
      - Fold `usage-limits-replace-caps`, `two-dimension-usage-limits`,
        `remove-non-usage-limits`, `account-storage-quota` and
        `consolidate-platform-resource-policy` into one limits change.
      - Fold `command-center-cutover` into
        `rename-universe-to-command-center`, then into C1's ratchet.
      - Fold the four starter changes into one.
      - Fold `node-scheduled-wake`, `owner-message-wakes`,
        `automation-event-triggers`, `served-inbound-webhook-triggers` and
        `automation-schedule-timezone` into one wake-triggers change.
      - Turn `target-architecture` tasks into pointers to C6, C7 and C9.
      - Close one of #4589 and #4602. The survivor drops its public
        `/api/v1` and becomes `ta` and model-door work.

      Gate-file change: one cross-family round.
      Guard: superseded-open, public-routes. Net about −6k. Depends on C1.
- **C3 One model call.** Execute
      `openspec/changes/one-model-call-path/` tasks 2.1–2.8 (#4616 step 1).
      **Deletes:** `call_provider`, `call_with_policy(_sync)` and its 5
      definitions, `UniverseBoundProviderCall`, `_build_fallback_router`,
      `FALLBACK_CHAINS`, the default/manifest scope split, 4 of the 5
      `TurnRequestBudget` roots, the MCP-sampling reviewer, the prompt
      band-aid in `engine_mcp_server`, and both mocks
      (`graph_compiler.py:1741`, `providers/call.py:_force_mock`). Moves the
      platform helpers (`reflexion`, `raptor`, `agentic_search`,
      `extract_learning`) onto the owner's call or deletes them.
      Floor (authority, money): its own cross-family rounds.
      Guard: `call-sites` = 0, `budget-roots` ≤ 1, S1 and S13 read
      converged. Net about −1.1k. Depends on C1.
- **C4 Plugin mirror built, not committed.** `build_plugin.py`
      stages `runtime/` at package time. Invert
      `scripts/check_mirror_parity.py` and the pre-commit hook to "no tracked
      mirror", and delete `packaging/claude-plugin/.../runtime/`.
      Guard: S19 converged. Net about −336k tracked. Parallel to C3.
- **C5 Dead limbs and dead spawn paths.**
      **Deletes:**
      - `cloud_automation_continuation.py` (3027) and
        `background_served_provider.py` (1790) with their tests.
      - `host_pool/` (734), the local `mcp_server.py` (442) and its console
        script, and the compose `slack-agent` service.
      - The tray host (`desktop/` except `icon_gen.py`, `tinyassets_tray.py`,
        `tinyassets.pyw`, the `.bat`/`.vbs` launchers) together with the
        `desktop-host-runtime` and `daemon-identity-and-host-pool` specs.
      - The root `fantasy_daemon/` runner (6.8k).
      - The `fantasy_daemon` and `research_probe` domain entry points. Their
        useful behaviour becomes a skill fixture. Repoint AGENTS Fact 5 to a
        core module first.
      - The `prototype/` trees and schemas, after re-grepping their cites.

      Guard: `host-paths`, `domain-entry-points`, `spawn-entry` (only the
      live starters remain). Net about −30k. Depends on C1; parallel to C3.
- **C6 One box per owner.** First, an ADR resolves the owner cell
      versus Firecracker/gVisor question. Then `BoxProvider` attaches once
      at execution admission, and per-path `role_*` cells become one
      backend behind it. Includes the `daemon_server.py` tables moving into
      `storage/` (S10), and off-region backup, key escrow and the DR drill
      from the box copy (S27).
      **Deletes:** the per-path cell kinds and their separate admissions,
      the platform writers in owner space, and `target-architecture`.
      Floor (cross-user): its own rounds.
      Guard: `role-cells` ceiling falls to one backend;
      `test_universe_path_io_guard` at 0. Net about −5k. Depends on C3, C5.
- **C7 Control plane only.** Wakes, continuations, automations,
      activities and event triggers become control-plane wake handlers on
      one tick.
      **Deletes:** the `scheduler.py` thread, `dispatcher.py`, `producers/`,
      `branch_tasks.py` (merged into v2), `request_continuations` and
      `agent_wakes` threads, and the limit code outside `usage_policy.py`
      (the folded limits change).
      Guard: `test_control_plane_inventory`, `limit-names`, and
      `opt-in-flags` for `TINYASSETS_ASSIGNED_QUEUE_CONSUMER`. Net about
      −3k. Depends on C3, C6.
- **C8 Agents are files; one starter.** `agents/<name>/` holds
      instructions and settings. `session.start(agent_ref, input, parent)`
      is the only starter for chat, agent nodes, addressed agents and
      `ta agent run`.
      **Deletes:** the `custom_agents.py` definitions, most of
      `agent_interchange.py`, the templates, `agent_review.py` as a platform
      feature, and the `agent_runtime_manifest` literal (with a migration
      for its CHECK constraint).
      Guard: `spawn-entry` = 1, `test_one_agent_definition`. Net about −4k.
      Depends on C3.
- **C9 One loop.** The thin loop becomes the only loop.
      **Deletes:** `TINYASSETS_AGENT_LOOP`, `TINYASSETS_ALLOW_CLAUDE_SERVING`,
      `claude_provider.py`, `codex_provider.py`, `codex_app_server.py`,
      `codex_launch_contract.py`, `onboarding/openai_device.py`,
      `ollama_provider.py`, `_CLI_REFS`, `_CLI_PROTOCOLS`,
      `_CLI_PROVIDER_CLASSES`, the provider and discovery cells, and the
      CLIs in the `Dockerfile`. Native CLIs remain only as named
      external-agent packages over the job API, and only after parity
      (#4616 step 4).
      Guard: `vendor-names` ceiling to the data-only floor, `opt-in-flags`.
      Net about −8k. Depends on C6, C8, `broker-streaming-contract`.
- **C10 Workflows and extensions as packages.**
      **Deletes:** the v1 workspace-executable loader (`ta_cli.py:54-105`),
      the Activities branch, and the platform `invoke_branch*` and
      `await_branch_run` node kinds (converted once). Folds
      `engine_mcp_server.py` handles into the one registration that
      `universe_server.py` serves. Narrows `api/market.py` to what the app
      calls, and deletes `bid/`, `treasury/` and the paid-market spec unless
      the founder ratifies the tiny-currency direction.
      Guard: `second-path` for the extension loader and handle
      registration. Net about −7k. Depends on C8.
- **C11 UI as a package; one durable send.** Owner messages are
      stored once, then dispatched (commit before publish). The app's send
      path and `converse` share it. Split `app.html` into modules, and make
      the default layout an ordinary package (#4616 step 6).
      **Deletes:** the duplicate send and retry paths, plus the bespoke UI
      operation lists.
      Guard: `send-path`. Net about −1k. Depends on C10.
- **C12 Knowledge is files; export.** The OKF bundle write path
      becomes canonical, and `memory/`, `retrieval/` and `knowledge/`
      become derived indexes. One export action follows (ADR-018).
      **Deletes:** the canonical-store duplicates in `daemon_brain.py` and
      the unwired `okf_export.py` branch.
      Guard: S17 converged. Net about −2k. Depends on C6.

## 3. Pull requests and notes folded by this change

- [ ] 3.1 Fold or close, each with a pointer to this change:
      - #4616: keep `openspec/changes/one-model-call-path/` (it is C3) and
        drop its design note; its findings are design §1 and §2.
      - #4609: close. The inventory and prune order are C5 and C10, and
        the model-marketplace hard requirement goes to an ADR if the founder
        wants it kept. Self-hosting contradicts ADR-008, so drop that note.
        Tiny-currency waits for a founder decision and goes to an ADR then.
      - #4608: close. The bring-your-own-model rules fold into the
        `first-run-model-connect` spec; `getting-started/` is for testers.
      - #4611 (vault envelope encryption, real code): keep and merge. The
        vault-key-escrow note's decision goes into its design. Rebase after C4
        to drop the mirror half.
      - #4612 (site redesign): keep and merge; it adds no notes.
      - #4605 (merged): `docs/archive/root-scratch/` goes in C1.
        `getting-started/self-hosting.md` shrinks to the README sentence and
        is deleted.

## 4. Done

- [ ] 4.1 Every registry row reads converged, or names an open item with a
      falling ceiling. The reconciler issue shows no duplicate lanes. Sync
      the spec delta and archive this change.
