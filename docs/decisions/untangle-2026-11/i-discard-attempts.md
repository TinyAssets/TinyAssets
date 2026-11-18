# Import discard attempts as built (2026-11-18)

The import consumer on f934871 can wipe the correct owner and lose the evidence needed to prove its files were removed. The private answer is written after the owner returns. On the same instance a second discard returns the first namespace. The owner after eviction can return null. A new host also picks a new ask. The host recovery on `codex/import-wipe-proof` preserves answers that exist when it lists staging.

## Evidence

- Two local workerd controls staged a real bundle, ran the actual first discard and observed a 32-hex namespace. They replayed the same queue message with the measured post-eviction null response supplied at the owner gate. One replay replaced the earlier private R2 answer. In the other control the first answer write threw before reaching R2, the queue retried and its only durable answer contained null. The eviction itself was measured separately in the owner suite on `codex/owner-discard-schedule`.
- The owner test here imports and reads a private collection row, discards, evicts the object, then imports another private row under the same owner and bundle. Both discards legitimately return null. Namespace equality cannot establish that the second database is the database a delayed message meant to discard. Minting a namespace just to distinguish the two would change this result and leave a files-plane erasure marker.
- A distinct local R2 experiment created an armed object conditionally, then raced two prepared values against its ETag. Exactly one won. The old ETag could no longer change it. A matching update after deletion did not re-create the object. Recreating the key with a different attempt in its contents gave it a different ETag and the original delivery could not change it. These are measurements in local workerd; no hosted account was used.

## What has to survive

1. The host needs a durable identity for the staging attempt and must arm its discard before enqueueing. A retry must resume the same attempt. The current bundle digest and principal-derived key repeat on a byte-identical restage. A new random ask per host command also loses continuity. An unresolved destructive attempt must exclude restaging, including import work already dispatched to the queue.
2. The owner must return its original namespace and establish an identity for the database incarnation before it can be wiped. A nullable namespace alone fails the SQL-only case above. The owner must claim the matching incarnation before its first asynchronous deletion. The current in-memory discarded flag is set after deleting blobs and disabling alarms. A separate read followed by the unconditional discard leaves a race.
3. The consumer must durably store the owner's preparation before deleting, and update the winning attempt conditionally. A competing worker must reread the winner. A late consumer must never create an attempt or completion that the host already retired. A final response alone cannot recover a crash before its first write.
4. Keep the staged index while proving all recorded file prefixes, and retire the proved attempt only when publication is fenced. The host run lock serializes commands on one host. It does not drain already-dispatched messages. The close predicate remains the founder's decision in `i-window-close.md`.

The durable protocol and its migration remain open. This branch records the failure boundaries and protects the genuine SQL-only result. It changes no Worker implementation, queue message format, staging credential or production data.
