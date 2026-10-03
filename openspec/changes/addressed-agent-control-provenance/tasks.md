# Tasks: addressed-agent control provenance

Design-only handoff; no implementation box is complete. One implementation lane,
owned by the lead's designated implementer after cross-family design review.

- [x] 1. Trace combined foundations and reproduce the owner-level effector mismatch without transport; draft carrier and compatibility contract.
- [x] 2. Obtain Claude design review and resolve correctness/authority findings before runtime or schema edits. First ADAPT at f302de7 folded (F1 native limit, F2 launch credential, F3 recurring reconfirmation); [actual Claude design APPROVE](https://github.com/TinyAssets/TinyAssets/pull/4343#issuecomment-5965426542) at 6bf7923597983ec9af61968b99001745a981f2a7 closes this design gate only.
- [ ] 3. Inventory all admission/queue/resume creators and digest serializers; extend existing turn/run records with validated provenance and mixed-version refusal tests.
- [ ] 4. Bind authenticated served/native/HTTP launch identity to persisted snapshots; prove owner/home/agent rejection, launch replay/audience rejection and cross-process credential isolation before work starts; hold native path without isolation.
- [ ] 5. Propagate through graph, nested, queued, resumed and existing manifest/activity lineage; prove crash/restart identity and stale-work holds.
- [ ] 6. Wire effectors, current rules and review instructions/switches; prove researcher hand-off and cross-worker revocation/rule-change admission races with fake transport; prove native termination outcome/deadline separately without claiming an internal pre-tool hook or releasing D2.
- [ ] 7. Wire pending creation/dedupe/mute/withdrawal, answer events/relay and notifications; prove same-owner agent separation and cross-owner refusal.
- [ ] 8. Wire existing Rules panel and addressed Stop/explicit stop-all; prove two-tab selection, current-home and stale-revision fences.
- [ ] 9. Complete journal/status attribution and mixed-version/rollback proofs. The legacy recurring hold and its owner reconfirmation door are CUT: the founder is clearing the stale definitions through their own surface (design §10a), so there is nothing to migrate and the `held` state, the `confirm_agent_provenance` resume extension and the projection fields are not built. An automation created from now on still carries a snapshot, and an agent-aware request without one still refuses. MUST include a test that the two KEPT snapshot-less definitions still fire as main -- "Morning focus note" (41e88e0f, cron) and "GTM Village - submit task" (cebc77f2, app_event) -- because holding them is the failure this slice is most likely to ship.
- [ ] 10. Run matrix, required mutations, affected heavy tests, Ruff and mirror/import checks; obtain fresh cross-family implementation approval and exact-head CI.
- [ ] 11. After separately authorized deployment, perform grouped owner live acceptance; show addressed Stop versus still-live background work (recurring reconfirmation is cut, task 9); assert deployed SHA, sync spec and resolve the linked P1 concern.
