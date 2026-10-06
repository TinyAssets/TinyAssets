# L2 verification

## PR #4502 CI repair (2026-10-05)

- Merged origin/main before repair without conflicts; fetched and merged again
  before the repair push (already up to date).
- Read full failed-job logs for 112056125901, 112056125935 and 112056125608.
  They contain nine failures: all seven native-auth parameter cases, the
  coordinator-guard fixture, and HTTP 402/account capacity scope.
- These tests encoded the deliberately replaced global quota key: native-auth
  mocks now require owner-1, the coordinator fake carries its principal receipt,
  and HTTP capacity reads owner rather than the unowned bucket. All retry,
  refusal and zero-spend assertions remain; another-owner cooldown must be zero.
- Linux oracle command: `MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q
  tests/test_provider_served_router.py tests/test_agent_workflow_fences.py
  tests/test_interactive_http_agent.py tests/test_provider_quota_scope.py
  tests/test_provider_work_authority.py tests/test_provider_retry.py
  tests/test_converse_turn_cost.py --basetemp /tmp/b`.
  Result: **193 passed, 1 skipped**. The existing true-Codex integration requires
  TINYASSETS_REAL_CODEX_TEST_UNIVERSE and TINYASSETS_REAL_CODEX_TEST_SNAPSHOT;
  neither is configured. This skip is not a pass and no skip was introduced.
- Ruff passed for all canonical Python/tests changed relative to origin/main.
  Plugin rebuild and import probe passed with no mirror diff. Static prompt
  budgets and guidance are unchanged. No production code change was necessary.
- Claude repair review: **APPROVE**, no correctness/floor findings; see review.md.
  This supplements the original implementation review below.

## Original implementation verification

All runs used `MSYS_NO_PATHCONV=1 python scripts/linux_oracle.py -- -q <files>
--basetemp /tmp/b`, with Python 3.11, bubblewrap and uid 1001. No tests were
skipped, weakened or xfailed.

| Slice | Passed | Coverage |
|---|---:|---|
| Request URLs | 94 | request_fields_are_answerable, pending_requests, request_card_layout_and_links |
| Owner cooldowns | 716 | New A/B scope test, providers, provider_stream_and_classify, free_model_sibling_retry, one_path_for_every_account, run_model_refusal_and_pins, daily_source_pooling, daily_cap_stops_same_source, free_account_run_provider_parity, learning_never_locks_out, mixed_agent_execution, provider_model_refusal, served_model_preferences, provider_jail_policy, platform_has_no_llm, provider_admission, provider_allowlist, provider_router_diagnostics, provider_slot_transfer, provider_sync_queue_deadline, provider_tool_wait_evidence; affected heavy files provider_work_authority and provider_retry |
| Inference accounting | 323 | request_budget_broker, request_usage_store, parent_turn_request_budget, converse_turn_cost, served_branch_create_errors, agent_review, text_only_review_provider |
| Final review corrections and affected callers | 505 | provider_quota_scope, providers, bug029_chain_drain, provider_router_bug029, daily_source_quota, broker_server, broker_process, outbound_http_connection, outbound_proxy_startup_diagnosis, a_failed_turn_says_what_actually_happened, loop_telemetry, provider_universe_jail, turn_interrupt, provider_jail_policy, request_budget_broker, converse_turn_cost, served_branch_create_errors |

Each coverage name denotes `tests/test_<name>.py`. Counts are per run, with
intentional overlap after changes. One supplemental invocation stopped before
collection because the oracle's source tar saw a changing tests directory; the
final 505-test invocation reran it after edits/review activity stopped.

- Ruff on every changed canonical Python/test file: passed.
- `python packaging/claude-plugin/build_plugin.py`: passed, import probe passed;
  commit hook verified every staged canonical file against its mirror.
- `python scripts/test_hygiene_gate.py --base origin/main --head HEAD`: 11 added,
  **0 removed / 0 tampering** after the implementation commits.
- `openspec validate platform-caps-and-quota-scope --strict`: passed; delta
  synced into `openspec/specs/platform-check-scope/spec.md`.
- `git fetch origin main` followed by `git merge origin/main`: already up to
  date, at `b945fb3b3a` on 2026-10-05.
- Claude review: **ADAPT**, all three findings **AGREE**, fixed and covered by
  the final run. See `review.md`; no second round.

Draft PR #4502 still needs a trusted final merge-review receipt. This lane did
not merge or deploy; there is no deployed-SHA claim or real-user app pass. The
OpenRouter concern remains open for the founder's actual review replay.
