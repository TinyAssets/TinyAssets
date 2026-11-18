# When may a disconnected MCP app return?

**Founder decision open, 2026-11-18.** When an owner disconnects an outside MCP
app, must it stay disconnected until the owner explicitly changes or forgets that
app's grant? Or may a fresh sign-in reconnect it once the owner has disconnected
enough other apps for its old record to be retired? This affects the meaning of
the owner's Disconnect action. The platform has two different answers.

## What was decided and what runs

| Evidence | Consequence |
| --- | --- |
| [Slice 5, #5355](https://github.com/tinyassets/tinyassets/pull/5355), `3b3da1901`, review M1 and the owner-object requirements “Grant rows stay bounded without forgetting a fence” and “Only the owner's own act lifts a revoke” | The owner's grant bound includes revoked records. When the bound is reached, the owner can make room by explicitly forgetting revoked clients. The MCP grant stays revoked until an owner action. Forgetting one records whose action it was and advances the owner's sign-in cutoff. |
| [Slice L, #5401](https://github.com/tinyassets/tinyassets/pull/5401), `1a8c57afc`, changes to `owner/src/grants.ts`, `keys.ts`, and `owner/test/grants.test.ts` | Mint counts live API keys. The owner grant store counts live owner-set and revoked owner-set rows separately. Over the revoked bound it forgets eligible oldest revoked rows as part of another change. This also applies to MCP grants. The shared sign-in cutoff advances, without an owner instruction naming the forgotten app. |
| Main `f934871ad` and joined `533733c40` | The two owner-object requirements still specify the slice-5 behavior. The grant implementation and both retention cases in `owner/test/grants.test.ts` still specify slice L. `limits-audit.md` also describes a single owner-set grant bound. |

The live API-key limit and the owner grant limits serve different purposes.
Turning off a key removes it from the live key count. The app's future key list
can read the existing key records regardless of the MCP decision. A revoked MCP
client with its record still present remains disconnected. After that record
is forgotten, the object interprets a sign-in after the owner's cutoff as a
new client. The cutoff continues to apply to earlier sign-ins.
Remembering that cutoff and remembering this app's disconnection have
different consequences for a later sign-in.

## Work that depends on the answer

Before signing off outside-client grants or describing reconnect behavior in the
app, reconcile the owner-object requirements, grant bounds, owner UI and the
existing retention cases with the founder's answer. Preserve held grants and
the durable sign-in cutoff in either case. If explicit owner action is required,
the reviewed design must bound stored disconnect decisions and give the owner a
way to manage them. If automatic retirement is intended, describe exactly which
records can be retired, what a subsequent sign-in means and what owners can see.
