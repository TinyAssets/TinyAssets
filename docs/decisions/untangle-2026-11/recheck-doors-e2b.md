# recheck-doors-e2b as built (2026-11-21)

Two sub-lines of `tasks.md`'s re-check loop line, in one PR: the overloaded owner object
(`pass-doors.md` §3) and E2B's lifecycle events (`pass-ci-deploy.md` §2).

## 1. Research

**What the runtime's overload is.** Cloudflare's limits page gives an object a soft limit of
1,000 requests a second. Past it, a call through the object's stub throws an error whose
`.overloaded` is true, and the error-handling page says not to retry it
(`limits-audit.md`, the row this PR updates). The sources were read for that row, and could
not be read again from this host. Only a call to a Durable Object throws it. The doors,
the sandbox Worker and the gateway make such calls.

**Who reads it today.** `doors.ts` `serve` caught it around the whole router, after every
door's own surface had passed it on as a bug, and answered plain text, `429` and
`Retry-After: 1`. So the egress door answered a provider SDK in a shape it does not read, an
MCP tool call got the SDK's `internal error`, and a sign-in got a text page. E2B's hook got
the same plain `429`. It has no refusal surface: a refusal from its object call was a `500`.
`egress.ts` `settleOnce` retried it once through a fresh stub, although the limits row says
the doors never retry it. Every other reader of `refusalOf` sees it as a bug. The queue
consumers (`erase.ts`, `import/index.ts`, `operator.ts`) retry a bug and a transient refusal
alike.

**Where the list of E2B's events is.** The kit registers the hook for `deploy.ts`
`E2B_HOOK.events`, and the doors deliver `e2b-hooks.ts` `TYPES`. Both are the same two
strings, and nothing holds them equal. `identity.json` is bundled whole into the sandbox's
runner (`b13-signin.md` §6), so a list added there moves the pair. `contract/src/identity.ts`,
the file's reader beside it, is not in the runner. The deploy kit (`deploy.ts`) and the doors
(`doors.ts`) already import it. A spread of the JSON's array into a frozen constant is
refused by the module-state rule (`ci/kit/module-state.ts`).

## 2. Decided

1. **The runtime's overload is a contract refusal, `object_overloaded`: `429`, `Retry-After`
   one second, transient, and `refusalOf` reads it.** `refusalOf` is already the one test of
   a refusal against a bug, so every surface answers it as it answers any refusal, and every
   lane settles it as it settles a transient one. The doors' own catch and `overloaded` go.
   The refusal's message is the text the doors answered before.
   - `asJson` (`/api`, `/app/api`, `/mcp`'s HTTP): `429`, `Retry-After: 1`,
     `{error: "this account is busy; try again shortly"}`.
   - `asEgress`: `{error: {code: "object_overloaded", message}}`, which a provider SDK reads.
   - `asTool`: the tool's error result, with that message, in place of `internal error`.
   - `asInbound` and `asShared`: `429` and its second, as any transient refusal.
   - `asRedirect`: `temporarily_unavailable`, as any transient refusal.
   - `settleOnce` throws it, without the fresh-stub retry, as the limits row says.
   - The queue consumers retry it, as they did.
2. **E2B's hook answers its object's refusal as JSON (`answered(asJson, ...)`).** That is the
   shape of the hook's own answers (`{error}`), and its status tells E2B to send the event
   again. An overload is `429` and a second, a restore `503` and a minute. Before this, each
   of those was a `500`, thrown.
3. **`contract/src/identity.ts` `E2B_HOOK_EVENTS` is the one list.** `deploy.ts`
   `E2B_HOOK.events` is that list, and `e2b-hooks.ts` delivers only its types. The deploy
   test's `EVENTS` stays as E2B's wire, as `pass-ci-deploy.md` kept it.

## 3. Found and left

The overload is answered on MCP Events as every transient refusal is. Giving the family its
own answer changes every one of them, so it is owed:

- [x] `doors/src/refused.ts` `asEvent` answers a transient refusal (`unavailable`,
  `restoring`, `object_overloaded`) as `Forbidden` (`-32024`, the message its reason): the
  design sketch's codes have none for the moment. One answer for the family
  (`untangle/events-transient`). Done (`small-shape.md` §3).

## 4. Proof

- The sandbox's runner and probes bundles move by the new `REFUSALS` entry alone (the runner
  `2d1da7ab…` to `153e1ba1…`, the probes `0271e824…` to `40509e8e…`, which name the runner's
  digest). The image does not move. Their pair check is the one owed at the dark redeploy.
  `E2B_HOOK_EVENTS` is in neither bundle.
- The recorders: see the PR body.
