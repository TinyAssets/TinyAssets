# automation-event-triggers

## C2 folded obligations

The following unfinished source tasks retain their acceptance detail; the one target change owns them.

### node-scheduled-wake

# Tasks: node-scheduled-wake

## 1. Build
- [x] 1.1 `once` trigger kind, `not_before` column, CHECK rebuild, due key and retire-after-run (`tinyassets/automations.py`).
- [x] 1.2 `enqueue_branch_run` registers a one-shot automation with `not_before`/`delay_seconds`; retire flag and shape caps (`tinyassets/graph_compiler.py`).
- [x] 1.3 Projection shows `not_before` (`tinyassets/api/automations.py`).

## 2. Prove
- [x] 2.1 Tests through the real pump: delayed wake fires once after `not_before`, self-rescheduling chain, private own branch allowed, cross-user refusals, restart mid-run retries, bounded retries, pending-cap usage limit.
- [x] 2.2 Rewrite `tests/test_node_enqueue_verb.py` node-level cases to the new contract.
- [x] 2.3 Mutation-check the floor and the due key.
- [x] 2.4 gpt-6-astra refute round: cross-user reach and runaway cost.
- [ ] 2.5 Deploy; `python scripts/deployed_sha.py --assert-contains <sha>`.

## 3. Land
- [ ] 3.1 Sync delta into `openspec/specs/user-owned-automations/`, archive.


### owner-message-wakes

# Tasks: owner-message-wakes

## 1. Build
- [x] 1.1 `owner_message` event type, coalescing emitter, converse and consumer-projection emit sites.
- [x] 1.2 Automation create takes `not_before` / `delay_seconds`; resident text and handbook lines.

## 2. Prove
- [x] 2.1 Tests: `tests/test_owner_message_wakes.py`, consumer projection in `tests/test_consumer_public_turn.py`.
- [x] 2.2 gpt-6-astra refute round on the cross-user parts (ACCEPT, 2026-10-01).
- [ ] 2.3 Deploy; `python scripts/deployed_sha.py --assert-contains <sha>`.

## 3. Land
- [ ] 3.1 Sync the delta into `openspec/specs/user-owned-automations/`, archive.


### served-inbound-webhook-triggers

# Tasks: served-inbound-webhook-triggers

## 1. Build
- [x] 1.1 Served `write_graph target=webhook` create/revoke and `read_graph target=webhooks`
      (`tinyassets/engine_mcp_server.py`, `tinyassets/served_tools.py`).
- [x] 1.2 `webhook_hooks.revoke_by_prefix` plus `revoke_webhook` token_prefix
      (`tinyassets/storage/webhook_hooks.py`, `tinyassets/api/webhook_ops.py`,
      `tinyassets/api/extensions.py`).
- [x] 1.3 `next_due_at` in the automation projection (`tinyassets/automations.py`,
      `tinyassets/api/automations.py`).

## 2. Prove
- [x] 2.1 `tests/test_served_webhook_triggers.py` (13) and `tests/test_automation_next_due.py`
      (10). Both are red on the unfixed tree and green after the change. A mutation that
      removes universe confinement in `revoke_by_prefix` turns the cross-universe test red.
- [ ] 2.2 Cross-family review (Tier 2), owed.
- [ ] 2.3 Deploy, then `python scripts/deployed_sha.py --assert-contains <sha>`.
- [ ] 2.4 Live acceptance through the app (steps in the PR body): a schedule fires on
      time and a webhook POST runs as the owner, both with no host online.

## 3. Land
- [ ] 3.1 Sync the deltas into `openspec/specs/`, then archive this change.


### automation-schedule-timezone

# Tasks

- [ ] 1. `user_accounts.timezone` column + `set_account_timezone` /
      `get_account_timezone`, validated against `zoneinfo`.
- [ ] 2. `POST /app/account/timezone`, identity-required, refusing an
      unresolvable name rather than clearing the stored one. (`/app`, not
      `/mcp/app`: #4112 moved the app to `https://tinyassets.io/app` and left
      no alias, so the path is covered by `_is_app_path` and identity-gated by
      default.)
- [ ] 3. App reports `Intl.DateTimeFormat().resolvedOptions().timeZone` on
      sign-in/load.
- [ ] 4. `automations.timezone` + `automations.last_due_local` columns.
- [ ] 5. Resolve the zone at create (passed → account → UTC); refuse an unknown
      name naming the field.
- [ ] 6. `_due_instant` cron: resolve the local slot through the zone, with the
      decided gap/ambiguous policy; dedupe on the local slot.
- [ ] 7. `next_due_at` cron: same resolution, so both surfaces agree.
- [ ] 8. Report `timezone` + a `7:00 AM America/Los_Angeles` schedule string
      wherever an automation is returned or displayed.
- [ ] 9. `write_graph.branches` handbook example shows the zone.
- [ ] 10. Tests: both 2027 transitions for `0 1`/`0 2`/`0 7`, the
      default-from-owner path, override, unknown-zone refusal, and the
      ambiguous-slot single fire.
- [ ] 11. One cross-family review round; fold P0/P1.
- [ ] 12. Sync the spec delta into `openspec/specs/` and archive on land.
