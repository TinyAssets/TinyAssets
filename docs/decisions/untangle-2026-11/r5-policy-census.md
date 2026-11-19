# R5 policy census as built (2026-11-18)

The existing offline census reports how many policy documents the new validator would archive. That number cannot tell whether an owner configured rules, spending limits or an egress ask. The census now also reports a fleet-wide `legacy_policy` tally, using the policy rows that the bundle builder reads. Run the existing `census` command against a verified backup copy to obtain actual counts.

The old broker's provisioner in `tinyassets/broker/policy.py` stored an empty entries array, four null spend settings and an empty map of source ceilings by default. For the subset of the document the tally reads:

- `with_entries` counts documents with explicit entries; `entries` counts the stored rows. Explicit settings can repeat a default. These numbers describe configuration, not which decisions would change under translation.
- `with_spend_settings` counts documents carrying at least one spend setting; `spend_settings` counts each non-null daily, monthly, transaction or ask threshold and each named source ceiling, including zero.
- `with_egress_ask` counts an explicit ask. A legacy document from before egress existed still contributes its stored entries and caps.
- `classified_documents` counts documents with recognisable sections. Other known policy rows, including malformed documents or ones on a tree the survey cannot read safely, contribute to `unclassified_documents`. Store and backup failures remain visible through the ordinary census checks. An owner's oversized file alone does not hide their policy. Withheld owners are excluded.

No owner ID, rule contents, source, cap amount or destination is included in these counters. The archived bytes, bundle and admission policy are unaffected. Translation of the action defaults, the archive disposition and the founder’s notice wording remain on P-import.

## Shared text reader at integration, November 19

- [x] Rechecked the policy reader and both consumers against the staged verbatim-row change. The rehearsal passed the documents from the census into the builder. The later archive-revision check below supersedes that instruction for integration. Its account reader must select named columns through the shared `tableRows` implementation, including its explicit collection ordering.
- [x] The ordinary regression on `a248b68e` distinguishes the two integrations on Node 22. The five-stage rehearsal at `70110c7d` without the shared text reader truncated an unrecognized stored document in its readable archive, even though its ordinary Node 24 run passed that case. Adding the verbatim reader to the same five stages passed all 181 ordinary importer cases and the full platform typecheck on Node 22. Five independent synthetic documents per source and runtime confirmed both the readable file and the separate archived SQLite copy, recorded at `bf36bfa9`.
- [ ] Real representative census, legacy mapping decisions and integration on main remain open.

## Archive revision review, November 19

- [x] On 93f714d1 the normal bundle writer reads policy text before copying the
  SQLite archive. In a controlled frozen-copy fault simulation on Node 22 and
  24, the input changed during a carried file read. The same exported bundle
  then held the earlier refused-policy text and the later SQLite document.
  The ordinary regression failed on 93f714d1. Main f934871ad and the staged
  archive-row implementation eeee6bba kept both copies together for this
  ordering. The inverse collection-row ordering fails on main.
- [ ] Hold the integration that forwards previously read documents to the
  bundle writer. Live and readable policy output must come from the actual
  archived database using the verbatim-text reader. The census must still
  report policy settings for an owner refused before building and retain both
  the unreadable-policy and size findings in its existing regression.
  Confirm the counts for those owners and compare the reported classification
  with the bytes actually published for owners that can be built.

The regression is published at 70f30b5c. The ordinary 48-case policy census
suite on 93f714d1 passed on Node 22, including both pre-fit controls, while
the new writer regression failed. Additional Node 22 and 24 runs with actual
bundle output crossed the two validation outcomes. When the archived
document changed from valid to unrecognized, 93f714d1 emitted one valid
live policy and no refused file. In the reverse direction it emitted no live
policy and a refused file. The archive-row writer eeee6bba agreed with
the archived database in both directions on both runtimes.

## Reconciliation with the archive writer

- [x] The builder reads policy text and collection rows from the SQLite files it
  puts into the bundle, using the staged verbatim reader. The census classifies
  those exact documents for a built owner. When an owner fails the size check
  before a copy is built, the census reads the store identified by the survey.
  A policy read failure retains the size finding as well as the bad-store finding.
- [x] The previous policy revision failed the permanent writer regression. The
  corrected tree passed all 72 bundle and census cases, then a new census
  regression passed separately after a document changed during the build.
- [x] Verify the complete importer, platform types, and the joint rehearsal on
  the published source. Representative data and the founder's mapping remain open.

On 5b05e6bf all 172 ordinary Node 22 importer cases and the full Node 24
platform typecheck passed. Both new revision tests failed on the previous
head a5e6aa8e with its writer and classifier. In independent Node 22 and
24 builds, valid-to-unrecognized and unrecognized-to-valid transitions both
produced the live policy or refused file selected by the archived SQLite copy.
The thirteen-stage rehearsal (twelve stages at 0d09d898 plus 5b05e6bf)
passed all 216 importer cases, 25 Owner import Workerd cases on Node 22,
and the full platform typecheck on Node 24. Preserve the existing short-read
helper at integration and the F1 precheck when reconciling the census imports.
The next F1 change addresses a separate stale collection byte measurement.

## Missing columns in an otherwise readable account database

- [x] On 01d33a71 four ordinary regressions failed. SQLite integrity passed,
  but a missing policy document, collection body, sequence or row identifier
  stopped the dry or writing builder. The combined importer also stopped the
  census for a missing collection body or identifier on both Node 22 and 24.
  This used altered synthetic databases. The original Python stores declare
  these fields. Their absence in a real backup has not been established.
- [x] The survey and the builder now use the same projected and ordering
  columns. An owner with a known missing column is counted as bad_store.
  Other owners proceed. The size check can still report an oversized file
  for the same owner. The four ordinary owner cases and existing combined
  size and unreadable policy regression passed on Node 24.
- [x] Source c6899cdd passed all 176 ordinary Node 22 importer tests and the
  full Node 24 platform typecheck. On each of Node 22 and 24, 32 independent
  comparisons exercised dry and writing builds, ordinary and rehearsal names,
  public and excluded owners. All six required columns, an extra compatible
  column and a case-only rename were covered. Folder counts and index keys
  agreed with the published-owner counts.
- [x] With the seventeen stages documented at 67c22ccc all 227 Node 24
  importer tests, 25 Node 22 Owner import Workerd cases and full Node 24
  platform types passed. The same 32 independent cases passed on each runtime
  on this tree too. The first rehearsal dropped the survey's quoted-identifier
  import, failing 133 importer cases and typecheck. Restore that import
  alongside modifiedTime and tableRows. At the measurement overlap keep the
  F1 precheck and append the known missing stores for private and public owners.
- [ ] Integration on main and a representative frozen copy remain outstanding.

## Reading live text from the filtered archive

The live projection declares which fields require text. SQLite supplies their
native bytes from the archived database. The importer decodes those bytes in
the database encoding and counts bad_store for the affected owner if a field
is binary or contains malformed text. Fleet readers retain native binary
values and their existing behavior for unused historical fields. On a
collection refusal, census uses the policy documents already read from the
same archive. An early size refusal still needs its separate source check.

Two ordinary regressions failed on 661d8798. Both passed after this change,
along with the archived-policy revision and combined size/refusal controls.
Full-suite and combined verification are pending. Historical cohort and fresh
deletion comparison remain open.
