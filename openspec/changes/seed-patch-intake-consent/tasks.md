# Tasks: seed-patch-intake-consent

## 1. Build
- [x] 1.1 `tinyassets/patch_intake.py`: configuration, grant, seeding decision, rail view, delivery fence.
- [x] 1.2 `find_by_action_type` (`tinyassets/storage/pending_requests.py`).
- [x] 1.3 `grant_patch_intake` action: validation, grant sentence, answer execution, seeding + rail block (`tinyassets/api/pending_requests.py`).
- [x] 1.4 Fence delivery to the configured intake on both acceptance paths (`tinyassets/api/deliveries.py`).
- [x] 1.5 `write_graph.delivering` patch-request section, resident index line, env-var catalog entries.

## 2. Prove
- [x] 2.1 Tests: seeded on first rail read, idempotent, not re-seeded after deny/clear/mute, approval writes exactly one grant, the grant delivers to that intake and nothing else, cross-user refusals, misconfiguration refuses.
- [x] 2.2 Mutation-check the fence and the idempotence key.
- [x] 2.3 gpt-6-astra refute round on grant scope and cross-user leakage: 3 P1 + 5 P2 agreed and fixed (self-grant via `source_channel`, config-dependent fence, grant-before-resolve, obsolete card blocking the replacement, `withdrawn` read as a decision, capped decided-already lookup, guidance claiming a declined card is waiting, enumerated-sender refusal). No grant-scope widening and no intake-owner graph disclosure found.
- [ ] 2.4 Set `TINYASSETS_PATCH_INTAKE_RECEIVER_ID` in the deploy env once the founder's universe exposes its intake as a receiver.
- [ ] 2.5 Deploy; `python scripts/deployed_sha.py --assert-contains <sha>`.
- [ ] 2.6 Live: the free account's rail shows the ask; approving it lets its universe file a real patch request that arrives at the intake.

## 3. Land
- [x] 3.1 Sync the as-built delta into `openspec/specs/connect-cross-user-nodes/` (2026-10-05, #4477).
- [ ] 3.2 Archive only after deployment and live proof (2.4?2.6); keep this change active.
