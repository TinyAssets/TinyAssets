# When may a disconnected MCP app return?

**Founder decision open, 2026-11-18.** When an owner disconnects an outside MCP
app, should choosing Allow on that app’s ordinary OAuth consent page and signing in
again be sufficient to reconnect it? Or must the owner first change or forget the
revoked grant in the account's grant controls? Outside authorizations always
require per-app consent and a fresh sign-in. Today the effect of that consent
depends on whether the owner object still has the revoked row.

## What was decided and what runs

| Evidence | Consequence |
| --- | --- |
| [Slice 5, #5355](https://github.com/tinyassets/tinyassets/pull/5355), `3b3da1901`, review M1 and the owner-object requirements “Grant rows stay bounded without forgetting a fence” and “Only the owner's own act lifts a revoke” | The owner's grant bound includes revoked records. When the bound is reached, the owner can make room by explicitly forgetting revoked clients. The MCP grant stays revoked until an owner action. Forgetting one records whose action it was and advances the owner's sign-in cutoff. |
| [Slice 7, #5359](https://github.com/tinyassets/tinyassets/pull/5359), `227aa258e`, doors requirement “A client authorizes again after a revoke” | The owner sees consent for this client again. The doors require a fresh sign-in. `doors/src/authorize.ts` issues the provider grant, without changing or forgetting an existing revoke in the owner object. |
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
existing retention cases with the founder's answer. The Connected apps screen currently hides revoked grants.
If a separate account action is required, the owner needs a way to reach it.
Preserve held grants and
the durable sign-in cutoff in either case. If explicit owner action is required,
the reviewed design must bound stored disconnect decisions and keep explicit consent
separate from grant management. If automatic retirement is intended, describe exactly which
records can be retired, what subsequent consent can restore and what owners can see.
