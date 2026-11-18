# A wake answer and the subscription it can still describe

Re-review on 2026-11-18, main `f934871ad` and combined candidate `533733c40`. This finding applies to both trees. Status and token-notice ownership remain open. All observations below used synthetic data locally.

## Two owners of the result

The returned vendor status belongs to the attempted wake. The current callback row also keeps a status, and a dead-token result tells the owner to paste a replacement. Those two writes can outlive different things. If the owner replaces or ends the subscription while a request is pending, the old request still happened. Its answer can no longer diagnose the new token.

* `doors/src/app.ts` (`doorbell`) and `doors/src/events.ts` bind a stable subscription ID to the principal, callback URL, kind or event, and canonical arguments (`contract/src/wake-kinds.ts`, `subscriptionId`). Re-pasting for the same destination reuses that ID. The owner stores only the gateway's sealed blob. The Claude routine record in `contract/wake-kinds.json` names 401 as the result for which the owner can replace a token.
* `owner/src/ledger.ts`, `subscribe`, replaces the blob and explicitly resets the last status. `unsubscribe` deletes the row. In `owner/src/wakes.ts`, `Doorbell.send` reads callbacks, awaits the wake mark, then rechecks subscriptions by ID. It still sends the previously read blob. It awaits the gateway, calls `Ledger.answered` by ID alone and makes the notice decision using the previous snapshot. A zero-row update after unsubscribe still reaches the notice call.
* `owner/src/owner-do.ts` wires the gateway and the owner's notices into that sender. `gateway/src/doorbell.ts` opens the supplied envelope and returns the status for that send. The gateway may also return a rewrap of the envelope. `Ledger.resealed` already protects the rewrap by matching the envelope it was made from. The wake outcome and the owner's actionable notice follow separately in the sender.
* `owner/test/doorbell.test.ts` covers repeated 401s, a later re-paste and another 401, and a refresh racing a key sweep. Those cases leave the interval from dispatch to delivery of a stale answer unexamined. `wakes-lost.md` already establishes that an uncertain prior send must not be replayed.

## What ran

On each commit a Node 24 probe invoked the actual `Doorbell.send` and `Ledger` methods with SQLite in memory, the actual callback table, its index and both insertion triggers from `MIGRATIONS`. A minimal enabled-agent row, synchronous storage adapter, notice counter and local synthetic gateway supplied the environment. No Worker, real credential or network request was used. Each run checked both notice counts and the stored result. A second probe replayed the exact subscribe and answer SQL from each commit and independently confirmed the stale status write.

| Condition around the first 401 | Observed on each tree |
| --- | --- |
| Ordinary send, followed by another 401 | One notice in total. |
| Re-paste during the gateway call | Replacement row was set to 401 by the old answer. One notice from the old token, then zero additional notices for the replacement's own first 401. |
| Unsubscribe then recreate during the call | Same incorrect status and notice attribution. |
| Unsubscribe during the call | Row stayed absent. The old answer still called the owner notice. |
| Rewrap only during the call | One notice for the still-current configuration. The subsequently dispatched blob changed as expected. |
| Re-paste while awaiting the mark, before the pre-send check | The new configuration passed the ID recheck and the first send used the old blob. The next wake used the new blob. |
| Overlapping two sender calls, same configuration, two 401s | Both snapshots considered this the first dead-token reply and both called the notice. This is a method-level control. Scheduling two such wakes in the real runtime remains unverified. |

The seven cases passed their observational assertions on each commit. Passing here reproduces the four stale-subscription cases. Real retained notice counts and runtime interleavings require a scoped workerd regression.

## Identity and the remaining design work

The callback row (`owner/src/schema.ts`, `wake_callbacks`) has no established durable identity for one installed configuration. The server-derived ID deliberately survives replacement and can recur after deletion. The door starts the seal at `DOORBELL_VERSION = 1` for each paste. The gateway can legitimately change the blob, kid and version during rewrap of the same secret. The wake mark identifies the event and fold and survives a refresh. Creation and refresh use the owner clock with millisecond precision and no uniqueness constraint. The refresh and recreate controls used the same timestamp successfully. Previous blobs are optional and expire. None of these fields independently answers whether the configuration that produced the answer is still installed.

Re-review the subscription write, pre-dispatch recheck, current-row status transition and token-notice transition together before choosing a fix. Keep the original wake's per-kind acceptance and 429 handling, and its may-have-sent rule. Include the same-send rewrap and an independent key sweep. Include end/recreate, grant revocation and late results. The owner already knows when a configuration is installed, replaced, ended or rewrapped. Recording that distinction must preserve sealed-secret ownership in the gateway. If a persistent schema change is necessary, review existing rows, append-only migration and forward recovery, frozen row accounting, and backup/export exclusions before implementation. `owner/src/export.ts` excludes this table under ADR-018. No proposed field or migration is approved here.

## Other owner production changes reviewed in the combined tree

| Caller and boundary | Scope and ordering |
| --- | --- |
| `archive.ts`, `Ledger.archiveBlocked`, mail posting | A full archive pauses the archive lane. Posting reaches its own hot ceiling before it refuses. The staged wording should preserve this distinction. The billing refusal and actual upgrade availability have a separate review. |
| `codemode.ts`, code caller | Diagnostic text follows the serialized JSON result size. The recorded calls and landed effects remain available on oversize results. The B26 change is independent of callback identity. |
| `collections.ts`, `ledger.ts`, collection watchers, digest readers, Doors' Events schema | The collection fanout and both digest reads use the contract's collection-room prefix. Keep these consumers with the prefix definition. This represents app collections. Historical DM membership has separate requirements. |
| `wakes.ts`, kind records, contract `wakeReads` | The contract owns change-to-digest order and each vendor's accepted status. The sender classifies the real returned status from that record. Keep the digest reader and its contract definition together. Neither change resolves the callback lifetime finding. |
| `import.ts`, `erase.ts`, `owner-do.ts`, `importDiscard` | The owner hands retry and sharing of an in-flight physical wipe to Erasure. Same-instance retry retains the namespace, waits for a successful database wipe, and can retry the final Files sweep. Ordinary owner doors stay busy during the wipe. Use the erasure and binding changes together. A fresh instance still lacks the private import-attempt identity, and normal account erasure has its own outstanding Files completion obligation. |
| `schedule.ts`, owner job and job-error paths | Both enter the owner gate and check halt state there. Synchronizing unchanged lanes now shares a gate. A real rearm yields, then the next gated section rechecks halt. Review together with wipe and restore transitions in integrated behavior. |

This review covered all nine changed `platform/owner/src` files from main to the combined candidate. It is an architecture review of those diffs and their immediate consumers. It does not certify all owner code, production behavior or the project-wide untangle. No production change is part of this finding.
