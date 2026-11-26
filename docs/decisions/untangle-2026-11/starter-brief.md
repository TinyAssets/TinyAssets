# The starter brief and its board cursor as built (2026-11-18)

The run profiles omit all three mailbox tools. The owner's object also refuses those
calls from a run. A schedule supplies its saved input. An admitted mail run receives
only the message that started it. If admission refuses the message, it starts no run
(owner-object spec, “A run reads no mailbox” and the mail delivery scenarios). The
starter's shared rooms and schedule skills now describe those separate inputs. A brief
can still use the board. When its change cursor resets, the board skill saves the reset
cursor, pages the current board collection, and resumes changes from that cursor.

Main carries starter version 7. The owner reconciles a newer package and retains the
owner's own edits under its conffile rules. These two managed texts require version 8.
The directory plugin versions and both copies of each skill come from the existing
generators. The independent starter-release-version guard enforces this same rule. If
another managed version lands first, reconcile the changes and choose the next release
against the package on main. Observed behavior of an actual model on the stage is still
owed by BRAIN.

On the candidate at `6db507ad7`, a local workerd fixture used the owner's Starter,
Files, all 18 migrations and SQLite storage with a local R2 chunk adapter. A fresh
owner received the 14 exact v8 files. A settled v7 owner kept its board when offered
the changed text with version 7. At version 8 its untouched board updated, its edited
schedule stayed in place with the new text beside it, and its personal file stayed
edited. All 14 receipt paths survived. Repeating the pass left the receipts and
manifest charge unchanged. Across both passes there was one update notice.

The generator reproduced the committed files. The starter's 11 ordinary cases and
one tagged size case passed, along with five owner starter guards. In a disposable
join with the staged version guard at `ac79618d`, all six owner starter guards
passed. The isolated fixture uses a local R2 adapter for chunk bytes. The ordinary
owner pool at the preceding `2319b921e` exited 137 during startup. The live BRAIN
acceptance still requires observed stage behavior.

That candidate's room skill was 4,133 UTF-8 bytes, up from 3,979 on main.
It crossed the existing 4,096-byte inline limit in the files plane, adding a
chunk write during installation. The shorter wording states the same delivery
rule. The files plane keeps its existing storage policy.

The 4,039-byte room skill at `45c429950` stayed inline in workerd. A real Files
Worker on local R2 and two SQLite owner fixtures verified all 14 files, the
settled-owner update, the owner's edited and personal files, and the single
notice across two passes. A separate upgrade fixture ran the unmodified owner
test pool, its real owner entry and Files Worker, with the old seed on main and
the exact new package. It passed. The initial 23-case invocation exited 137. On
`5a718fa1d`, five disjoint original-pool runs then passed all 23 existing owner
starter cases: five base cases and four review groups of five, four, five and
four cases. The generator reproduced the tracked files; the 11 ordinary starter cases,
one size case and five owner guards passed. Six owner guards passed when joined
with `ac79618d`; reverting the joined seed number to 7 failed the version guard.

### The importer's tier check

The F1 index fixture used the real owner paths and migrations on Node 22 and 24.
From 10,066 indexed bytes both genuine seeds reached the same 61,988 peak and final
index charge. The ordinary storage tier counts different bytes. In a fresh workerd
owner the v7 seed used 71,540 stored bytes and v8 used 71,722. Both charged 52,402
index bytes.

The importer's existing admitted synthetic backup supplied six actual entries and
no collection rows. Its `measure` reported 1,742 stored bytes and accepted a tier
of 73,463. After those same entries went through the owner's `importFiles`, v8's
starter stopped at 13 receipts with a failure notice and reconciled version 0.
At 73,464 it reached all 14 receipts and version 8. V7 reached version 7 at
73,282 and failed one byte below. The successful index charge was 62,514 on
both versions. The cutover census needs the admitted owner's complete tier check
with the carried starter, its real writes and shared content. The existing F1
production dependency and representative census remain open.

### Two proposed packages already use version 8

- [x] Compared the 14 carried files on main `f934871ad`, the digest branch
  `7d314f374`, and OKF watcher PR #5692 at `3f8be4de9`. The watcher changes the
  knowledge reference. The digest changes the schedule and rooms-and-board skills.
  Both branches assign version 8. The watcher's deprecation note adds 335 UTF-8
  bytes and the two digest edits add 182. All other carried files agree.
- [x] Exercised both release orders in local workerd with the real Starter, owner
  file and import code, SQLite migrations, and Files Worker on local R2. After v7
  and either proposed v8, the union at v8 had no scheduled pass. The watch-first
  owner still lacked both revised skills. The digest-first owner still lacked the
  revised reference. Both had 14 receipts. A projected union at v9 reconciled
  all 14 current content IDs on both owners. Including retained file versions,
  each of these upgrades used 118,836 stored bytes.
- [x] On fresh owners with the same six imported entries, measured the minimum
  passing tier in bytes for main (73,282), watcher (73,617), digest (73,464),
  and the projected union (73,799). At each exact limit the starter finished
  with 14 receipts. One byte below each limit it kept 13 receipts, reported
  starter_failed and left the reconciled version at 0. These are results for the
  named synthetic backup.
- [ ] Before either package ships, the release coordinator must establish which
  package, if any, has already reached an owner as v8. If either v8 has shipped,
  give the combined package a later version and regenerate both plugin surfaces.
  Recheck migration from the released package and importer tier fit on the
  selected commit. The watcher still points to its prior upstream repository.

### November 26 current-main replay of PR #5692

Main at `d662e63e` now carries starter 10 with 16 files and both plugins at 1.0.10. The draft still proposes starter 8 with 14 files. The older two-v8 comparison above describes its original base.

- [x] Replay main's watcher against the draft's exact proposed spec and README. Both Git blob IDs matched the frozen upstream inputs. The watcher proposed starter 11. The normal starter and plugin generators kept all 16 paths, changed only the managed OKF reference, and emitted plugin version 1.0.11 for both consumers. The managed reference text matched the reviewed draft byte for byte.
- [ ] Regenerate the draft from current main if this upstream revision is still the coordinator's choice. Confirm the actual release base and owner upgrade before integration. This was a disposable projection. The draft and main remain at their observed versions.

Open watcher recovery finding on main `d662e63e`: the proposal step treats an existing branch as proof of a pull request. Four offline executions of the actual workflow step used synthetic command responses. An open proposal, interruption after branch publication but before PR creation, and a deliberately closed proposal all exited zero after only the branch lookup. With no branch, the control reached the mock push boundary. The actual #5692 is open. This finding concerns recovery after interruption, not an observed missing PR. Before changing automation, distinguish a matching open or closed proposal from a branch with no proposal, and verify the intended tree before resuming an interrupted create. Preserve deliberate closure and cooldown. No workflow or release behavior changed.
