# Midday untangle review as built (2026-11-18)

Main remained at `f934871ad`. The review uses the morning cross-area recheck,
then integration commit `533733c40` (tree `b38fa6c8b`) and the later
queue, sign-in and callback review at `5dd287f41` (tree `24b68531e`).
These are disposable review builds. The reviewed changes are staged.
The coordinator owns the merges. Keep untangling first until wave 2 §E
passes, repeat after merges, and repeat on the actual pre-cutover build.

## Scope and what the counts mean

The morning report compared 50 staged heads over 900 paths and compared the
other 23 branch heads to their actual merge bases. Of the 900 paths, 872 had
a single expected result, 25 reproduced an automatic merge and three needed
semantic reconciliation. See `nov18-recheck.md` on the staged review branch.

At the 12:55 Pacific snapshot, GitHub advertised 82 distinct heads, including
main and 63 Codex heads. Of the 74 earlier refs, 68 retained their exact SHA,
one moved, and five were retired. Thirteen new names appeared. Thirteen PRs
were open, comprising the twelve rebuild drafts #5679–5690 and legacy #5343.
The latter's outside-grants requirements inform the rebuild. Its code belongs
to the retiring stack. Claude's R5 records the migration lock, P2 waits for
the coordinator's ruleset change, and the files and renewal heads are already
under review. A vanished ref does not invalidate its earlier observations.

Of the 63 Codex heads, 53 have an identical whole-tree object in the combined
build's history; subsequent merges can still alter those files. I compared each changed path on the ten remaining heads
against that build. Eight differ only at the shared task list, builder rules
or CI workflows; the file-index census also contributes to the shared owner
import test and import identity intersects the shared contract and import
window. A tree comparison establishes which content was integrated. It does
not establish that the resulting design is correct.

On `533733c40`, the rename-aware diff has 947 entries, or 948 distinct
old/new paths with rename detection off. The changes remove 697 historical
OpenSpec paths, including a procedure preserved at
`docs/ops/daemon-image-retention.md`. Independent reviewers traced all 29 changed
non-test runtime files under the Worker/source `src` folders and the sandbox
runner TS files: nine owner, thirteen doors/contract/codemode/files/gateway,
and seven sandbox/sources. That denominator excludes the app JS, deploy kit,
import host, site, workflow logic and all unmodified files. Those consumers
need their own review. I also parsed all 30 changed workflow definitions:
20 had identical parsed definitions and ten changed executable values.
The ten concern static-site and image inputs, import modes, incident
attribution, copy/retirement scheduling, test relocation and pruning. This
comparison cannot validate external permission, queue behavior or live time.

The Step 4 headline still sent builders back to O1–O5 on main. Its own
children and merged source show B3 removed OwnerState, O2 split the door
tables (#5662), O3 bound the callers (#5665), and O4c shipped the live-name
guard (#5673). Deploy X1b, staged in #5684, was the only unchecked *checkbox* under that
headline on main. The surrounding prose also assigns B12 mode retirement, B24
manifest and B26 spec obligations and leaves cross-room mail-chain semantics
unsettled. The plan now directs builders to those decisions and the separate
project-wide recheck. The parent remains open.

## Decisions that change the next build

| Area | Evidence at the reviewed commits | Dependency and disposition |
| --- | --- | --- |
| Owner | The actual sender and SQLite ledger on main and the combined build accept the result of an old callback after its replacement. Seven scenarios per tree covered refresh, removal/recreation, rewrap, pending dispatch and notice delivery. Ordinary erasure has a separate gap after its final SQL wipe. | Keep wake changes behind an owner-defined subscription lifetime. Rewrap must keep the lifetime, replacement must change it. Before claiming erase or targeted restore complete, give Files a durable completion obligation and fence previously admitted writes, including browser uploads. |
| Doors and contract | Imported owner/folder identity, wake IDs and event kinds were reviewed with their writers and public consumers. `727b718b` accepted 12 historical indexes; 197 importer checks passed when the earlier staged fixes were joined. The registration tool still describes fewer methods than the installed contract. | Preserve historical identity and signed-message boundaries. Check discovery through each profile before extending wake kinds or onboarding. |
| Sandbox and engines | Both continuation paths discarded the saved vendor session after reboot. At `6aea8136`, 80 offline invocation comparisons and the Worker dispatch check passed. The privileged CLI test still needs CI. The validator accepted contradictory sign-in declarations in the four tested cases on main. The sign-in change at `173cb187` rejected all four and passed 355 source tests on its own tree. | Use recorded session identity. Resolve surviving PID reuse and deferred/promptless resume before treating restart recovery as complete. Validate source record declarations before adding engines. Paid builds and vendor sign-in remain separately gated. |
| Services | Three gateway tests maintained independent time loops. The change at `03ce5d89` passed 50 Worker cases and all three test controls failed when the actual cancellation was withheld. Files and sandbox adopted shared digest implementations in separate staged changes. | Land all digest adoptions before removing the now-unused exception helper. Empty lists in a rehearsal do not empty the lists on main. |
| Cross-area, CI and release | CI attribution at `dddeedb9` has the same tree as the locally tested fix: four old failed attempts each identified the right single job. Routed egress is unavailable at dark deploy. A recent manual run or future timestamp can satisfy the proposed clock watchdog. | Exercise egress after routes and restricted admission. Require independent scheduled tick evidence before enabling the watchdog. Process test moves wait for the trusted base to recognize their new locations. |
| Import and storage | Stage, dead-letter removal and the signed operator use the same queue lookup. Duplicate names with different IDs silently selected the last row. The repair checks complete inventory. In 29 independent offline cases, all 18 bad inputs across these three callers stopped before a write. Backup-key retirement still infers destination history from job evidence that can be incomplete. | Fix resource identity in the shared reader. Give each backup destination a durable record before its first possible write; until then keep the old Files key. Producer identity across an interrupted discard, true census and owner policy mapping remain open. |
| Site and account | The staged site consumes connect and profile contracts. At its cutover boundary, the account page promises deletion that it cannot request, and Commons still calls a graph tool absent from the three rebuilt profiles. Archive text once promised an upgrade even when the owner cap would forbid it. | Pair the site handoff with routes, a reachable deletion flow and the public snapshot source. The archive notice correction is staged at `a2aaf0be`. A real purchase needs both a larger eligible tier and a working billing door. |
| Future streams and rooms | A projected second archive on the real restore path independently drove the mail cursor to 900 instead of 12 while the file version stayed 5. Today's production schema is mail only. The archive breakdown also attributed both archives to mail. | Before writing new stream data, settle cursor ownership, restore and storage accounting together. Opaque mail membership must preserve retired-agent history and first-message discovery. Keep existing app collection identifiers separate. |

## Remaining plan and acceptance

The current plan spans 28 major headings and subheadings. Its checkbox count
also grows when a review uncovers a missing dependency. The completion gates
are wave 2 §E: fresh searches across all five areas, zero owed entries, the
layer check without exceptions, and the full suites, behavioral recorders,
deploy plan and frozen SQL with any intentional differences explained.

1. Re-review the affected source, independent callers and actual regression
   cases for each finding above before merging. Refresh the exact branch
   inventory after each merge batch. The runtime diff review leaves unchanged
   modules, historical edges, site consumers, import host scripts, and parts of
   the cross-area and post-cutover plan open for deeper review.
2. Keep full behavior recording and privileged coverage tied to the exact
   tree. On `533733c40`, wire (804 lines) and tools (7 lines) matched the
   baseline; the 50 gateway and 140 sandbox checks and platform TypeScript
   passed for the same source. Independent branch evidence survives. It does
   not substitute for the outstanding joined recorder, plan or frozen SQL.
3. Re-review the remaining Step 19 and 20 dependencies before starting their
   features, in particular store/mobile identity, provider grants,
   scheduled-task identity, streams, devices, realtime match hosting and archive
   migration. Preserve founder control of vendor enables, external
   submissions, backup costs and cutover. The meaning of automatic mail across
   rooms remains an explicit product question.

Nothing in this record authorizes routes, customer data, paid builds, key
retirement, rule changes or a final merge.

## Follow-up on the combined build

The 13:10 Pacific comparison used `9c619aff0` (tree `6d57bac8c`); the shared-install
decision later joined it without changing runtime source. The following checks
were run locally, apart from the separately named GitHub results.

- All 18 owner migrations ran on main and this combined build. The exact text
  of all 534 SQL statements and the SQLite catalog after each migration
  matched. The last catalog had 441 objects, including 283 triggers. All four
  owner migrations in the wave-1 freeze at `9ea1bbd2b` matched too. All 18
  hashes were in the staged lock; main still pinned 17. Both sandbox objects
  matched at every stage as well: three control migrations and one slots
  migration matched their respective locks. Use the reviewed migration-18 PR
  for the eventual pin.
- The actual deploy kit's plan matched main in 96 offline runs under the
  same main-commit harness: 48 resource states times the public and private
  read paths, yielding 16 distinct plans. States varied held resources, pair,
  routes, retired workers and the import window; an open window needs an
  existing doors Worker. This is a scoped comparison, not every historical
  harness scenario or an account observation.
- The joined tree passed 268 process cases. Its dependency check exposed the
  new app regression importing a private display helper from the test. On a
  checkout with ordinary dependency directories, 63 of 64 layer/kernel
  checks passed and this was the only failure. The revised test now uses the public account UI.
  On the repair staged at `4d104c94`, four shipped-app browser cases passed
  with local Workers, including the regression with the owner's actual byte
  count and effective cap. Twelve offline browser comparisons passed, and
  all 64 layer/kernel checks passed on the joined build `b5b14e446`. The 12 frozen-bound and migration
  guards passed.
- The full-body wake discovery comparison found six registrations with
  intentionally updated descriptions and one unchanged app listing on the
  intermediate candidate; check the final protected-session wording on its
  own commit. Future methods still need an owner acceptance and migration
  decision. The current MCP mechanisms for tasks work with explicit
  registration, but a scheduled operation and each run it creates have
  different lifetimes. A past-runs page returned 55,554 bytes against the
  50,000-byte tool bound. The plan now requires sizing and caller isolation
  before exposing it (`921fe024`).
- Step 20's history requirement has a measured boundary: on the main and
  combined owner SQL, inserting 2,001 settled runs pruned the oldest
  mail-to-run receipt. Decide what history the future app really promises
  before relying on pre-feature runs (`050c6430`). Shared-app installation
  likewise needs one current owner across owners, even when an old binding
  later revokes. Three historical SQLite cases passed; the build decision is
  staged at `771b7f0a`. External submission remains a founder action.
- Rechecking the exact GitHub head of each of the twelve rebuild drafts
  found both Tests and invariants successful. All twelve were drafts. The
  thirteenth draft was the legacy #5343. These were existing GitHub runs.
  The project-wide cutover criteria remain open.

## Scope refreshed after the fixes

At 13:19 Pacific there were 90 remote heads, including 71 Codex heads.
Relative to the 12:55 snapshot, eight names were new, one moved and 81
kept the same commit. The nine changed heads were compared with local
integration `eb627bb76`. Eight have their exact tree in its history.
For the queue-inventory head, all four paths outside the task list and the
shared deploy test matched its current tip exactly. The shared source-record
check and its tests also incorporate neighboring changes in the joined tree.

The Doors error-handling inventory traced 43 catch clauses, eight promise
catches and four explicit queue retries. Egress settlement could immediately
retry an owner reporting overload. The shared binder now distinguishes this
from a lost answer. On the staged repair `e56791a4`, three focused Worker
regressions passed. Both affected Doors test files also passed in full (81 cases).
An independent 27-case comparison used main, the earlier
combined tree and the candidate. On the real owner schema, an abandoned
reservation retained its estimated charge and replaying a successful
settlement charged it once. Other workers' error-handling inventories and
the full behavioral comparison on the new build are still open.

Two preparation requirements for the single F2 site release had landed under
Step 18, after the window. The corrected plan at `de6e3097` puts them under
the Step 5 site deliverable. The Step 13 publication still follows routes.
The ordering check failed on the prior branch and combined plan and passed
on the corrected plan and its rehearsal with site profiles. The standalone
OpenSpec validation passed. The graph source, deletion flow and handback
revision are still prerequisites.

An additional founder decision affects outside apps. The original MCP
grant requirements keep a disconnection until the owner clears it. Later
bounded grant storage permits an old, unheld revoked row to be retired.
The durable cutoff still blocks earlier sign-ins. The OAuth door requires
per-app consent and a fresh sign-in in either case. The open question is
whether that flow can suffice after retirement or whether the owner must first
clear the block separately in grant management. Record the intended promise
before approving the disconnect screen or relying on automatic retirement.

The registration description also changes generated source. The first joined
guard found the owner door map stale. After using the canonical generator,
all six generator steps ran on joined `eb627bb76` and left the checkout
clean. The single invocation covering seven guard files on that tree was
killed under load before reporting its 118 results. On the earlier joined
tree, 53 of the 54 cross-area cases had passed, with the generator case the
sole failure. The exact earlier 64 layer checks and seven invariant checks
are reported separately above. The later documentation-only rehearsal
`b7645b538` passed shape, OpenSpec and seven repository invariants, with the
separate Chrome-only invariant skipped.

The signed import prefix change also preserves a historical distinction:
12 actual writer outputs agreed on the folder grammar, while an envelope
from before the operation field still gets its existing terminal refusal.
On the local candidate `8f9489c1d`, the full importer suite passed 167 cases
on Node 22 and the four disjoint Doors Worker selections passed all 22
import cases. The full TypeScript attempt that exited 137 supplied no
passing result. Durable discard across eviction is a separate open protocol.
