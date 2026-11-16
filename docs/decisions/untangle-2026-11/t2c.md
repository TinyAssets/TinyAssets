# T2c as built (2026-11-16)

T2c is lane T's third doors PR (`t2b.md`, "T2c"): the last six B23 entries in `doors/guards/rules.ts`, the table doors' answers typed from the contract (`ownDoor<T>`), and `d2:N52`, `N84`, `N85` and `N89`.

## The plan (research, before building)

**What is there.** Five doors test files reach the owner's folder for seven constants and one type: `doors-live.test.ts` (`OWNER_UNADMITTED`, `PLATFORM_ONLY` from `admission.ts`; `ERASE_CONFIRM`, `NO_ERASURE` from `erasure.ts`), `e2b-hooks.test.ts` (`BILLED_WINDOW_S`), `hardening.test.ts` (`BODY_BYTES_MAX`), `runs.test.ts` (`SESSION_REFUSED`) and `env.d.ts` (the fake sandbox's `Log`). `t2.md` item 10 said they "move where both sides read them". Read one by one against what each test is for, only two are vocabulary both sides hold. The other five are the owner's human text, or the owner's rule, asserted by a doors test:

- the refusal's own contract (`contract/src/refusal.ts`): a message is human text, read by no code; what a caller reads is the `code`, from the closed list;
- T2a's rule (`t2.md` item 6): the owner's subjects belong in the owner's suite, beside the code that makes them.

So moving owner text into the contract only so a doors test can read it would be a product move that only a test needs. Each one goes as below.

**The rule this PR holds the doors' tests to:** a doors test asserts an owner refusal by its code. Where the code is a family's (`refused`, `invalid`) and the reason is the owner's, the owner's suite holds the reason. A test that calls the owner's object straight with a binding no door sends tests the owner, not the doors.

1. **`ERASE_CONFIRM` is the contract's** (`contract/src/owner.ts`, beside `eraseWords` and `EraseRequest`): the words the owner types, which its client sends and the object takes exactly, as `eraseWords` are the platform's. The owner's `erasure.ts` and its tests, and the doors' tests and recorder, read it there. The recorder (`wire.record.ts`) spelled it out as a literal.
2. **`BODY_BYTES_MAX` is the contract's** (`contract/src/limits.ts`), defined as what its doc says it is: half of `EGRESS_BODY_MAX`, room for a body's JSON escapes in the call that carries it. It is still 8 MiB, two chunks. The hardening test's `2 * BODY_BYTES_MAX === EGRESS_BODY_MAX` then holds by definition and goes. `ledger.ts`'s alias `MAX_BODY_BYTES` goes with the move, its readers reading `BODY_BYTES_MAX`.
3. **`OWNER_UNADMITTED`**: the fresh object's state is read as the platform reads it (`admissionOf`: `pending`), not by the owner's refusal text. `admit.test.ts` holds the refusal.
4. **`PLATFORM_ONLY`**: three doors tests call the owner's reserved doors (`erasureBegin`, `ownerState`, `dataKeyRewrap`, `gatewayReseal`) straight with the owner's own binding, which no door sends. So does the sign-up test of the platform caller at `grantList` (`PLATFORM_RESERVED`, spelled as `/reaches no owner door/`). Each is the object's binder, so each goes from the doors. The owner's suite holds the rule over the table instead of a hand list (`admit.test.ts`): every `reserved` and `erasure` row refuses the owner's own binding `PLATFORM_ONLY` (a `tier` row `PLATFORM_TIER_ONLY`), and an `erasure` row refuses the platform `NO_ERASURE` while no erasure is under way. No owner test held `NO_ERASURE` before.
5. **`NO_ERASURE`**: the doors' test of an unmarked owner's message asserts the refusal's code (`refused`) and that no outside record was written. The reason is item 4's owner test. `ERASING` at the mark is asserted by its code (`erasing`) too.
6. **`BILLED_WINDOW_S`**: the window is the owner's rule (`sandbox-time.ts` `billed`). The doors only hand the event's signed time on as `at`. So the doors' test asserts what crossed the owner gate: an old event's and an early event's `at` is the time each signed. The owner's suite gains the window test: `sandbox-time.test.ts`, an event older than the window or ahead of the clock is not counted. The doors' test was the window's only test.
7. **`SESSION_REFUSED`**: the run's error is the owner's (`runs.ts`), held exactly by `owner/test/runs.test.ts` with the same input. The doors' test asserts its own part: the runner's post is relayed whole, and the app's `agent/status` answers the object's own status, which carries an error.
8. **The fake sandbox Worker moves to `ci/fake-sandbox.ts`**: it is bound by both the owner's and the doors' test configs (and the browser test's), so it is test machinery of more than one workspace, as `ci/workerd.ts` and `ci/gate-worker.ts` are (`t2.md` item 8). Its `Log`, `Recorded` and `Script` types are then read from `ci/`, which every workspace's tests may import.

With these, the doors' `OWED` list is empty.

**`d2:N52`** (`revoke.ts`'s four `pageSize = GRANT_PAGE` parameters, which only the hardening test passed): they go. The test of reading every page keeps the real provider and the real `listUserGrants`, but its observed `OAUTH_KV` answers each grant listing one key a page, cursor kept. A short page with a cursor is what the provider itself answers (`listUserGrants` returns `cursor` whenever KV's list is not complete, whatever its count, and filters keys of its own). The test asserts the count revoked and that more than one page was read, not the size asked for.

**`d2:N84`** (`binder.test.ts`): the client and family derivation test, and the foreign owner's namespace, recomputed the exact strings the code hashes. Each now pins known answers, the digests as literals, so a changed derivation fails it without the test repeating the formula. These are durable: every stored card and foreign owner is keyed by them. The test that the test env's `FOREIGN_ISSUERS` is `"[]"` goes, since it read the suite's own config. The config guard holds the deployed value (`rules.ts` `vars`).

**`d2:N85`** (`egress.test.ts`, `EGRESS_DEADLINE_MS === EGRESS_DEADLINE_S * 1000`): it cannot fail, so it goes. `EGRESS_DEADLINE_MS` loses its export, which only that test read.

**`d2:N89`** (the sleeps): none of the listing's sites is left. #5605 and #5627 (`test-waits.md`, `test-waits-2.md`) and T2a/T2b removed them (`runs`, `doors-live`, `hardening`, `egress`). What the doors' tests keep are bounded polls of a record the test caused (`export.test.ts`'s cursor, the browser test's reads of the fake sandbox and the page). The timeout guard (`ci/guards/timeouts.test.ts` `timerWaits`) allows these and holds the rest, with no owed entry. The browser test's `{ timeout }` options are node's test runner's liveness bounds. So N89 needs no change here.

**`ownDoor<T>`: owed, not built here.** Typing a table door's answer from the contract needs `OwnerDoors` to hold every table door and its answer. That is `untangle/o2b`, built locally and not yet pushed (`o2b.md`): it writes `OwnerDoors` from the door table (all 134 doors, `DoorAnswer<"door">`) and rewrites `contract/src/owner.ts`'s `DoorTypes`. The doors' tests read 20-odd doors' answers, and typing those needs `answer` entries in that `DoorTypes`, so the overlap is real. Built now, it would need either a second hand-typed `OwnerDoors` or edits to the file o2b rewrites. So `ownDoor<D>` (its door one of `OwnerDoors`' keys, its answer `DoorAnswer<D>`) is `untangle/t2d`, after o2b merges.

**Size.** About 500 changed lines, so no split beyond `ownDoor<T>`.

## As built

As planned, items 1 to 8 and N52, N84, N85, with one change the move needed:

- **`fakeConfig` dates every fake at the Workers' one compatibility date** (`ci/workers.ts`). Before, it read the date from the fake's own workspace's Worker config, and `ci/` has none. Every Worker runs at one date (`ci/kit/config.ts` `dateProblems`), so the rule is the same for a workspace's fake and for a `ci/` one, and no fake's date changes.

**Mutations**, each failing a test that passes on this head:

- **`owner-do.ts`'s `erasure` caller check dropped:** fails only the new table rule (`admit.test.ts`).
- **`erasureOutside` with no `NO_ERASURE` check:** fails only the new table rule.
- **The doors handing `Date.now()` as an E2B event's `at`:** fails the doors' crossing test.
- **`billed` with no window floor:** fails the owner's new window test. The doors' suite no longer could.
- **`grantsWhere` reading one page:** fails the hardening revoke test, now with no page-size parameter.
- **The client's derivation under another domain string:** fails the known-answer test.
- **`BODY_BYTES_MAX` at a quarter of the request ceiling:** fails `bodies.test.ts` and the `limits-audit` guard.
- **The run's error left null for a refused session id:** fails the doors' runs test, which no longer restates the owner's text.
- **The owner's erasure taking other words than `ERASE_CONFIRM`:** fails the six erasure tests of `doors-live.test.ts`, which send the contract's words.

**Not run here:** `npm run test:app`, since this host has no Playwright browser. Its one change is the fake's path.
