# catch-refusals as built (2026-11-14)

Four owner catches took every error where only a refusal is meant, so a storage fault or a lost answer read as an ordinary answer, with no log (Fact 8). #5591 found them (its retro-check, "one for all four") and its review.

1. **Three catches narrowed to the refusal each means; anything else is thrown.**
   - `owner/src/files.ts`, `leaseRelease`: only `sync_fenced` is "already given back" (`{ released: false }`). Anything else rolls the unit back and reaches the caller (`launch.ts`'s `releaseLease` and `leaseFreed`), which gives nothing back on a throw.
   - `owner/src/archive.ts`, `archivedParent`: only `refused` ("no such message held") is "no parent"; the post then refuses it as no parent.
   - `owner/src/signins.ts`, `signinOwned`: only `refused` (`NO_SIGNIN`) is "not the owner's"; the socket door then refuses the ticket.
   - Each wraps only SQL reads in the object, so the error they swallowed was a storage fault or a bug. Their tests make one by renaming the table the read names, as `wakes.test.ts` makes one with a `RAISE` trigger. The gates' `lose` acts on a binding's answer, and these reads cross no binding.
2. **`importCheck`'s chunk read (`owner/src/import.ts`) is left as it is; it needs another lane's change first.**
   - **Today:** any throw from `FILES.getChunks` reads as `read: null`. The import check then reports the chunk as "not read back as its hash" (`import/check.ts`), and the owner is not clean.
   - **What a throw can be:**
     - FILES's own answer that the chunk is broken: missing from the bucket, its bytes not its hash, or not opening under the key. Each is a plain `Error`, the files Worker's loud invariant break.
     - A refusal: `data_key_refused`, `keyring_unavailable` (transient) or `invalid`.
     - A lost answer.
   - The object can tell only the refusals apart (`refusalOf`). The broken-chunk answers and a loss are all plain errors, and telling them apart by message is what B2's guard forbids.
   - **Retry in the object:** no. That is a special case. A loss that persists would still read as missing, and a chunk that really is gone would be read again for nothing.
   - **Let every throw out (no catch):** loud, but the cost is too high:
     - the check would lose its record of which chunk failed, the record it exists to keep;
     - verify would fail on "no answer" instead of listing the difference, which changes the window's flow (`import/check.ts`, the doors' check test);
     - `CheckChunk.read` would always be the hash, which is surplus (lane I).
   - **Mark the owner's import failed:** there is nothing to add. Verify's record is that mark, and admit already refuses unless every owner verified clean.
   - **Decided:** the check asks FILES a question whose answer is a value, chunk by chunk: is it there under the owner's key, and is it its hash (read, or why not). A throw is then only a lost answer, a refusal or a bug, and `importCheck` has no catch:
     - the import queue already retries a failed check slice (`doors/src/import/index.ts`, `message.retry`; the check is a read, so the retry costs nothing);
     - a loss that persists ends in the dead-letter queue, and verify fails loudly ("an answer did not come within the wait: read the dead-letter queue");
     - a broken chunk stays a recorded difference;
     - the window's flow (verify, dlq, admit) is unchanged.
   - **Owed, on branch `untangle/check-chunks`:** the FILES method (`files/src/index.ts`, and `ChunkStore` in `contract/src/files.ts`), which are lane V's files (d2 §D). It comes with the one owner change (`import.ts`'s catch removed) and a test that loses the `FILES` answer with the gate's `lose` (#5591's `Control.lose`).
3. **The review's retro-check found two more, fixed here.**
   - `owner/src/egress.ts`, `egressOpen`'s refresh catch: only `credential_refresh_failed` or `credential_expired` lets the open go on; a storage fault in the refresh (its lease write, or the rotated tokens' store) is thrown, not dropped with no log.
   - `files/src/index.ts`, `#put`'s existing-key read: a failed bucket read is its own error, no longer "a chunk key holds bytes this Worker did not verify"; the catch is gone (`openObject`'s null is the only "does not open").
