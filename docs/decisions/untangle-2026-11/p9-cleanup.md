# p9-cleanup as built (2026-11-21)

`untangle/p9-cleanup` is P9's OpenSpec cut (`pr_merge_plan` item 3, whose other half, P7's
workflow headers, is already ticked) and carries the move of `untangle/r8-lost-copy` to "After
the cutover" (Lane R after #5674, 2026-11-17).

1. **Deleted: the archive and 72 of the 85 old changes.** 1,834 files, 139,517 lines: the
   archive's 229 folders (1,376 files) and 458 files of open changes, each one no test claims an
   id of. Main's commit `88e74e1cd` holds them all. Their 73 `platform/cutover.json` entries
   (the archive's among them) go with them, since the manifest lists only what the tree holds.
2. **Kept for now: the 13 old changes whose ids the old suite's tests claim.**
   `owner-daemon-in-box`, `shape-convergence`, `n1-inbound-door`, `box-subscription-sources`,
   `model-connect-by-discovery`, `post-c6-multiplayer`, `n3-realtime-model-sessions`,
   `n9-model-media-results`, `starter-agent-out-of-plumbing`, `owner-network-egress-policy`,
   `owner-box-snapshot-undo`, `c12-knowledge-files-export` and `test-suite-follows-shape` hold
   the `verifies` ids of 1,763 of `tests/`' 2,226 tests. Deleting them fails the scope guard
   (`test_hygiene_gate.py`, "spec retires an id still named", 1,907 findings on a full
   delete), which runs from the base, so no PR can lift it for itself. It would also arm the weekly
   `test-shape-prune.yml` to propose deleting those tests. list3 P5 already put the id rule
   (`test_shape.py`, `test-shape-prune.yml`) on the cutover's delete list, and
   `untangle/p6-tests-beside` gives the id check its own step. So the rule goes first, brought
   forward (`untangle/p9-ids`, after p6-tests-beside), and then the 13 changes go
   (`untangle/p9-openspec-rest`). Their manifest entries name both branches.
3. **Readers that stay, re-pointed.** Each citation of a deleted change in a file that is not a
   decision record links to it at `88e74e1cd`, so the evidence still opens. There are 41
   citations in 28 files: 20 concern notes, `docs/host-actions.md`, three `docs/ops` pages, two
   old specs, `ideas/INBOX.md` and `shape-convergence`'s tasks. `CONTRIBUTING.md` and
   `docs/architecture.md` pointed at `rename-universe-to-command-center` as the rename's plan.
   That plan is gone, so they now say that the old stack keeps the name until the cutover. The
   S21 row names only `owner-daemon-in-box`. Its ceiling (2) only falls, so it needs no edit.
   The `full-channel-access` sentence in `http-connections-and-outbound-authority` said that the
   change would join the spec. It now says that the change was deleted unbuilt.
4. **`check_shapes.py`'s `adr-has-spec` goes now, not at the cutover.** It failed 14 accepted
   ADRs whose only cited spec was a deleted change (ADR-009, 012, 017 to 019 and 023 to 031).
   list3 P4 decided that the check goes with `check_shapes.py`, and that the ADRs that still
   stand keep their text, with citations of deleted specs left as history (ADR-005: an ADR's
   body is not edited after acceptance). Its subject left early, so it does too. No test
   held it.
5. **Left, with the reason.**
   - Decision records (`docs/decisions/`) keep their citations as history (ADR-005), and so does
     `p9-now.md`'s plan line.
   - `tinyassets/` docstrings, `tests/` docstrings and a fixture README, the `Dockerfile`,
     `deploy-prod.yml` and `dr-drill.yml` comments, and `scripts/cloud_only_preflight.py` and
     `deployed_sha.py` name a deleted change by its name or path. Each is old stack that the
     manifest deletes in the window, and none reads the file.
   - `check_shapes.py` `HISTORY_DIRS` and the archive handling in `adr_superseded_open` and
     `test_shape.py` stay. `openspec archive` writes `openspec/changes/archive/` for any change it
     closes, `clean-sheet-platform` included, so these read the tool's own layout and are not
     dead code.
   - `openspec/specs/` stays. The manifest deletes it at the cutover, and 504 of the old suite's
     tests claim 58 of its ids (list3 D5).
6. **Gates.** `openspec validate clean-sheet-platform` passes. `check_shapes.py` is clean,
   and so are the hygiene gate against main, `validate_skills.py`, the background-authority
   inventory, the context budget and `cutover.test.ts`. The pytest files `test_shape_registry.py`,
   `test_live_docs_reference_real_scripts.py` and `test_test_shape.py` pass (42 tests).
   Mutations: restoring one concern's old path fails `check_shapes.py`
   (`deleted-path-reference`); restoring one deleted change's manifest entry fails
   `cutover.test.ts` ("is not there"); deleting `test-suite-follows-shape` as well fails the
   hygiene gate (7 findings).
