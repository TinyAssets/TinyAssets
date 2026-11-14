# keyed-copies as built (2026-11-14)

`untangle/keyed-copies` folds the sorted-key copies of `keyed` that #5600's review found in files it did not touch, makes a `subscription_limit` refusal without its limit fail loudly at the doors, and adds the guard that catches a new sorted-key copy. Each site was read first: every one is `keyed`'s check (exactly these keys, in any order), and every caller's input is plain data (JSON-parsed, a JSON module, a YAML document, or structured-cloned over RPC), so `keyed`'s stricter object test changes nothing it sees.

1. **The sorted-key checks are `keyed`:**
   - `gateway/src/doorbell.ts` `doorbellSecret` (the plaintext is the sealed blob's JSON, or the object `plaintext` builds);
   - `contract/src/envelope.ts` `parseRing`, and its inline object test of `keys`. The envelope imported nothing so that Node's type stripping runs it; it now imports `./checks.ts` by file name, which keeps that true (`contract/guards/node.test.ts`);
   - `contract/src/apps.ts` `checkedApps`: an app's keys, `match`'s, and `connect`'s `some(!includes)` (all four optional), with the file's inline object test;
   - `files/src/index.ts` `storedRoot`;
   - `owner/src/admission.ts` `setAdmission`: two shapes, `{admit}` or `{reason, suspended}`;
   - `owner/src/owner-do.ts` `importBinding` (and its `import`'s one key) and the sandbox binding's check;
   - `owner/src/sandbox-time.ts` `meterRead` (the log and each event) and `billedEvent`, whose own `whole` was `checks.ts`'s.
   - The guard below found four more in `ci/guards/legs.test.ts` (`legJobProblems` and `processProblems`); they are folded too. The messages still list the keys a job carries.
2. **`owner/src/authority.ts` `stamped` is deleted, not folded.** It has had no caller since #5389 took its two out with pi-in-the-owner-object. A stamp is built inside the object from its own grant rows and read back from its own storage, so nothing outside hands one in to be checked. `STAMP_KEYS`, its `label` helper and their imports go with it, and so does its test.
3. **A full agent's refusal carries its limit, or fails loudly.** `doors/src/refused.ts` `asEvent` answers `subscription_limit` as `ResourceExhausted` only with facts that are exactly `{limit, max}` (a name and a whole number). Without them it throws an `Error`, a bug, which the MCP SDK answers as an internal error. It no longer answers `ResourceExhausted` with no data (Fact 8). Today the ledger always sends the facts; this guards the day it doesn't.
4. **The guard:** a fourth kernel in `ci/kit/kernels.ts`, the key-set test, home `checks.ts`, in the same layers as the object test (now one list, `CONTRACT_READERS`). The mark is `Object.keys(…).sort()` joined or `JSON.stringify`'d, then compared with `===` or `!==`, or named in a variable to compare. All twelve took that form, and so did one of legs' four. Sorted keys in a message or a log line (the deploy test's `env=` event), or kept as a list (to read in order, or a test's `toEqual`), are not marked. Over the whole tree, the broader mark (any sorted keys joined) also hit that one log line, which is why the narrower mark was chosen. It does not see the list stringified through a variable, or a joined string returned from a callback, as three of legs' were. It is a tripwire, as the kernels are.
5. **Left:**
   - `ci/kit/config.ts` `varProblems` sorts `Object.keys(allowed)` before `keyed`. The sort is needless but harmless, and it is no copy.
   - `import/stage.ts` sorts a query's keys to sign it (SigV4's canonical query), and `contract/src/wake-kinds.ts` sorts them to write canonical text. Neither is a key-set test.
