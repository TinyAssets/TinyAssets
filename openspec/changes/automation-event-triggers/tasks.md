# Tasks: automation-event-triggers

## 1. Build
- [x] 1.1 `event` trigger kind, `event_type`/`event_filter_json` columns, CHECK rebuild, registration validation (`tinyassets/automations.py`).
- [x] 1.2 Emitter that stores a `once` wake per matching subscription, stamped with the causing principal (`tinyassets/automation_events.py`).
- [x] 1.3 Emit `run_completed` on the terminal transition and from the boot sweep (`tinyassets/runs.py`); emit `pending_request_answered` from `resolve_request`.
- [x] 1.4 Surface: create fields, projection, refusal sentences, served guidance (`tinyassets/api/automations.py`, `tinyassets/engine_mcp_server.py`).
- [x] 1.5 Retire the four un-emitted scheduler types; `subscribe_branch` refuses; delete the concern.

## 2. Prove
- [x] 2.1 Tests through the real emitters and the real pump (`tests/test_automation_events.py`).
- [x] 2.2 Mutation-check the floor, the filter, the transition guard and the rebuild.
- [x] 2.3 gpt-6-astra refute round: cross-user reach and runaway cost (ADAPT; two P1s folded, one declined with reason).
- [ ] 2.4 Deploy; `python scripts/deployed_sha.py --assert-contains <sha>`.

## 3. Land
- [ ] 3.1 Sync delta into `openspec/specs/user-owned-automations/`, archive.

## Folded lanes (one owner and one cutover)
- [ ] Complete `node-scheduled-wake` obligations in the folded design section (2 open source tasks); use the surviving path only.
- [ ] Complete `owner-message-wakes` obligations in the folded design section (2 open source tasks); use the surviving path only.
- [ ] Complete `served-inbound-webhook-triggers` obligations in the folded design section (4 open source tasks); use the surviving path only.
- [ ] Complete `automation-schedule-timezone` obligations in the folded design section (12 open source tasks); use the surviving path only.
