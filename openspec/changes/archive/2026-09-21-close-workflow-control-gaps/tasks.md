## 1. Shape and reproduction

- [x] 1.1 Complete independent shape/basic-safety review of September21 model-policy follow-up; Fable3265 APPROVE, adaptations in docs/reviews/2026-09-21-served-node-policy-shape.md; prior lifecycle review retained.
- [x] 1.2 Add failing actual served-route/owned-persistence policy-edit regressions; prior five-gap reproductions retained. 2026-09-20 Windows, venv py3.14: three new tests in tests/test_engine_mcp_write_graph_patch.py red against HEAD 94725f49 server (3 failed / 1 passed -k "llm_policy or allowlist"), green after the change.

## 2. Implement

- [x] 2.1 Expose existing node llm_policy replacement/clear through served update_node, preserving canonical validation and provider/owner authority. 2026-09-20: `_SERVED_PATCH_UPDATE_NODE_ALLOWED` gains llm_policy only; grammar delegated to `_coerce_llm_policy_update`; connect_compute + write_graph guidance distinguish pin edit from connecting a provider. Focused run: test_engine_mcp_write_graph_patch + test_llm_policy_pin + test_llm_policy_override + test_run_failure_model_advice = 53 passed, 0 skipped; ruff clean; plugin mirror rebuilt. Not yet live-proven.
- [x] 2.2 Pass trusted graph ancestry to workspace resolution and prove discard plus sibling exclusion through dispatch.
- [x] 2.3 Expose exact, bounded owned-run output reading through existing graph handles.
- [x] 2.4 Record failed code-node identity and terminal state without mislabeling parallel siblings.
- [x] 2.5 Expose scoped queued/running cancellation with accurate pending and terminal readback.
- [x] 2.6 Close the live-retest follow-up: record the actually cancelled code node as terminal without relabeling completed or unstarted siblings.

## 3. Verify and deliver

- [x] 3.1 Pass focused Windows and Linux baseline/candidate comparisons, lint, mirror parity and exact-head independent review. September21 follow-up: root53Windows/53Linux,0skips; Claude red/green route regression, Ruff/mirror496/strictspec; exact63cf7739 root approval after Fable shape review.
- [x] 3.2 Merge through normal CI guards, deploy and pass authenticated canary plus deployed-SHA containment. PR3900 merged5394efd; required35556481547/image35557923773/deploy35558095890 passed; public handles03:37:49, protectedcontains03:37:51UTC.
- [x] 3.3 Send the exact ordinary retest prompt and obtain the webapp agent's explicit message that all five gaps are closed; fix and redeploy if it finds a blocker. September21: exact retest20:38PDT; original model edit20:46; explicit five-control verification20:56 says All five are working / Nothing is blocked. Receipt includes exact runs and limitations.
- [x] 3.4 Sync shipped specs, record rendered/organic evidence and archive this completed delivery slice while preserving unfinished broader limits work. Canonical lifecycle requirement equals complete delta; no fresh owner-originated clean use claimed. September21 archive; underlying parallel reliability, effect-edit scope, full cloud lifecycle and onboarding remain separate.

September21 model-policy follow-up is live-accepted: app20:46PDT changed
original1271c2f748cd in place, runb8a6b2af938b48c5 completedOK, no replacement.
See 2026-09-21-served-node-policy-deployment.md (archived in git). Historical five-gap
proofs contained individual acknowledgments but no consolidatedall-five statement;
20:56PDT response now supplies it. Current closeout branch
codex/served-model-edit-closeout; no operator private workflow edits. Broader
resource and parallel reliability work stays open independently.
