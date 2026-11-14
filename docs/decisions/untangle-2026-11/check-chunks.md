# check-chunks as built (2026-11-14)

`catch-refusals.md` 2 decided that the import check asks FILES a question whose answer is a value, chunk by chunk, and that `importCheck` keeps no catch. This is that build.

1. **The FILES method is `checkChunks(ns, key, sha256s)`, answering a `ChunkCheck` per chunk, in order: `stored`, `missing` or `broken`** (`contract/src/files.ts`).
   - No existing contract shape fits. A refusal is thrown and is about the whole call. `CheckChunk` is the owner's answer to the job: it carries the size, which the files Worker never knows. The contract's other answer unions (`RingAnswer`, `InboundAnswer`) are each their own call's.
   - `broken` covers two causes, an object that does not open under the key and bytes that are not their hash. Both read as "not stored as its hash", and no caller acts on them differently.
   - A refusal (`invalid`, `data_key_refused`, `keyring_unavailable`) is still thrown. It is about the call, not a chunk.
2. **One read in the files Worker** (`files/src/index.ts`, `#chunk`): the bucket's get, the open and the hash recheck, answering the bytes or why not. `getChunks` throws the why with the same messages as before. `checkChunks` answers the value. Both take at most `GET_READS` chunks a call, since each holds a whole chunk (`#reading`). `putChunks`' check of an existing key (`#put`, without its catch since #5598) is the same read: a dedup hit only when the key is stored as its hash.
3. **The owner:** `Files.check` batches `checkChunks` `GET_READS` a call, as `Files.fetch` batches `getChunks`, through one `#reads`. `importCheck` maps `stored` to `read: <its hash>` and anything else to `read: null`, with no catch. `CheckChunk` and the window's flow are unchanged.
   - A throw fails the slice. The import queue drops a terminal refusal with a log and retries anything else (`doors/src/import/index.ts`). Either way verify waits for the page and fails loudly ("an answer did not come within the wait").
   - Before, a refused key or a lost answer read as a missing chunk, with no log.
4. **Fewer calls:** the check used to make one `getChunks` call a chunk, a one-byte range. It now makes one call per `GET_READS` chunks, and no byte crosses.
5. **Left:**
   - Verify's difference says "not read back as its hash" and does not say whether the chunk was missing or broken. Carrying that would change `CheckChunk` (lane I's wire, `contract/src/import-message.ts`) and verify's lines. The window acts the same way on both: the owner isn't clean.
