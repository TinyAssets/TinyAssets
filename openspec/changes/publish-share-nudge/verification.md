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
