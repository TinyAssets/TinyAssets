# Muse/pi proposal review and verification

Planning batch: `spec/muse-pi-gap-proposals`, based on `9e96ff95959499cd2ec6fda9e0761b9d498240dd`. This record covers clean-agent-start, command-center-harness-control and connect-anything-ladder. No implementation, deployment, spec sync or PR is claimed. Implementation checkboxes intentionally remain open.

## One cross-family floor/correctness round

Claude reviewed the three drafts plus parent delegation changes through the peer-agents skill, read-only, with no sub-agents or tests. It returned `VERDICT: ADAPT` with seven findings. The drafting agent accepted and addressed all seven; no second review round or claim of reviewer reapproval follows.

| Finding | Disposition and resulting contract |
|---|---|
| Deleted Soul can cause home rebinding | **AGREE.** clean-agent-start now uses authenticated center/home state for first contact, rebinding and scheduling; the deleted-Soul scenario preserves the same home and forbids reseeding. Evidence: first_contact.home_is_complete and api/universe.py's living-home check both used file presence. |
| Personal-file preservation conflicts with D10 predecessor hashes | **AGREE.** The clean profile publishes no predecessor hashes for its three personal files; installed receipts alone select automatic replacement. Parent design and additive delta state the same rule. |
| Lazy soul-edit default is unspecified | **AGREE.** A versioned D10 candidate supplies an editable default permitting owner/agent edits within current delegated rules, with exact history and no extra gate; first-use materialization respects deletion/custom choices and D7 handle retirement. |
| Stdio has no credential-safe authentication path | **AGREE.** Use general scoped egress proxies with broker-side auth injection; raw-secret-only servers are explicitly unsupported in v1, never given daemon secrets in the jail. **Superseded by [connect-anything-ladder/design.md](../connect-anything-ladder/design.md#mcp-transport-semantics):** explicit exact-revision owner opt-in now permits a named own key in a separate server sandbox, with broker-scanned stdout/stderr and a warning naming that server's code and author as the only key readers. |
| Post-login browser can expose storage credentials | **AGREE.** Credentialed contexts are broker-isolated and provide structured actions/sanitized observations without arbitrary evaluation, CDP, cookie/storage/profile export or raw network inspection; unsafe surfaces remain owner-only. |
| Streaming MCP omits its broker prerequisite | **AGREE.** Explicitly depend on broker-streaming-contract, preserving cross-chunk secret scanning, cancellation, backpressure and durable operation reconciliation. No bypass transport. |
| Same-scope third-party UI updates inherit grants | **AGREE.** Pin exact revision/content hash. New third-party code needs renewed owner permission unless the owner explicitly granted auto-updates from that authenticated author within a recorded ceiling; incoming code cannot declare its own permission. |

The reviewer found delegation consistent: D10 transactions; starter renderer cutover; D7 memory/history/editor; D8/D9 installation/main selection; D5 browser substrate; D6 ta; inline-connect-and-approve request authority. Parent design links record these boundaries, and canonical as-built specs remain unchanged.

## Verification

- Strict OpenSpec validation passed for all three new changes and the three edited parents: starter-seed-lifecycle, composable-ui-experiences and universe-agent-harness. Artifact status reports proposal/design/specs/tasks complete for all three new changes.
- Task counts: clean-agent-start 8, command-center-harness-control 10, connect-anything-ladder 10. Normative scenario counts after review: 8, 11 and 11.
- `python scripts/openspec_flow.py check-change <name> --provider codex`: ALLOWED for each draft. Existing command-center-publish-repair missing tasks warning is unrelated; no implementation claim was added.
- Windows: `python -m pytest tests/test_openspec_flow.py tests/test_test_hygiene_gate.py -q -o addopts= --basetemp <external TEMP>/muse-pi-proposals-pytest` — **65 passed**, 0 skipped.
- Linux oracle: `python scripts/linux_oracle.py -- tests/test_openspec_flow.py tests/test_test_hygiene_gate.py -q -o addopts=` — **65 passed**, 0 skipped, Python **3.11.16**, uid 1001, bubblewrap 0.12.0. First attempt failed during tar snapshot with a file-change race before tests; the stable retry passed. All pytest temp roots were outside the repository.
- No test names, assertions, gate files or Python files changed. Touched-Python Ruff has an empty input set; no unrelated whole-repo lint run. No tinyassets edits, so plugin mirror regeneration is not applicable. `tinyassets/onboarding/app.html` is untouched.
- `git diff --check` passed. Committed-tree hygiene with `--base $(git merge-base origin/main HEAD) --head HEAD --body x` passed: **0 added, 0 removed, 0 tampering findings, 0 product lines added**; rerun on the final amended HEAD before push.

These are documentation/workflow checks, not evidence that the proposed runtime behavior is implemented or live. Deployed-SHA and real-user acceptance remain explicit future tasks in each proposal.


## Final fix round (3 of 3), PR #4466

Read the full round-2 review of `5a3bc0dc05` (`review-4466-r2.md`). This is the requested docs/spec correction round, not a new review or implementation claim.

- **AGREE ? raw-key stdio isolation and output custody.** connect-anything-ladder, universe-agent-harness and command-center-harness-control now require a separate server process/user/filesystem sandbox, mediated stdio-only access from agent/hooks/extensions, and no server /proc, environment, argument or file reads. Reuse the broker scanner for stdout and stderr, including split-chunk secrets, before model context, logs or transcript. The protected warning names that server's code and author as the only key readers. Exact-revision explicit owner opt-in remains required; broker injection remains the default. Normative hostile-read/output scenarios and existing implementation tasks carry these checks.
- **AGREE ? classification write authority.** request-continuations and harness-control require the protected owner-authenticated rule-write surface and exact-revision owner approval. Package/template/saved-connector/author/agent classifications are inert proposals; bearer-only writes and code-update grants confer no classification authority. The send_payment-as-read scenario covers standing-read-grant escalation and stale approvals.
- **AGREE ? historical wording.** The old unsupported-in-v1 row above now links to its superseding isolated-server opt-in contract.

Validation for this final docs-only round:

- All 15 OpenSpec changes touched by the PR passed `openspec validate <name> --type change --strict --no-interactive`.
- Windows `python -m pytest tests/test_openspec_flow.py -q`: **15 passed**. Linux `python scripts/linux_oracle.py -- tests/test_openspec_flow.py -q`: **15 passed**, 0 skipped (Python 3.11.16, uid 1001, bubblewrap 0.12.0).
- `python scripts/concerns_index.py --check` and `git diff --check`: passed.
- Ruff on the OpenSpec/concerns scripts and OpenSpec tests: passed. Repository-wide Ruff reports **58 existing violations in untouched Python files**; no Python files changed in this round.
- PR hygiene: **0 removed / 0 tampering**, no test or gate edits. Rechecked against the final committed tree before push.

Implementation acceptance remains pending in the existing task boxes; this round neither deploys runtime code nor syncs proposed behavior into as-built specs.
