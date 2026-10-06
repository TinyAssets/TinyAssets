---
severity: P1
title: The deploy workflow no longer satisfies ~25 uptime-and-alarms spec requirements
filed: '2026-10-02'
summary: since #2442's fail-safe rewrite, deploy-prod.yml lacks markers, an always-run terminal receipt, issue truth-classification, an Access-gate check, exact canary name, pre-pull disk guard and rollback ancestry proof that openspec/specs/uptime-and-alarms still requires; per-requirement restore-or-retire decisions owned by deploy-incident
---

# The deploy workflow no longer satisfies ~25 uptime-and-alarms spec requirements

**Filed:** 2026-10-02 by the test-hygiene lane, while separating the 55
heavy-listed `tests/test_deploy_prod_workflow.py` failures into retired
design and still-required behaviour.
**Owner:** deploy-incident (lead decision 2026-10-02).
**Severity:** P1. The as-built behavioural spec and the production deploy
disagree, and the failing tests were the only thing that said so. Those tests
are heavy-listed, and their job gated nothing.

## The finding

#2442 replaced `.github/workflows/deploy-prod.yml` with a fail-safe
single-daemon deploy (`deploy/deploy_fail_safe.sh`). `openspec/specs/uptime-and-alarms/spec.md`
(cited below as **U**) still specifies the earlier deploy's observable
guarantees. Some examples:

- first-host-mutation and image markers (U:323-325);
- an always-run terminal receipt after rollback, with a pure classifier and an
  atomic writer (U:337-395);
- truth classification of the deploy-failed issue (U:450-477);
- bounded prior-receipt capture (U:279);
- a direct-origin Access-gate check;
- an exact `--assert-name TinyAssets` canary;
- a pre-pull disk guard;
- an ancestry proof for the rollback image: W:72/128 declare the stop-writer
  floor, but the rollback does not prove it.

Each requirement needs one decision: **restore** it in the current workflow's
shape, or **retire** it and sync the spec. Leaving both as they are makes the
spec false.

## Cross-family classification

Codex (gpt-6-astra), 2026-10-02, against main `118ef3e5f`: **34 SPEC, 8
RETARGET, 13 RETIRED**. W is deploy-prod.yml and D is deploy_fail_safe.sh.

Since then, #4260 restores the request-idempotency and agent-interchange HMAC
rows, and the 3 scrub, 2 host-owned-destination and rollback-identity rows. The
unsafe-fence rows follow `2026-08-27-unsafe-fence-recovery-path-deleted.md`
(KEEP). The 13 RETIRED rows' tests were deleted 2026-10-06, and 4 SPEC rows
(Access gate x2, candidate diagnostics, manual image source) pass on main and
left the table; the RETARGET rows are handled by test-hygiene's plan-B PRs.

| test | verdict (RETIRED/SPEC/RETARGET) | evidence (file:line) | one-line reason |
|---|---|---|---|
| `test_workflow_dispatch_has_explicit_request_hmac_rotation_input` | SPEC | `docs/concerns/2026-10-01-deploy-dropped-request-idempotency-hmac.md:16` | Explicit RESTORE decision includes incident rotation; the dispatch input disappeared. |
| `test_manual_unsafe_fence_recovery_is_separate_and_source_bound` | SPEC | `docs/concerns/2026-08-27-unsafe-fence-recovery-path-deleted.md:15`; `W:27` | KEEP per your instruction; current dispatch has no source-bound recovery job. |
| `test_manual_image_tag_is_env_bound_and_validated_before_use` | RETARGET | `W:84`, `W:88`, `W:99` | Target **Resolve image tag -> immutable digest**; environment binding and tag validation remain. |
| `test_resolved_digest_is_canonical_before_any_host_write` | RETARGET | `W:84`, `W:109` | Target **Resolve image tag -> immutable digest**; canonical digest validation remains, with different error wording. |
| `test_capture_previous_uses_configured_and_running_digest_observations` | SPEC | `U:276`, `U:286`; `W:166` | Current capture selects running identity with a fallback; it does not independently establish configured/running agreement. |
| `test_capture_previous_transports_bounded_prior_receipt_read_only` | SPEC | `U:279`, `U:293`; `W:162` | Bounded prior-receipt capture remains specified but is absent from current capture. |
| `test_capture_previous_does_not_emit_untrusted_image_labels_as_outputs` | RETARGET | `W:162`, `W:172`, `W:178` | Target **Capture current image (for public-canary rollback)**; it emits no raw revision labels. |
| `test_canary_step_only_probes_canonical` | RETARGET | `W:365`, `W:371` | Target **Public MCP canary (--assert-handles)** explicitly; substring matching currently selects the capture step. |
| `test_rollback_step_present` | RETARGET | `W:335`, `W:386`; `D:1446` | Target **Run fail-safe deploy on the droplet** and **Roll back if the public canary is red**. |
| `test_rollback_runs_always_and_eligibility_keys_to_image_marker` | SPEC | `U:334`, `U:337`; `W:387` | Internal rollback does not replace the specified always-running, marker-aware terminal handling. |
| `test_disk_preflight_runs_before_deploy_image_pull` | SPEC | `W:446`; `D:1293`; `tests/test_deploy_prod_workflow.py:763` | Conservative: post-canary retention and pull-before-swap do not establish a pre-pull disk-capacity guard. |
| `test_deploy_scrubs_stdio_only_workflow_universe_from_cloud_env` | SPEC | `tinyassets/storage/__init__.py:279`; `W:488` | `TINYASSETS_WIKI_PATH` remains a live override; current credential cleanup does not remove this cloud misconfiguration. |
| `test_deploy_scrubs_and_fails_closed_on_shared_request_hmac_duplicate` | SPEC | `docs/concerns/2026-10-01-deploy-dropped-request-idempotency-hmac.md:34` | The daemon-only key’s shared-env leak guard is explicitly included in RESTORE. |
| `test_deploy_preserves_host_owned_backup_destination` | RETARGET | `W:488`; `deploy/retire_platform_llm_logins.sh:73`; `U:939` | Target **Retire platform LLM logins from the host**, including its helper; current cleanup preserves `BACKUP_DEST`. |
| `test_deploy_preserves_host_owned_log_destination` | RETARGET | `W:488`; `deploy/retire_platform_llm_logins.sh:73`; `deploy/ship-logs.sh:62` | Target **Retire platform LLM logins from the host**, including its helper; `LOG_DEST` remains required and untouched. |
| `test_production_marker_is_immediately_before_first_scrub_host_write` | SPEC | `U:323`, `U:334`; `W:210` | Preserve the first-host-mutation marker requirement, reanchored to today’s first mutation rather than the deleted scrub. |
| `test_image_marker_is_immediately_before_first_tinyassets_image_write` | SPEC | `U:325`; `D:1417` | Image mutation moved inside the script, but the specified observable marker was dropped. |
| `test_rollback_and_terminal_receipt_are_ordered_under_always` | SPEC | `U:337`, `U:360`; `W:408` | Success-only publication omits the required post-rollback terminal record on failure. |
| `test_daemon_deploy_owns_exact_public_server_name_assertion` | SPEC | `W:371`; `scripts/mcp_public_canary.py:669`; `docs/ops/anthropic-connector-catalog-submission.md:51` | `--assert-handles` does not imply `--assert-name TinyAssets`; the exact-name assertion disappeared. |
| `test_terminal_receipt_keys_to_production_marker` | SPEC | `U:323`, `U:334`; `W:408` | Terminal publication must follow any production mutation, including failures before image replacement. |
| `test_rollback_emits_safe_defaults_and_final_outputs_before_exit` | SPEC | `U:337`; `W:351`, `W:404` | Current deployment outputs do not provide the specified safe rollback tuple, especially for public-canary rollback. |
| `test_rollback_identity_failure_preserves_the_passed_canary_tuple` | SPEC | `U:356`, `U:511` | Canary success and failed identity proof remain distinct observations; current rollback lacks this classification. |
| `test_terminal_receipt_invokes_pure_helper_and_preserves_atomic_writer` | SPEC | `U:360`; `W:419`, `W:426` | Both the pure classifier and atomic sibling-file replacement remain required; direct installation supplies neither contract. |
| `test_terminal_receipt_never_mutates_the_deployed_image_after_publication` | RETARGET | `W:386`, `W:407`, `W:426` | Target **Publish release-state receipt** by name; rollback precedes it and its publication code performs no image mutation. |
| `test_terminal_writer_outputs_are_visible_before_fallible_work` | SPEC | `U:367`; `W:407` | The current writer emits no safe terminal-publication defaults or bounded classification outputs. |
| `test_terminal_canary_output_preserves_the_raw_applicable_canary` | SPEC | `U:375`; `W:420` | Required forward-versus-rollback canary observations were replaced by a success-only hardcoded receipt. |
| `test_forward_green_terminal_identity_failure_stays_red_after_publication` | SPEC | `U:395`, `U:549`; `W:429` | Receipt readback does not establish the required terminal identity agreement or reject its absence. |
| `test_deploy_failure_issue_consumes_rollback_and_terminal_outputs` | SPEC | `U:450`, `U:475`; `W:516` | The issue consumes forward deploy outputs, omitting final rollback and terminal-publication evidence. |
| `test_deploy_failure_issue_rejects_partial_or_contradictory_tuples` | SPEC | `U:464`, `U:477`; `W:516` | Missing or contradictory evidence must remain unproven; current issue wording performs no such classification. |
| `test_deploy_failure_issue_has_truthful_bounded_wording` | SPEC | `U:450`; `W:516`; `D:117` | The unconditional “NOT left at zero” claim contradicts explicit rollback-failure/manual-intervention outcomes. |
| `test_deploy_requires_and_installs_agent_interchange_hmac_secret` | SPEC | `deploy/DEPLOY.md:134`, `deploy/DEPLOY.md:141`; `W:145` | Yes: deleting the repository secret must block deployment, and daemon-only installation/rotation remains documented. |
| `test_deploy_requires_and_installs_daemon_request_idempotency_hmac_secret` | SPEC | `docs/concerns/2026-10-01-deploy-dropped-request-idempotency-hmac.md:27` | Explicit RESTORE covers validation, daemon-only installation, mismatch refusal, and controlled rotation. |
| `test_unsafe_recovery_validates_both_hmac_prerequisites_before_mutation` | SPEC | `deploy/DEPLOY.md:119`, `deploy/DEPLOY.md:141`; `tests/test_deploy_prod_workflow.py:1705` | The recovery path you require keeping must retain both HMAC prerequisites before mutation. |
| `test_disk_preflight_precedes_every_remote_image_pull` | SPEC | `D:1293`; `W:446`; `tests/test_deploy_prod_workflow.py:1787` | Conservative: no replacement pre-pull capacity check exists; post-deploy retention does not prove equivalent protection. |
| `test_stop_writer_deploy_proves_exact_safe_image_and_drains_old_ids` | SPEC | `U:312`; `W:112`, `W:128` | Fleet draining is retired, but this mixed test also protects digest-bound revision/ancestry proof, which remains missing. |
| `test_stop_writer_blocks_unsafe_rollback_image` | SPEC | `W:72`, `W:128`, `W:389`; `D:1479` | The security floor remains declared, but current rollback uses the captured previous image without proving its ancestry. |
| `test_terminal_never_reports_deployed_without_exact_cleanup_restoration` | SPEC | `U:1010`, `U:1030`; `W:420` | Conservative: current spec still requires separate cleanup truth and running-state proof; wholesale deletion would erase that contract. |
| `test_recovery_canary_waits_for_the_daemon_instead_of_probing_instantly` | SPEC | `tests/test_deploy_prod_workflow.py:1987`; `docs/concerns/2026-08-27-unsafe-fence-recovery-path-deleted.md:15` | KEEP recovery’s startup wait; ordinary deploy’s health loop does not restore the missing recovery job. |

## Resolve by

For every SPEC row, the deploy-incident lane either restores the requirement
in the current workflow, which makes the quarantined test pass and drops its
ledger line, or retires it. Retiring means syncing
`openspec/specs/uptime-and-alarms/spec.md` and deleting the test with
`Test-Removal: retired`. Delete this file once no SPEC row is left.
