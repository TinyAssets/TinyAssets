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
clear the block separately in grant management. The decision is staged at
`e754f7e4`. Record the intended promise
before approving the disconnect screen or relying on automatic retirement.

The registration description also changes generated source. The first joined
guard found the owner door map stale. After using the canonical generator,
all six generator steps ran on joined `eb627bb76` and left the checkout
clean. The standalone fix staged at `ce1abb4f` passed 45 Doors cases,
8 registration-card cases and 2 focused ledger cases. Its seven official
tools recordings matched the original bodies after accounting for only the
two intended description edits. The protected app listing was identical.
The single invocation covering seven guard files on the joined tree was
killed under load before reporting its 118 results. On the earlier joined
tree, 53 of the 54 cross-area cases had passed, with the generator case the
sole failure. The exact earlier 64 layer checks and seven invariant checks
are reported separately above. The later documentation-only rehearsal
`b7645b538` passed shape, OpenSpec and seven repository invariants, with the
separate Chrome-only invariant skipped.

The signed import prefix change also preserves a historical distinction:
12 actual writer outputs agreed on the folder grammar, while an envelope
from before the operation field still gets its existing terminal refusal.
On the source staged at `e0a847e3`, the full importer suite passed 167 cases
on Node 22 and the four disjoint Doors Worker selections passed all 22
import cases. Scoped Node 22 TypeScript passed on the four changed source
and test files and their dependencies. The full Node 22 workspace typecheck
subsequently passed on the same tree after the first attempt exited 137. Durable discard across eviction is a separate open protocol.

## Where to continue the dependency review

On `eb627bb76`, the scoped source inventory contains 201 tracked TS or JS
files under platform workspace `src/` and `sandbox/runner/`, excluding test
files and test directories. Thirty-five differ from main. The six additional
paths since the 29-file review are the two contract registration sources, the
generated owner door map, and Doors' binder, dispatch and egress sources.
Their registration and retry findings are above. App JS, deploy, import host
and the website remain separately scoped.

Using the TypeScript compiler to resolve all 1,265 relative import references
in that inventory, 137 of the 166 unchanged files directly touch a changed
file in at least one direction. Changed files import 127 unchanged files;
61 unchanged files import changed ones. The categories overlap. The refusal,
limits and digest modules have respectively 17, 16 and 12 distinct changed
importers. Start the next unchanged-source review with those contracts and
the app and connection callers. Then follow the remaining edges and the
separately scoped consumers. This inventory includes type dependencies.
An edge count cannot establish that an underlying decision is sound.

## What the next pass found

At 13:44 Pacific, GitHub advertised 96 heads, including 77 Codex heads.
Compared with the 13:19 snapshot, 88 kept their commit, two moved, six were
new and none disappeared. The comparison below used main `f934871ad` and
combined source `b4ebe1371`, with synthetic local accounts and resources.

| Area | Finding and evidence | Disposition |
| --- | --- | --- |
| Gateway | The slot pool and gateway both converted a full pool to the same refusal. Real local contention returned the same HTTP 503, one-second retry and response body on Node 22 and 24. All 32 gateway tests in the affected files passed. | `3f3e2343` removes the second conversion. The pool still releases its units and the gateway still owns its public response. |
| Import host | The import job hashed bundle data and backup files independently of the digest it already used for building imports. The full importer passed all 167 cases on Node 22 and all 167 on Node 24. Nine lengths through two file-path encodings agreed with Python, including an empty file and a partial final chunk. | `2bd83dbb` uses the existing digest while retaining the host's bounded reads and signed-request behavior. The existing C6 guard has its original scope. |
| Account app | Rooms displayed a prefix as a full message. The existing owner read could recover every byte of a 46,005-byte example, including 21,164 bytes beyond the first part. The app never requested that continuation. Mark all read required a click. The board, room heads and file browser also showed the first page only. | The first-party acceptance criteria are staged at `ce273c61`. Reading the complete message and explicit bulk acknowledgement are separate user actions. |
| Scoped file sync | A real owner schema and real runner saw 1,000 changes outside the client's scope followed by one readable directory. The first sync finished without applying that directory. The next sync created it. Both Node versions reproduced this on both revisions. | A page can advance the owner's raw cursor while returning zero visible entries. Resolve completion, bounded work under continuous writes, reset and existing runner images before changing the sync contract. |
| Sign-in | The documented limit is 4,095 UTF-8 bytes. The owner accepted a synthetic 4,096-byte emoji string and a 4,098-byte CJK string. Owner and runner were counting UTF-16 units. The private socket decoder also needs to handle a character split across byte fragments. | Review owner, inbox, runner and framing together. Keep the separately tested renewal verbs and terminal privacy boundaries. |
| Grant retention | Two existing Worker cases at `e754f7e4` passed. They confirm that a retired revocation keeps the older sign-in cutoff. | The founder must decide whether ordinary per-app consent plus a fresh sign-in suffices or whether the account holder must first explicitly unblock the app. |

A second deployment comparison ran the unmodified main scenario definitions
against both implementations with the test world's local transports. A
recorder around the real `perform` call observed **696 matched calls** in
122 named cases across 15 modes, including 265 fake apply calls. All 466
successful plan arrays matched exactly. Of 230 refusals, 227 matched
exactly. Two differed only in the random temporary plan directory and one
only in the generated operator message ID. No other field was normalized.
The baseline harness passed 179/179. On the combined kit, 178 of those same
179 cases passed. The old lifecycle assertion expected a PATCH after an
incomplete hook response; the staged kit refuses that response before PATCH.
That assertion calls the lifecycle helper separately from the 696 `perform`
calls. These counts describe the main-commit harness. B8 and B12's
historical 342-scenario criterion still needs its original case mapping.

The task list contained three citations to original research files absent
from all 96 live heads and 132 distinct local tips. The importer now has a
tracked entry point in its CLI and the three merged slice PRs (#5461,
#5475, #5477). The owner constructor decision points to the coordinator's
B3 record in `o2.md`, and R5 points to §N in the remaining-build decision.
§N7 already settles the defaults for imported owners. The translation,
archive, and founder's wording for the owner notice each retain their own
acceptance criteria. This repairs the working plan without reconstructing
the original unpublished research.

## Follow-up from the first-party callers and release review

At 14:12 Pacific the branch inventory contained 105 refs including main and
86 Codex heads. Main still pointed to `f934871ad`. The following work is
staged separately. The scopes and exclusions above still apply.

| Consumer | Confirmed result on the staged tree | Next dependency |
| --- | --- | --- |
| Rooms | `2ec78fcc` lets the owner read the remaining text using the mailbox's byte cursor. The independent 144,010-byte message reconstructed exactly. All eight selected Chromium and local Worker journeys passed. On main the same test reached the genuine message and failed at its missing button. Reading and acknowledging remained separate and later mail survived the acknowledgement. | Finish board and collection paging and the two directory views. The product question about intentionally acknowledging a partial message is still open. |
| Talk and subscriptions | `a08764ca` keeps the live viewer through pending initial and reconnect tickets. Seven local Worker and Chromium cases passed. `b64d8c33` keeps status and cancellation replies attached to their run. Three browser regressions failed on main and passed on the change. The joined tree `eea2e2086` passed all nine follow and Talk cases. | Two initial sends can still create different conversations. Recover the original submission identity before sending later input. Check retention when its result is uncertain. |
| Private sign-in input | `e0067de9` enforces the 4,095-byte UTF-8 limit through the owner and runner. Six HTTP, 30 related owner, 140 sandbox and 64 guard cases passed, as did TypeScript. Three real local terminal inputs at the limit were delivered and three larger inputs were refused. All 66 verdicts from 99 owner-memory measurements passed. | Exercise the privileged runner and the final joined recorder on the release tree. |
| Sign-in output and deployment probe | `bb863130` rejects missing codes in the Codex and two Kimi pair checks. 140 sandbox tests, 64 guards and TypeScript passed. At `a632c592` the four source-proved emitter fixtures produced 496 refusals and 56 accepted wrong fields on main across 1,466 split points. Keeping complete lines in a local experiment recovered every field on Node 22 and 24. | The fifth emitter, pinned Claude, hands off to a native binary. Establish its prompt boundary, then design the shared bounded reader for the runner and probe. No real sign-in or paid build was used. |
| Service refusals and sync | At `c272c6e5`, 143 codes and four transport faults exercised the shared public refusal handling. The focused 52 contract, four CI and 17 Gateway Worker cases passed. `114be3b2` records the real filtered-feed counterexample on both Node versions. | Scope sync needs an owner-observed completion boundary during continuous writes. Keep old runner compatibility in its replacement and handback. |
| Structural CI | At `24c3bb9b`, the runbook now describes checking the current checkout. The old revision flag was ignored. A review of 99 heads and 13 open PRs found no executable caller using the flag. All 82 focused tests passed. Main and candidate ran the same 780 test IDs across 41 guard files, with 761 passes and the same 19 failures. | Coordinate this process change with the existing process PR. The full-run failures comprised 13 socket restrictions and six legacy actor assumptions in this environment. |

The combined deploy kit at `b4ebe1371` passed its own 188 current harness
cases. Seven split architecture-guard files on that build passed all 118
cases. Those results supplement the 696-call comparison described above.
The historical 342-case comparator ran outside the repository in the
original builder's workspace. [B8](https://github.com/tinyassets/tinyassets/pull/5553)
records 57 successes and 285 refusals. [B12](https://github.com/tinyassets/tinyassets/pull/5569)
records both plain and Actions environments including masks and route summaries.
Today's call comparison does not reproduce those fixtures or certify a cutover.

The local combined build `c20aec73b` retained both the scope-sync and
prompt-framing pre-pin tasks plus the completed sandbox status-log task.
Nine follow and Talk checks passed on Node 22. The first joined thirteen-case
browser run with the installed Chromium passed twelve and its tab crashed
during the folder-save case. The two initial setup attempts without the
installed browser path provide no product result. Further combined suites
and the four-family behavioral comparison remain tied to their exact trees.
