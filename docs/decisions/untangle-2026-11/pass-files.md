# pass-files as built (2026-11-17)

d2 §E steps 4 and 5 for the files area, both passes in one PR (`tasks.md`, the passes line),
on the model of `pass-sandbox.md`. The listing they start from is v2 (`list2_services.md`
§2.3 and §2.6), taken at `fdac25ba4`, so every item was re-read at `origin/main`
`4d5a8edda`. Lanes V and R had fixed all of §2.3 before this pass: R1 deleted `cipher.ts`,
V2 to V4 made the checkers, the typed refusals and the generated `Env`. Behaviour is
unchanged. Every non-test source of the workspace, each minified with esbuild, and the
Worker's entry bundled with all it imports, has the same digest on base and head, so no line
of code changed, only comments. The recorders match.

## 1. Comments

**Kept:** vendor facts (R2's `BadDigest`, create-once and batch limits, NFC keys, Workers'
subrequest and RPC limits with their date), spec pointers (ADR-032, plan §6, NIST SP 800-57,
d3 §G), the decision files that hold a design (`r8-erased-orphans.md`, `r8-lost-object.md`),
and the reason comments: why a put reads its key back, why a mark sits outside its prefix,
why a root is swept before its objects.

**Removed or rewritten:**
- **Provenance tags:** `review M5-1` (three places), `review L7` (a test title),
  `untangle A5` and `B21` (`datakey-main.test.ts`), and `datakey.ts`'s `tasks.md` for where
  a kid leaves the ring. It now names the code that holds it, `deploy/deploy.ts` mode
  `retire`.
- **Stale:**
  - `rules.ts` "no var (there is one cipher)" and `vitest.config.ts` "bucket and cipher".
    There is no cipher choice.
  - `index.ts` described `BLOBS` as the platform's only R2 binding and `FilesUpload` as
    the doors Worker's only files binding. The files Worker alone binds the owner-files
    bucket. The doors Worker binds its own staging and operator-log buckets and reaches
    the files Worker through two entrypoints. Its upload uses the namespace and wrapped
    key the owner supplies. Its operator recovery channel can locate the owner from the
    namespace's newest finished generation.
  - At review, the header got back its reason, cut with the stale lines: the Worker takes
    only hex and bytes, never a path, and keeps nothing; a call's namespace and wrapped key
    are the owner object's, and `FilesUpload` puts with the pair the object hands one
    upload. "Never learns an owner" went: `FilesLocate`'s `locate` does, for the operator
    channel. Every source's digest is still base's (`f934871ad`).
  - `index.ts` "The nightly manifest is the only other kind of object". The marks and the
    holds are objects too. It is the only other kind of sealed object.
  - `index.ts` "`deleteNamespace` … the owner's erasure and the cutover's discard alone".
    `FilesLocate`'s `erase` calls it too.
  - `r2-keys.test.ts` "The doors' upload entrypoint hands its env whole". The locate one
    does too.
  - `index.ts` "at most 4 MiB" and "`BATCH_BYTES` (two chunks)", copies of the contract's
    values. They name `CHUNK_BYTES` and `BATCH_BYTES`.
- **Restating the code,** 17 of them:
  - `index.ts`: the docs of `mintKey`, `rewrapKey`, `putChunks`, `checkChunks`,
    `chunkNames`, `getManifestRoot`, `deleteSegments` and `FilesUpload.putChunks`, each a
    copy of the contract's own doc of that method (`contract/src/files.ts` `ChunkStore` and
    `ChunkUpload`), and the private `#key`, `#putObject` and `#getObject`, each its one line
    again;
  - `keys.ts`: `GEN`'s doc (the same sentence as `generation`'s), and `chunkHash`,
    `segmentSpan`, `manifestPart` and `manifestObjectKey`, each its own refusal again;
  - `datakey.ts`: the inline note on `blobMax(KEY_BYTES)`.
- **Run-on lines,** patched in past 100 characters or broken mid-sentence, reflowed: four
  paragraphs in `index.ts`, one in each of `keys.ts`, `datakey.ts` and `r2-keys.test.ts`.

**Left as they are, each with its reason:**
- The method docs that say what the contract's do not: `getChunks`'s charge per whole chunk,
  `deleteChunks`'s "already gone is no error", `markNamespace`, `deleteNamespace`,
  `rootNamespace`, `putManifestRoot`, `rewrapManifestRoot` and `sweepManifests`, each with
  its why.
- `index.ts`'s 100-line header. It is the one place that tells every method, the seal and
  the manifest end to end, and the methods' docs point to it.
- `src/env.ts`. It is generated (`npm run generate`), and the generated-files guard holds it.
- `contract/src/files.ts`'s docs. They are the contract area's.

## 2. Tests

| Item | At this head | Decision |
|---|---|---|
| `v2:F-T1` the env built without `FILES_CIPHER` | Gone: no cipher | Nothing to do. |
| Duplicates in `test/chunks.test.ts` | Three tests whose every assertion another holds | **Consolidated** (below). |
| `guards/config.test.ts` "a cipher choice" | The same refused key as "a var" | **Consolidated** into "a var". |
| `v2:V2-D6` `r2-keys.test.ts`'s header on `FILES_CIPHER` | Gone | Nothing to do. |

**Consolidated:**
- "sealed objects: stores only sealed bytes, reads them whole and by range, under the
  owner's key alone". Each of its checks is another test's: create-once and a re-put's
  no-op; the sealed size, no plaintext, and whole and ranged reads; a second key naming
  nothing; and other bytes refused under the hash.
- "getChunks: reads every range from its whole chunk, its hash rechecked" and "fails loudly
  when a chunk's bytes no longer match its hash" are one test, "… read whole or by any
  range". The first's comment said its changed byte was "outside every range read", but its
  second range (`8`, `100`) held it. The merged test's two ranges both miss it, and a range
  that skips the recheck fails it.
- "data keys: opens a key wrapped under an older kid, and none once its kid leaves the ring"
  joins "wraps a key again under the active kid". The one test reads under the older kid,
  rewraps, and refuses the old key once its kid has left.

**Fixed, checks that could never fail:**
- **The r2-keys guard's two central rules.** "Every key is a call of a key builder" and "a
  list is of one namespace's prefix" were held by no plant. Each plant that used a call used
  a builder or a prefix, so `BUILDERS` and `PREFIXES` could accept any call and every test
  passed. Two plants now call another function (`get(other(p))`,
  `list({ prefix: other(ns) })`), and each set's widening fails its plant.
- **The guard's one exemption.** `new FilesService(this.ctx, this.env)` may take the env
  whole. A guard that let any class take it passed every test. A plant now passes
  `this.env` to another class.
- **The root rewrap's race.** "writes a root under the active kid, and wraps an old root's
  key again in place" raced the rewrap with a fake bucket that answered `null` to every put.
  So the put's condition (`etagMatches` the root it read) was never tested: without it the
  test still passed. The race is now real: the fake reads the real bucket, writes the root
  again as other bytes (R2's etag is the bytes' hash, so the same bytes keep it), and puts
  to the real bucket. Without the condition the test fails.

**Left:** `test/chunks.test.ts` writes and reads the bucket directly (`env.BLOBS`) to plant
broken or moved bytes and to read what is stored. These are faults and stored forms no door
makes or answers. The one front door is the Worker's RPC, and a door for each is a product
change. They are the Worker's own suite on its own bucket.

## 3. The problem index for `platform/files/`

`d2:N56` and `d2:N57` name only `doors/src/` files, and `s2:N51` is the sandbox's
(`pass-sandbox.md` §3), so none is this area's. The files area's own list2 items are v2's:

| Item | At this head | Decision |
|---|---|---|
| `F-SP1` the cipher an `if` in five places | `cipher.ts` gone (R1, #5530): one seal, `sealObject` | Done. |
| `F-SP2`, `V2-D10` `FILES_CIPHER` and `FILES_KEYRING` optional | `src/env.ts` generated: `FILES_KEYRING` required, no cipher | Done. |
| `F-T1` | As above | Done. |
| `V2-H4`, `V2-D9` a second AES-GCM seal | The envelope's `sealObject` and `openObject` | Done. |
| `V2-H5` `WRAPPED_MAX` by hand | `blobMax(KEY_BYTES)` | Done. |
| `V2-SP1`, `V2-D8` plain `Error`s | Every caller fault is a contract refusal (`invalid`, `keyring_unavailable`, `data_key_refused`). "Root changed, run it again" is the answer `{ key: null }`. | **Kept:** the 17 plain `Error`s left are faults of stored state (a root, list, header, chunk or listed key out of shape, missing or not opening). Fact 8: loud, never a refusal of the caller. |
| `V2-S2` the unverified ranged read | Gone (V2, #5543): every read opens and rechecks its whole chunk | Done. |
| `F-H3′` the owner's read batching | The owner batches by `GET_READS` (`owner/src/files.ts`) | Done. |
| `V2-SP2` the opened root cast unchecked | `openedLists` holds it to `manifestLists` | Done. |
| `F-SP3` swallowed errors | See below | Kept or done. |
| `F-S1` `KeyringSecret` | Gone | Done. |
| `F-H4` | Resolved before v2 | Done. |
| `V2-SP3`, `V2-D13` the import bucket's exemption | The kit's `bucketTypeProblems` | Done. |
| `V2-SP4` the compatibility date | The kit's `dateProblems` holds all six | Done. |
| `V2-N2`, `V2-D11` owner tests reaching `files/src` | None left | Done. |
| `I-H17` the importer's copy of the prefix | `objectPrefix` in the contract | Done. |
| v2 §2.6, files' comment lines | §1 above | Done. |

**The files Worker's swallowed errors and retries, each re-read.** It has no retry and no
wait: a repeat is its caller's (`deleteNamespace`, `rootNamespace` and the sweeps are
repeatable). Kept, each with its reason beside it:
- `storedRoot`, `rootBytes` and `openedLists` catch a parse and throw "a manifest root is
  out of shape". The fault stays loud. Only the parser's own words are replaced.
- `locate` reads a part 0 whose first line is not JSON as no header, and then fails loudly
  ("holds no export header of it").
- `#put` reads an existing key back. A failed read is its own error, never "bytes this
  Worker did not verify" (`v2:F-SP3`'s first item, fixed by #5598).
- R2's `BadDigest` is left as R2 raised it (`F-SP3`'s third item: it no longer matches
  text).
- A put that finds its key (`etagDoesNotMatch: "*"`) is the create-once rule: a mark, a hold
  or a chunk made once.
- `rewrapManifestRoot`'s missed etag answers `{ key: null }`, so the owner's sweep counts
  the root left and asks again.

**Found and left:** `src/index.ts`'s `sha256Hex` is Web Crypto's beside the contract's
`digest.ts`, which is `node:crypto` and so not loadable without `nodejs_compat`. It is
already the workspace's one `OWED` entry (`kernels`, B7). Its fix is a code change, a
Web Crypto home in the contract, and the re-check loop's "every `owed` list empty" holds
it.

**Kept, not surplus:** `keys.ts`'s `SHA256 = HEX64` and `GEN = HEX16` name the role of the
contract's grammar where it is checked. They copy no value.

## 4. Proof

- Every non-test source's esbuild minified digest, and the entry's bundle, the same on base
  and head.
- The recorders: `wire`, `tools`, `rpc` and `rv` are the same.
- Mutations, each failing its test:
  - `BUILDERS` taking any call, and `PREFIXES` taking any call (the two new plants);
  - the exemption taking any class (the new plant);
  - the root's rewrap put without `etagMatches` (the race, now real);
  - a ranged read skipping the hash recheck (the merged recheck test);
  - `rewrapKey` keeping the old kid, and `rewrap` resealing an active key (the merged
    rotation test);
  - `putChunks` not create-once (for the removed sealed-objects test's no-op);
  - `vars` allowed (for the removed cipher probe).
