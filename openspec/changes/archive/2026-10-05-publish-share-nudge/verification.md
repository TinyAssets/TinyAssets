# Verification

## Cross-family review

Claude peer review, 2026-10-05, one round: no floor findings. Three correctness
findings adjudicated:

1. **AGREE**: do not render while holding the publish claim. Metadata is saved in
   the completed pin first; request resolution then enriches the preview.
   `test_finished_publish_retries_resolution_without_republishing` asserts the
   pin is `activated` inside the renderer.
2. **AGREE**: old completed pins lack `completion`. Recovery reconstructs it from
   the immutable definition and resolves the original request without publishing
   again. The same parameterized test exercises an actual pin with the field removed.
3. **DISAGREE_EVIDENCE**: a direct idempotent replay is not an update. In
   `custom_agents._publish_normalized`, the same author/key returns the existing
   definition after verifying the fingerprint; a different key creates a new
   immutable listing. No update relationship is implied by matching names or
   component ancestry (which can be remix attribution). Explicit package versions
   and release successors are classified as updates. The direct replay test asserts
   identical listing, version and original change kind; package and release tests
   prove both actual update paths.

## Boundary mutation

| Guard removed temporarily | Test | Result |
|---|---|---|
| Public definition author equals authenticated preview caller | `test_publication_preview_selector_is_owner_scoped` | Failed with Bob receiving a screenshot; guard restored before final runs |

## Delivery scope

Commit and push only; no PR or deployment requested. The onboarding app HTML is
unchanged. Specs describe the as-built public fields and preview selector.

## Final checks

- Windows: **479 passed, 8 skipped** (existing POSIX symlink and Linux PID-namespace
  cases), 289.41 seconds. All new publication, real-browser and scripted-turn tests pass.
- Linux oracle: **486 passed, 1 skipped**, Python 3.11.16, uid 1001, bubblewrap
  0.12.0, 270.70 seconds. The sole skip is the Windows directory-junction test;
  the Linux containment and symlink proofs all execute and pass.
- Files in both runs: `test_publication_completion`, `test_share_after_publish_skill`,
  `test_connect_skill`, `test_ui_preview`, `test_custom_agents`, `test_pending_requests`,
  `test_universe_bundle`, `test_in_platform_agent_systems`, `test_command_center_packages`,
  `test_command_center_publish_intent`, `test_command_center_release_surface`,
  `test_agent_node`, `test_publish_is_discoverable`, `test_universe_server_isolation`,
  `test_first_contact`, and `test_mcp_instruction_surfaces` (all under `tests/`, `.py`).
- Ruff passes for all changed Python; OpenSpec strict validation passes.
- Plugin mirror rebuilt with import probe; 598 canonical files match.
- Hygiene: **15 test functions added, 0 removed, 0 tampering findings**.
- All pytest temporary roots are outside the repository. Only explicit paths staged.

## CI repair: job 111610269337

The starting branch matched origin at `05e3ea2c45c41dbdb17d80a28ef8fa157ba5076f`.
The failed PR affected-test job installed Chromium but ran directly on Ubuntu,
without the permitted bubblewrap PID namespace required by `ui_preview._supervised`.
The publication correctly remained committed and reported its preview unavailable;
the real-render assertion correctly rejected that result.

Reproduction: the unchanged real publication test passed on Windows and the
Python 3.11.16 Linux oracle (one pass each). Running it with the oracle's
`--no-bwrap` restricted venue reproduced the exact `unavailable != ready`
failure (one failure). No renderer substitute or test edit was used.

**Resolution (Claude lead, 2026-10-05):** the earlier repair changed the
affected-test job to the Linux oracle venue (about 47 workflow lines plus a guard
test). That is a global CI change, so it was reverted here and moved to root's
own infra PR (`ci/affected-tests-linux-oracle-queue`). In this PR, the real-render
case skips only when the preview is unavailable AND `bwrap` is absent (the bare
affected-test runner), with an owner/expiry reason matching
`tests/test_ui_preview.py`. It stays `real_browser`-marked and in
`real-browser-proof.yml` paths, which runs it in the Linux oracle and fails on any
skip. Verified: 1 passed in the oracle (bwrap 0.12.0, uid 1001). No publication
product behaviour changed.

Expanded Linux run: **593 passed, 1 skipped** in 354.55 seconds, Python 3.11.16,
FastMCP 3.4.8. The only skip is the Windows directory-junction case. This runs
the 16 files listed above plus `test_served_tool_guidance`, `test_tests_workflow`,
`test_linux_oracle` and `test_linux_jail_proof_workflow`. All 12 publication cases
and all 19 served-guidance cases pass, including the real-render pixel assertion.

The initial expanded Windows run used the host's FastMCP 3.2.0 and returned
585 passed, 8 platform skips, 1 failure: its tool description retains `Args:`
and exceeded the existing 12,000-character guidance assertion (13,935).
The Linux dependency is FastMCP 3.4.8, which extracts parameter descriptions.
Windows verification therefore uses an external isolated venv with FastMCP
3.4.8; the host installation, assertion and guidance remain unchanged.

Final expanded Windows run with that venv: **586 passed, 8 skipped** in
1044.25 seconds, across the same 20 files as Linux. Skips remain the existing
POSIX symlink and Linux PID-namespace cases. Ruff and diff whitespace checks
pass. Hygiene against the PR base: **16 tests added, 0 removed, 0 tampering**.
The CI repair now changes only the render case's bubblewrap gate and this
verification record; the workflow change lives in the separate infra PR. No PR or deployment is part of this repair.


## Async approval repair: merge-group 37266089374

Starting HEAD and origin both matched `16f48cfa4879840d9a664bc3fedf26846295d4df`
after the requested fast-forward pull. The root timing comparison and PR comment
5988697479 identify 3950.639 summed test seconds versus the unchanged 3000-second
budget, compared with 2596.322 before this feature. Code tracing confirmed that
`receipt_completion` invoked the real renderer synchronously inside approval.

Approval now commits and resolves before admitting background preview work. Tests
hold rendering behind an event to prove the approval returns with a durable pending
answer, then release it and assert the image and second completion wake. Existing
privacy, immutable-content, failed-render and legacy-pin tests still run; the real
browser test waits for the actual stored completion and retains its pixel assertion
and original venue gate. Failed request resolution now starts no render, so the
retry test correctly expects one render after successful resolution, not two.

Final coverage: the same 19 modules ran on Windows (external share-ci-venv,
FastMCP 3.4.8) and Linux oracle (Python 3.11.16, bubblewrap 0.12.0, uid 1001).
The modules include all four PR-touched test files, publication packages/intent/
release/discovery, update_executor, picker, served guidance, preview, custom agents,
pending requests, bundle, in-platform systems, isolation, first contact, MCP
instruction surfaces and the connect skill. JUnit and logs stay outside the repo
in `C:/Users/Jonathan/Projects/publish-async-*`; pytest basetemp is external on
Windows and `/tmp/b` in the oracle.

Ruff passes across all PR-touched Python plus the receipt store. Strict as-built
spec validation passes. Plugin rebuild/import probe and parity pass (598 canonical
files). Hygiene on an index snapshot: 17 added / 0 removed / 0 tampering against
the PR base; this repair adds 2 / removes 0 / tampers with 0. No budget or gate
changes. The prior review round remains the existing round; the root comment
explicitly requests no fourth review. Delivery remains commit/push only.


Linux final result: **588 passed, 1 skipped**, 376.36 seconds. The only skip is
the existing Windows directory-junction case. The `real_browser` publication
case passed (4.906s including setup/teardown; 4.16s call), without a skip; the
stored completion supplies a PNG with the expected `(32, 80, 192)` pixel.

Historical merge-group JUnit times versus this Linux oracle's JUnit times
(setup + call + teardown, seconds):

| Existing fixture/test | Before | After |
|---|---:|---:|
| update_executor: identical screen then next release | 12.951 | 2.700 |
| update_executor: settlement sweep failure | 12.724 | 2.391 |
| picker: both doors offer two latest packages | 10.535 | 1.021 |

Across all 81 matching update_executor/picker cases in the root comparison,
summed time fell from **530.127s to 129.881s** (75.5%). This is a historical CI
versus local-oracle comparison, not a claim to have rerun the full merge-group
budget gate. Several unrelated oracle suites were active on the local host.
The causal proof is the held-render test: approval resolves and returns while
the renderer is still waiting, with no fixture renderer bypass or budget change.
Timing rows: `C:/Users/Jonathan/Projects/publish-async-timing-comparison.json`.


Windows final result: **581 passed, 8 skipped**, 767.13 seconds. The existing
POSIX symlink / Linux PID namespace skips remain; the real-browser publication
case passes on Windows too. Both full runs exited 0. This repair's focused
non-browser run also passed: **17 passed, 1 deselected**, 29.23 seconds.
All requested modules and all PR-touched tests passed on both platforms.
