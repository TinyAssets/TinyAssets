# automaton-states as built (2026-11-22)

#5796 left `AUTOMATON_STATES` (8,192, `contract/src/sync-ignore.ts`) with no derivation: its time bound held a sync-out to E2B Hobby's hour at the tiers' 500,000 entries, and neither the deadline nor the entry ceiling exists (`matcher-ceiling.md`). Fact 15 keeps a ceiling only as a recorded technical ceiling with its reason.

1. **The research** (Node 22.22.0, the template's Node, on this host; the matcher's code with the ceiling lifted, in a scratch copy, deleted).
   - **Time forces nothing.** `matcher-ceiling.md` stands: no session deadline ends a sync-out and no ceiling bounds its entries, so a slow match is said past `SLOW_MATCH_MS` and refused never.
   - **`LIST_BYTES` bounds the states.** A token is at least a byte of its line, and a line's own state is its newline, so a list has at most a state a byte: 6,291,456 at `LIST_BYTES`.
   - **The masks alone would fit.** Compiled and settled, the automaton holds 43.5 to 46.9 bytes a state (its masks, 32; its rules), at 65,536 to 1,048,576 states. That is within `HEAP_PER_LIST_BYTE`'s 48 at a state a list byte. The 9 bytes a list byte `LIST_BYTES`' row gave automaton lines was mostly lines held unapplied past the cap.
   - **Two costs do not fit.**
     - **The folders.** Each folder of the path last asked keeps the path automaton's D and I (`Folder`), ⌈S/32⌉ words each. Measured: 0.254 bytes a state a folder at 131,068 states, down 2,043 folders 68.0 MB. A workspace path is at most `MAX_PATH_BYTES` (Linux's `PATH_MAX`), so at most 2,043 folders deep. At the states `LIST_BYTES` allows, that is 3.2 GB.
     - **Compiling.** A token's set and its line's parse come to about 360 bytes a state at the peak. 1 MiB of automaton lines needed between 360 and 400 MiB of old space, and 98,304 states needed 44 MiB.
   - **So memory forces a ceiling**, and the folders set it. The compile's peak is lower a state and is gone before a path is matched.
   - **The walk's own folders.** `AGENT_PEAK` was measured three folders deep. `walkFiles` holds 14.8 MB at 2,000 folders, beside the matcher's.
   - **Found in review: the walk's figure was not its worst.** It was measured over folders of one entry. Node's `opendir` reads a batch of 32 names, each a `Buffer` of its own, so an open folder holding 31 more names of 250 bytes took 17.9 KB where one of one name took 2.4 KB. At every folder of the deepest path that is about 30 MB more, past the margin: (88.6 MB − 47 MB − 1.1 MB) / 510.75 is about 79,000 states. Read one entry at a time (`bufferSize` 1), an open folder takes 2.4 KB whatever it lists; 200,000 entries took 224 ms to list where they took 177 to 186.
2. **The choice: the ceiling derived from the heap.**
   - The value is (88.6 MB `LIST_BYTES` leaves − the walk's 15 MB − the folders' own 1.1 MB) / 510.75 bytes a state at the deepest path, about 141,900. That is rounded down to 98,304 (3,072 words), a margin of 1.30. 131,072 would leave 1.07, under the near-ceiling rule's 1.25.
   - **Weighed against no memo of folders** (each path stepped from the root, the state O(W)), so that `LIST_BYTES` alone bounds the states. It trades the ceiling for every path costing its whole length, which is the cost the memo exists to remove (`REMEMBERED_NAME_BYTES`).
   - **Weighed against two ceilings** (a name's automaton keeps no folder's state). That is one more limit for a list of name patterns past 98,304 states. The one ceiling is held at the dearer automaton's cost.
3. **As built.**
   - `contract/src/sync-ignore.ts`:
     - `AUTOMATON_STATES` is 98,304, its comment the derivation's terms.
     - `unappliedLines` goes: its one product caller is gone, and the tests read `ignoredBy(lines).unapplied`.
     - `REMEMBERED_NAME_BYTES` no longer cites the time budget.
   - `owner/src/files.ts`: the notice's check for automaton lines goes. It reads only an inline list (`INLINE_BYTES`, 4,096), which has at most a state a byte, so the check could never fire, at 8,192 as now. The run's report says those lines, and the app shows them. The spec's sentence says so (`specs/doors/spec.md`).
   - `sandbox/runner/sizing.ts`: `AGENT_PEAK` is restated. It was measured over 500,000 entries three folders deep, it grows with no entry count (`sync.test.ts`), and only a path's depth adds to it, which the states' test measures.
   - `sandbox/runner/sync.test.ts`: a new test (`large`). It takes `AUTOMATON_STATES` of path lines at a state a list byte, matched down a walk to the deepest path the host allows, the walk's folders with it, and holds the result to the room `LIST_BYTES` leaves. It measures 71.4 MB against 93.3 MB.
   - `contract/guards/sync-ignore.test.ts`: all 310 templates together apply whole. The states test's list is sized from the ceiling.
   - `sandbox/runner/fsafe.ts`: `entries` reads an open folder one entry at a time (`bufferSize` 1), as `walkFiles`' doc already said, so the states' test's chain of one-entry folders is the walk's worst at any depth. `sync.test.ts` holds it: 400 folders each listing 31 more names of 241 bytes hold under 512 bytes a folder more at the bottom (70 to 101 measured).
   - `runner.test.ts`'s filler is sized from the ceiling.
   - `doors/guards/app-words.test.ts` passes its own figures, the words being the app's.
   - `limits-audit.md`'s row carries the arithmetic.
   - **The bundles.** The constant and the walk are in both bundles, so the runner and the probes change, and the pair tag with them. The next deploy builds and checks the pair as for any runner change.
   - **Cost.** A byte of text costs up to 3,073 word steps where it cost 257. The contract guards' hostile lists, sizes tests, take 3.6 to 4.1 s each locally where they took 0.5 to 1.3. The walk-cost test, a medium one, takes 1.1 s where it took 0.3 (budget 4.6 s, times 4).
     At 1.4 ns a word step at 3,072 words (this host), a byte of a name costs about 4.4 µs, so a sync-out over the disk's 3,788,096 inodes, each a distinct 255-byte name, matches for about 70 minutes, where 8,192 took about 6. It is said past `SLOW_MATCH_MS`. The sync agent is a process of its own and its requests have no timeout, so the run's heartbeats and its lease go on beside it. Paths of one short name each over 98,160 states, where the copy of a folder's D and I is most of a path's work, held the cost guard at 0.35 of its bound.
4. **Proof.** Each mutation was run and fails.
   - `AUTOMATON_STATES` doubled fails the states' test (126.7 MB against 98.0).
   - The folders keeping three words a word of I fails it (121.8 MB against 93.3).
   - 8,192 again fails "all 310 together" (56 lines said).
   - `runner.test.ts`'s old 900-line filler fails "says the sync-ignore lines past its ceilings" (no event).
   - Node's batch again (no `bufferSize`) fails "walks a folder an entry at a time" (2,924 bytes a folder against 512).
5. **Left.** Nothing in this PR's scope. One thing outside it was found in review, after merging #5798.
   - `sync.ts` `goneFolder` walks a gone folder `sorted`. That reads each folder on its way whole, and holds it while the walk is under it, so sync-in's heap grows with a folder's entries: about 300 bytes a 106-byte name (14.9 MB at 50,000, Node 22.22.0), so about 1.5 million names fill the agent's heap with no list at all. The walk it does sits beside the matcher's folders at `AUTOMATON_STATES`, which this derivation counts at one entry a folder. It is owed below.

- [ ] live: the next deploy's pair job passes with this PR's runner and probes (the standing checks run once on the pinned image, as for any runner change).
- [ ] fix/gone-folder-walk: `sync.ts` `goneFolder`'s walk holds what a gone folder lists (`sorted`), so sync-in's memory grows with a folder's entries; walk it an entry at a time, or bound what it holds, and hold it in `sync.test.ts` beside the states' test.
