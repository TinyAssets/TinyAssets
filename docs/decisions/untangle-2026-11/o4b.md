# O4b as built (2026-11-15)

O4b is what O4 (#5622) left owed to `untangle/o4b` (`o4.md` item 7), split at one fix boundary: B10's literal guard is O4c (`untangle/o4c`, item 6).

1. **B10's owner-test literals read the founder's record.** The owner tests' copies of the tier numbers (`runs: 5`, `runs: 10`, the free and paid storage bytes) in `files-quota`, `storage-full`, `storage`, `bodies` and `archive-mail` are `tiersAt(free, paid)` (`test/support.ts`): `TIERS` from `deploy/control.json`, with the storage figures a test sets. A test's own scaled figures stay its own. `owner/memory/probe.ts` already read `TIERS`.
2. **B6's owner adoption:**
   - `egress.ts`'s `HINT` and `SERVED` are the contract's `MODEL_NAME` (V2).
   - **The three range builders are one, `Files.range(chunks, from, to)`** (`F-H3′`, V2): content bytes `[from, to)`, each chunk read by the part the range needs, joined. It goes by each chunk's size, since a file's chunks are at most `CHUNK_BYTES` and not all equal. A segment's or a placed body's chunks are a stream cut every `CHUNK_BYTES`, `streamChunks` (`segments.ts`). The three joins went with the builders. A range past its chunks still throws, as `chunkReads` did; the other two callers bound theirs by the size first, as before.
   - **The inbound route's grammar** was copied in `doors/src/directory.ts` (which writes routes) and `owner/src/inbound.ts` (which takes them). It is `INBOUND_ROUTE` in `contract/src/ids.ts`, beside the other ids: lane C's file, three lines; lane D's file, its import and the copy deleted.
   - `holds.ts` names `EGRESS_BODY_MAX` (D1's rename).
   - Already done on main: `signins.ts` reads `signinUrls`, and `sessions.ts` reads `SESSION_TTL_S`.
3. **B7's owner items (d2 B7, `o2:2d`):**
   - `randomHex` for both `token()`s (`ledger.ts`, `registrations.ts`).
   - **One `OWNER` in `authority.ts`.** It is the owner itself wherever a party may instead be an outside client: a need only it meets, a write's files scope and a row's filer, a sandbox's authority, a room member, a mail's reader or sender, and a subscription's principal.
     - It replaces `OWNER_ONLY`, `OWNER_FILER`, `OWNER_AUTHORITY`, the ledger's `OWNER`, `OWNER_SCOPE` and `IMPORT_WRITER`, and the bare `"owner"` literals of that meaning.
     - `scopeOf(client)` names the owner's scope or a client's `client:<id>`. It replaces the five copies of that choice (in `files.ts`, `launch.ts` and `ledger.ts`) and `filerOf`'s own.
     - The frozen-bounds guard holds `OWNER` against the schema's `'owner'`.
     - Left: `"owner"` as another vocabulary's word. These are type tags (`authority.ts` `by`, the ledger member's `kind`, `runs.ts` `Initiator`, `control-gate.ts` `Gate`, `policy.ts`'s buckets, `collections.ts`'s usage keys, `wake-kinds.json`'s `callback`), the binding's `owner` key, and the frozen SQL.
   - **One `unitsNow(take)` in `gate.ts`** for `archive.ts`'s `now` and `backup.ts`'s `unitsNow`: what `take` holds of the pool now, or `busy`.
   - **`inTime` is `clock.ts`'s:** the owner's time, now and a call bounded in time. It no longer sits in `schedule.ts`, the lanes' module, which four other modules imported only for it. Its `Infinity` case went with `Files.run`'s `until = Infinity`, a default only the tests used. They pass a pass's end (`passEnd()`), as the schedule does.
   - **The request texts are one `printable(value, max, what)`** in `contract/src/text.ts`, beside `name`, which now calls it (lane C's file, seven lines). `connections.ts`'s and `ledger.ts`'s `text` go. `files.ts`'s `whole` reads its `count`, not a copy of it. `command_center_id` is `COMMAND_CENTER_MAX` (D1's owed line).
   - `memory/leg.mjs` reads `CPU_LIMIT_MS` from `guards/cpu.ts`, which imports with its extension so Node runs it.
4. **B7's remainder from O4:**
   - **The constructors' clocks are gone.** These are `Schedule`'s, `Connections`'s, the erasure's parts', the starter's and the wakes' deps', `runWakes`'s, and the relay's `now`, which is the same seam found here. Production always handed them `clock`. Each reads `clock()`. The tests that varied them move a fake `Date` (`clockAt`, `atClock` in `test/support.ts`, as d3 §K2 has every timed test do). `runs-support.ts`'s `later` is `clockAt`. The schedule test's probe reads and sets `p.now` through it.
   - **A lane's pass ends in seconds.** `Lane.run(now, until)` is one unit, the owner's clock's. The schedule hands `clock() + LANE_PASS_S`, and every lane compares `clock()`. The files lane converts once, for its delete floor and `inTime`.
   - **A bug, found by the unit change:** the `runs` lane (`launch.ts`) compared `clock()`, seconds, with `until` in milliseconds. So its three loops never reached the end of a pass: a pass took every waiting run, mail and due schedule, however long it ran. Now a pass takes no row past its end, as every other lane's does. Test: `agent-mail.test.ts` "starts no run past its pass's end …". Its mutation compares against `until * 1000` and fails it.
5. **The paths no test reached:**
   - `roomPost`'s parent read again on `archive_changed`: `archive-read.test.ts` "posts a reply whose parent moved to the archive after its gate …" (`large`). The reply's placed body is held at the files gate while the parent moves. Mutation: read the parent once, and it fails with `storage_busy`.
   - `archiveMoving`'s answer: `archive-read.test.ts` "answers a read the archive moved under each of its reads …" (`large`). Each of the `READ_TRIES` reads is held while the lane makes a segment, then its answer is lost. It pins the code, the message and `retryAfter`. Mutation: a wait of 2, and it fails.
   - `#runDoor`'s `not_granted`: `agent-mail.test.ts` "from a client holds its run's run doors to that client's grant …". The run holds `run_graph` itself, but Muse's grant does not hold it on another agent. Mutation: `refused`, and it fails.
   - **`uploadPlan`'s and `openUploads`' now as milliseconds is caught on main**, so no test is added. Each swap fails `import.test.ts`: `uploadPlan`'s fails 10 tests across `import.test.ts` and `files.test.ts`, and `openUploads`' fails "resumes an open upload of the same file …". This corrects `o4.md` item 7's "neither failed".
6. **O4c (`untangle/o4c`), owed: B10's literal guard over every folder.** It is its own PR for three reasons:
   - **Its exceptions are not ratchet entries.** d2 B10 allows exceptions as "`owed` entries with a reason", but `ci/kit/owed.ts`'s `ratchet` refuses any entry whose fix is not a `B<n>`. Some literals are permanent:
     - the sandbox's E2B template name, an image input (`s6.md` item 2);
     - a guard's fixtures that the guard must refuse;
     - `contract/src/queue-names.ts` and `contract/services.json`, generated from the configs.
     So the guard needs a decided form for a permanent exception, beside `owed`, before it is built.
   - **It touches every lane's `rules.ts`.** On main, 35 files in nine workspaces hold a `tinyassets-*` name outside the configs and `platform.json`. `deploy/guards/deploy.test.ts` alone holds 66. Each workspace owes its own entries.
   - **Size.** This PR is about 1,100 changed lines without it.
