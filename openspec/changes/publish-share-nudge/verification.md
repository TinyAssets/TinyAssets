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

The affected-test job now uses the queue's `linux_oracle.py --required-runner`
venue and identical container AppArmor profile. Namespace preflight remains
mandatory; selection, shard split and assertions are unchanged. Summaries and
JUnit are exported from the container, including failed runs. The existing host
browser installation remains intact. No publication product behavior changed.

One focused Claude cross-family review found no floor issue and one correctness
issue: **AGREE**, artifact uploads must use `overwrite: true` so a rerun can
replace the earlier shard result. Applied, with a regression assertion. The
earlier publication behavior review remains intact.

The final workflow passes all 27 workflow tests on Windows and all 27 through
the Linux oracle's actual required-runner path (zero failures or skips, valid
JUnit and summary). Plugin regeneration passed its import probe; all 598
canonical files mirror-match. Existing tests are unchanged.

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
Only the workflow, its additive regression test, and this verification record
are changed by the CI repair. No PR or deployment is part of this repair.
