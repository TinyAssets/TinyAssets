# Importing complete legacy text on the cutover host (2026-11-18)

The cutover shell pulls `node:22` once and reuses its image digest for census,
bundle and verification. On Node 22.23.5, reading a SQLite TEXT value as a
JavaScript string stopped at its first U+0000. The old collection writer accepts that
character and stores the following text. Node 24.22.3 already read it whole.

The census summed the stored byte lengths in SQLite. The bundle read the truncated
string. Verification recomputed the expected collection digest from the bundle, so
a match could confirm the truncated value. The private handoff also read the old
billing store's nullable customer and subscription identifiers as TEXT.

## Which values cross the boundary

All row data that the importer interprets now passes through `backup.ts`'s
`tableRows`. SQLite JSON-quotes each TEXT value before the driver reads it. The host
decodes those strings as it iterates. SQLite applies the sort to the original column
values. SQL NULL, numbers and binary values keep their native types.

| Reader | Values and original producer |
| --- | --- |
| `build.ts` | The collection body is arbitrary text, while the old collection and row names must be printable. The writer also supplies private status and sequence. Policy documents are JSON, whose string escapes preserve a contained NUL. Rejected documents are archived verbatim. |
| `survey.ts` | Admission and deletion records contain structured JSON. Principals, states, tiers, center names, snapshot IDs and digests have their own grammars. Customer and subscription IDs are nullable opaque strings in the private cutover record. The old plan store accepts nonempty strings when binding a verified checkout. |
| `erasure.ts` | Image tombstones and center state use the same table reader as the fleet survey. The original ownership and digest checks consume the complete values. |
| SQLite archives, schema and filesystem | Whole archives copy bytes. Selected archived tables copy via SQLite's own `INSERT … SELECT`. Names of SQLite tables, SQL statements and filesystem paths cannot contain an embedded NUL. Row and byte counts, integrity checks and table-presence queries do not return user text payloads. |

## Verification

On `f934871ad` and the joined rehearsal `151c2a83f`, seven calls through the
old Python `Conversations.write` produced seven source hashes. On Node 22 the
real builder matched two. A 65,536-character body occupied 131,072 bytes in
SQLite and three bytes in the bundle. Two synthetic private IDs written through
`Plans.bind_checkout_subscription` were also truncated. On Node 24 both
baselines matched all nine values. The corrected builder matched them all on
both runtimes, preserving row order, privacy and a different account's null IDs.
The oracle was the Python writer's returned bytes. No provider supplied an ID.

Across UTF-8 and both UTF-16 SQLite storage encodings, 27 controls per runtime
preserved text, embedded and trailing NULs, quotes, byte arrays, NULL, numeric values
and the requested ordering. Fourteen filesystem paths containing composed,
decomposed, astral and private-use characters, a BOM, spaces and nested directories
matched independent per-component UTF-8 ordering and byte-identical contents on
both baselines and the candidate. That path ordering already agreed.

On the joined rehearsal the actual Worker imported five corresponding strings,
skipped them on redelivery, matched their complete row digest and returned all
five on collection reads after admission, including a placed body. This covers
the destination independently of the host fixture. An actual census and all
coordinator-controlled cutover rehearsals remain outstanding.

## Archived policy documents

- [x] The ordinary bundle regression at `a248b68e` passed on Node 22.23.5 and failed with the same fixture on main `f934871ad`. On main, SQLite contained the complete synthetic document, but the owner's readable `policy.refused.json` stopped at the first NUL. The archived SQLite copy retained the complete value. The legacy policy writer serializes JSON, which escapes embedded NULs. This check covers the existing archival promise for unrecognized stored text and establishes no frequency in real backups.
- [x] Independently compared the SQLite source as a BLOB, the emitted database's BLOB, the readable artifact and the census disposition. Five synthetic documents ran on five source combinations on each of Node 22 and 24. On Node 22 main and the five-stage import rehearsal without this reader preserved one of five readable documents. This branch, the earlier file-index rehearsal and the six-stage rehearsal with this reader preserved all five. On Node 24 each source combination preserved all five. Every comparison retained the complete SQLite copy and correctly archived the refused document.
- [ ] Coordinator integration must carry the shared SQLite row reader into both the separate policy census reader and bundle construction. The earlier unrecognized-policy census and current real rehearsal remain separate acceptance requirements.

## Unfinished check for collection values that cannot be represented as text

The original Conversations.write requires a Python string for the body.
SQLite's declared TEXT column and integrity_check alone do not prove that
a historical row holds valid text. This is a synthetic data-quality finding.
Occurrence in a real old volume is unknown.

- [x] On bf36bfa9 and the combined importer with c6899cdd, both Node 22 and
  24 reported the census ready and bundled the owner. A three-byte BLOB
  became the string "97,255,98". An ill-formed three-byte TEXT value became
  the string "a�b". Valid text containing those literal characters produced
  identical live rows in each pair. An embedded NUL remained intact. All five
  source databases passed SQLite integrity_check on each runtime and tree.
- [ ] Two ordinary bundle regressions currently fail on bf36bfa9. Both require
  the owner with the unreadable collection value to be withheld and counted.
  Valid comma-separated text, a real replacement character, NUL, leading BOM
  and all supported SQLite encodings must keep their original meanings.
- [ ] Reconcile original-store validation with the archive projection and
  the shared SQLite reader. The latter also serves callers that legitimately
  read binary values. Check source bytes before a filtered archive can
  transcode them, and the actual materialized revision before emitting rows.
  Preserve private cleanup and explicit refusal results for older runs.

### Encoding of a filtered SQLite archive

- [x] The earlier three-encoding checks exercised the row reader. The ordinary
  census and builder had a separate failure when a valid UTF-16 store needed
  a filtered archive. SQLite requires attached databases to have the same
  text encoding. The new empty destination had defaulted to UTF-8.
- [x] Two new ordinary tests failed for that reason on 094d29e7. The archive
  writer now reads the source encoding through the immutable connection and
  sets it before attaching the source. The tests passed for both byte orders,
  including original bytes, a leading BOM, embedded NUL, non-BMP text, and
  omission of the dropped approval table. The two lossy-value regressions
  above still fail and are a separate open finding.
- [x] On 85413357 the full Node 22 importer had 170 passing cases and
  exactly the two open collection-value failures. The seventeen-stage
  rehearsal from 661d8798 plus this source and these regressions had 229
  passing and the same two failures on Node 24. Full platform typechecks
  passed on both trees.
- [x] Independent normal builds with UTF-8, UTF-16le and UTF-16be kept
  the source encoding, storage class and raw bytes in all 12 filtered
  collection databases on each Node runtime. Legal BOM, NUL, non-BMP and
  replacement characters survived in the live rows. A malformed surrogate
  in either UTF-16 byte order still produced a different live string.
  Thus the remaining loss occurs when projecting the live row. SQLite
  preserved the bytes in these filtered archives.
- [x] A disposable reader using SQLite's actual storage class, raw TEXT
  bytes and a fatal BOM-preserving decoder returned 11 generic values and
  11 declared-text verdicts for each encoding on each Node runtime. It kept
  real binary values for generic readers. Two runs per reader and runtime
  over 25,000 four-field rows produced identical output digests, with
  overlapping timings on Node 22. This was a prototype, outside production.
- [ ] Bring the declared live text types and per-owner refusal together with
  the shared projection on c6899cdd. Review generic readers of fleet and
  erasure facts before changing the common decoder. Keep the two explicit
  failures open until census and writing agree on the actual archived revision.

- [x] A permanent positive control on the same source passed on Node 22 and
  24. The actual writer preserved both legitimate strings that collide with
  the malformed examples, with unreadable bytes in an unconsumed extra column
  of a synthetic fleet database. Independent runs with null, ordinary TEXT,
  ill-formed TEXT and BLOB in that unused column each completed with three
  public owners and one private owner on both runtimes. A decoder that
  indiscriminately rejects every unused legacy column would change this
  behavior. Validate the fields a consumer actually interprets. Keep the
  ordinary controls for owner, fleet and erasure readers when changing them.
