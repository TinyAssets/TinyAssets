# T2b as built (2026-11-15)

T2b is lane T's second doors PR (`t2.md` item 10). The whole item would pass 1,500 changed lines, and its constants' move edits the owner's source, which lane O's `untangle/o1c` was changing, so it is split at the seam the brief names:

- **T2b (this PR): the doors' real entry and its erasure queue** (`d2:N49`, `N50`, `N53`).
- **T2c (`untangle/t2c`):** the last six B23 entries in `doors/guards/rules.ts` (the owner's constants `OWNER_UNADMITTED`, `PLATFORM_ONLY`, `ERASE_CONFIRM`, `NO_ERASURE`, `BILLED_WINDOW_S`, `BODY_BYTES_MAX` and `SESSION_REFUSED` moved where both sides read them, and the fake sandbox's read-back type), the table doors' answers typed from the contract (`ownDoor<T>`), and `d2:N52`, `N84`, `N85` and `N89`. `untangle/o1c` merged as #5645 while this PR was built, so T2c may start from main once this one merges.

## As built

1. **The doors' suite runs `src/index.ts`** (`doors/vitest.config.ts`, with no entry of its own). `test/entry.ts` and `test/fake-upstream.ts` are deleted. `ci/workers.ts` `main` loses its `entry` parameter, whose one caller was the doors' config, with the kit test of it.
2. **The upstream sign-in comes from config, the live one** (`d2:N49`). The suite's `OWNER_ISSUER` and `UPSTREAM` are set as the deploy kit sets them, and every global `fetch` of the doors Worker reaches `test/fake-providers.ts` (`outboundService`), which also plays AuthKit:
   - its token endpoint (`api.workos.com`, the record's one secret origin) checks the code's PKCE challenge against the verifier and that a client secret was sent, and answers an RS256 access token for the code's subject, signed under a key it makes at first use;
   - its JWKS names that key;
   - its identity deletion answers `204`, or `503` once for a subject a test arms, and keeps the statuses it answered each subject (`support.ts` `forgotten`, `failNextForget`).

   So `oidc.ts` runs whole on every sign-in in the suite. The router, the authorize and callback doors and the erasure take the upstream from `env` where a sign-in or a deletion needs it (`upstream.ts` `upstreamOf(env)`, moved from `index.ts`). The `Upstream | (() => Upstream)` union, `authorize.ts`'s second `upstreamOf` and every upstream parameter go.
3. **`d2:N50` was already gone**: `oidc.ts` has no `fake?` parameter, and `authkit.test.ts` spies the Worker's `fetch`.
4. **`d2:N53`**: `sharedApps(declared = apps)` loses its parameter, which no caller passed. Its other half, a parse on every inbound request, stays: a parse at load is a call at top level, which the module-state rule refuses (`ci/kit/module-state.ts`, its guard failed on it), and the file is a few hundred bytes.
5. **The test OAuth source has no config path, by design.** No RFC 6749 record ships (`sources/records/`), and a record or a binding only the suite sets would be the test-only product input the untangle rule refuses. So the catalog stays the entry's to hand to the router (`serve(request, env, ctx, sources)`, `index.ts` passing `bundledSources()`). Only a test of a record the bundle lacks calls the router with its own catalog: `connect.test.ts`, and the browser test's harness (`e2e-entry.ts`), both over `test/sources.ts` `TEST_SOURCES`.
6. **The erasure queue is the config's own.** The owner Worker's `ERASURES` is `tinyassets-erasure`, consumed by the doors' own `queue` export a message a batch and at once, as `owner/vitest.config.ts` runs its fake consumer. `owner-erasures-recorded`, the doors' use of `owner/test/fake-erasures.ts`, `ERASURES_SENT` and `doors-live.test.ts`'s `sent` and `consume` (a 10 ms poll) go, with one B23 entry (`env.d.ts` → `fake-erasures.ts`).
   - A test waits for the consumer's confirmation (`erasureOutsideDone`) to cross the owner gate (`confirmed`).
   - A test that looks between the mark and the outside record holds the consumer's `erasureOutside` at the gate (`holdOutside`, `heldOutside`, `letGo`). So "the message is lost" is now "the message waits", and a failed delivery is a delivery by hand while the object's own message waits.
7. **What the live upstream makes unreachable goes**, each held where its one source is:
   - the doors' recheck of the URL the upstream builds (`authorize.ts` `signIn`): the one upstream builds it with the contract's `authorizeUrl`, whose record parser refuses a flow parameter, and `authkit.test.ts` holds both. Its eight cases go;
   - the callback's recheck that `authenticated_at` is whole seconds: `oidc.ts` refuses such an `auth_time` itself (`authkit.test.ts`). The doors' test now asserts the upstream's refusal, `502` naming the check (`hardening.test.ts`);
   - the two door-level tests of a sign-in reported under another issuer (`binder.test.ts`): the live upstream reports `OWNER_ISSUER` alone. `ownerOf`'s own tests keep every issuer rule.
8. **A client's metadata document is the fake outside world's** (`support.ts` `serveDocument`, `fake-providers.ts` `/document`), in place of a `fetch` spy in each test (`hardening.test.ts`, `brain.test.ts`). Each spy wrapped whatever `fetch` the test file's own spy had put in place, so once a sign-in fetched (the token and JWKS), the stacked spies broke it. One spy is left, `serveFirstPartyDocument`, which serves the doors' own document from the doors. The bound on metadata fetches (`doors-live.test.ts`) counts them in the fake's log.
9. **Mutations**, each failing tests that pass on this head:
   - the entry's queue export ignoring the erasure queue fails the seven erasure tests that wait for the outside record;
   - the erasure skipping the identity's deletion fails both "begun at" tests and the retry test;
   - `oidc.ts` taking an `auth_time` that is not whole seconds fails the hardening test of it, which the callback's recheck held before;
   - `upstreamOf` building the live upstream under another issuer fails seven of `oauth.test.ts`'s tests. Before, no doors test built it.
