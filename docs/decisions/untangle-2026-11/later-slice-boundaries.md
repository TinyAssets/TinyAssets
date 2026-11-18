# Later-slice dependency review (2026-11-18)

The later-slice list in Step 20 still refers to the original
`openspec/changes/device-first-build/tasks.md` S6–S8 and the N rows in
`docs/architecture.md`. This review traced those requirements on main
`f934871ad` and the staged combination `533733c40`. It covers prerequisites
and historic data. The product slices and their live acceptance remain open.

## What S6 can show about an older turn

A message that starts a native agent creates a run receipt containing the
message ID (`owner/src/launch.ts`, `runs.ts`, `schema.ts`). `runs.ts`
prunes settled receipts to the newest 2,000. The mail archive has a different
lifetime. For an automatic reply to the owner or an outside sender,
`Ledger.answer` takes the run ID as the client message ID of the reply.
When the sender is another native agent it returns without making an
automatic reply. Thus a reply cannot supply a universal historical join.
The transcript is the engine's own file. No common transcript-to-receipt
lookup is defined for this view.

On each reviewed tree an offline SQLite exercise ran all 18 owner migrations,
inserted 2,001 synthetic settled mail runs and applied the exact `Runs.#prune`
statement with its source limit. The oldest message matched one receipt
before pruning and none afterward. Both retained 2,000 receipts. This
exercise tested the stored relationship and prune statement. It did not run
an engine, post mail, or simulate a live owner.

**Before first real runs**, settle what the app can promise for older
mail-started runs. A view of retained receipts can ship later with that
limit shown. If the product needs earlier relationships after receipt
expiry, decide what to record at the start, its retention, privacy,
export and erasure first. Do not infer a run by matching senders or
timestamps. This question concerns the run that received a message.
The separate decision about causality across external mail rooms stays open.

## Other later slices

| Slice | Source and dependency |
| --- | --- |
| S6 app and agent players | The old S6 names app manifests, seat-scoped grants and match records for players with different owners. The importer archives legacy `universe_app_ui` and its assets (`platform/import/rules.ts`); the legacy app manifest derives its screen from that UI (`tinyassets/app_object.py`). Archived UI is not a live seat grant. Define who holds a match and how owners participate under ADR-032 before offering play across accounts. |
| S7 and S8 | Original S8's B/C tiers are match transport modes. They require the S6 match and S7 host advertisement. ADR-024 limits a paired device to its owner's work, with inbound connections excluded and in-flight work unknown on disconnect. ADR-031 keeps the user's login on the device. Review host election and handoff against these obligations. Current hosted recovery in `platform/sandbox/runner/runner.ts` is specific to that sandbox. |
| N2 phone delivery | The importer explicitly drops `.owner_devices.db` so the app can register again. Review registration, owner consent and the sender of phone notifications with the new shells. The vendor wake registry describes a different delivery destination. |
| N3 voice/video and N9 media results | The original rows name a platform `model_call`. ADR-034 assigns the loop and the model call to unmodified engines. Establish which current engine/session supplies a stream, which bytes become owner files, and what a receipt covers. Do not bring back a second platform model loop. |
| N4 owner takeover | The old row names `BoxProvider.view`. Review the current sandbox's owner/session boundary, local OS identities and device placement before exposing a view. The served engine records and hosted sign-in support do not establish this capability. |
| N6 action undo | The old row calls for a pre-action snapshot. Modern `owner/src/restore.ts` restores into a pending, empty owner and has a separate operator pin/wipe protocol. This is disaster recovery; it cannot implement an ordinary undo on an active owner. Define which future actions are covered, capture before them and account for external effects. The current plan establishes no retrospective action-level undo guarantee. |
| N7 purchases | The original row already requires a specification. The clean-sheet policy decides a call under an owner and receipts record its outcome; a model-spend allowance does not authorize a merchant payment. Keep the money-moving interface and credentials behind an explicit custody, approval and settlement decision. |
| Later mail archive | ADR-036 keeps unread and in-flight mail hot. `owner/src/ledger.ts` releases the held message on run settlement, and `owner/src/segments.ts` handles archived records, compaction and purge. Research sparse late acknowledgments and unread archive separately, preserving delivery and acknowledgment when a run is in flight. Neither later task currently requires moving unread mail during the first migration. |
