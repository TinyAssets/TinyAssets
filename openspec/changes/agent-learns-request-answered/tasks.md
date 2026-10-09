# Tasks: agent-learns-request-answered

## 1. Build
- [x] 1.1 `graph_delivery_answers` + answer/list/take in `tinyassets/storage/deliveries.py`; erase in `account_deletion.py`.
- [x] 1.2 `answer_delivery` / `list_deliveries` actions, enveloped note, notice text (`tinyassets/api/deliveries.py`).
- [x] 1.3 Routes: `write_graph target=receiver operation=answer`, `read_graph target=deliveries` (public and served).
- [x] 1.4 Once-only `platform` notice in `universe_server.converse` (main thread).

## 2. Prove
- [x] 2.1 `tests/test_patch_request_answer_loop.py`: file, resolve, status read, next real turn carries the notice once, cross-owner refusals, re-answer told again.
- [x] 2.2 Mutation-check the receiver-only answer guard and the sender-only notice guard.
- [ ] 2.3 Deploy; `python scripts/deployed_sha.py --assert-contains <sha>`.
- [ ] 2.4 Live: answer delivery `54a947b9b92e44a8b515b35506cee6d0` as resolved from the intake's command center; the founder's agent's next turn names it and re-checks its DeepWiki workaround.

## 3. Land
- [ ] 3.1 Sync the delta into `openspec/specs/connect-cross-user-nodes/` after live proof; archive.
