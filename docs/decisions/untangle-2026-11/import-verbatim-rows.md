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
