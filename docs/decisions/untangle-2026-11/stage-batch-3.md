# stage-batch-3 as built (2026-11-28)

The founder's stage test, batch 3 of its edge cases (a claude.ai chat with the stage
connector, the stage serving main `70e362bcb`, 2026-11-24 to 11-28). Each finding was checked
against main at `5c6a53ef9` by reading the code and by probing it in workerd (both MCP
revisions, and code mode with the real codemode Worker), then fixed, or set out here where
the behaviour is intended. Research first (Fact 14).

## 1. A backslash in a file name was accepted

**Cause.** The stored-path grammar (`contract/src/paths.ts` `storedPath`) is Linux's: any
code point but `/` and NUL in a name. The founder has moved the owner's own computer, the
desktop app on Windows among them, ahead of the cutover as a place runs happen (ADR-040,
#5865), and the workspace syncs there by the sandbox's protocol, so a name must exist on every
place it reaches.

**Research: how the leading sync tools treat a name Windows cannot hold** (read 2026-11-28).

- **Dropbox** keeps the name on its server and refuses it on a Windows computer ("Couldn't
  download. Remove incompatible characters from the filename"); since its "flexible file
  names" setting, the Windows app may instead show `_` in each such character, locally only,
  the server's name unchanged. Its staff: a colon "will not sync to Windows, and never has".
  ([Naming files](https://help.dropbox.com/organize/file-names),
  [flexible file names](https://help.dropbox.com/sync/flexible-filenames).)
- **OneDrive and SharePoint** refuse `" * : < > ? / \ |`, leading and trailing spaces, a
  trailing dot, and `CON`, `PRN`, `AUX`, `NUL`, `COM0` to `COM9`, `LPT0` to `LPT9` (with
  `.lock`, `desktop.ini`, `_vti_`, `~$`); the Mac client offers to rename by `_`. Since January
  2025 the Mac client syncs `*:<>?\|` names without renaming, only where Windows never sees them.
  ([Restrictions and limitations](https://support.microsoft.com/office/64883a5d-228e-48f5-b3d2-eb39e07630fa),
  [OneDrive for Mac](https://techcommunity.microsoft.com/blog/onedriveblog/improved-filename-and-external-drive-support-for-onedrive-mac/4372026).)
- **Syncthing** neither renames nor encodes: a Windows peer reports "filename is invalid" (or
  "must not end in space or period") and leaves the file out of sync until it is renamed at
  its source. ([forum](https://forum.syncthing.net/t/name-is-invalid-must-not-end-in-space-or-period-on-windows/17333).)
- **git** stores any name in a tree and refuses to check one out on Windows
  (`core.protectNTFS`, on by default since CVE-2019-1353): "error: invalid path".
  ([Atlassian KB](https://support.atlassian.com/bitbucket-data-center/kb/error-invalid-path-during-git-clone-to-windows-client/).)
- **Nextcloud**'s "Windows compatible filenames" is the server-side rule: new names a Windows
  computer cannot hold are refused (its forbidden characters, basenames and trailing dots and
  spaces), existing ones are left to rename or delete, and case is not enforced.
  ([admin manual](https://docs.nextcloud.com/server/stable/admin_manual/configuration_files/windows_compatible_filenames.html).)

**Chosen: refuse at the one place a name is made.** Encoding (Dropbox's and OneDrive's `_`) is
lossy and per place: the name on the server and on each place differ, every place holds a
map back, and the stand-in collides with a real name (`a_b.md` beside `a:b.md`); a lookalike
mapping (rclone's full-width characters) is reversible but collides the same way. Refusing
before a name exists keeps one name on every place, fails loudly (Fact 8) and needs no map;
it is Nextcloud's rule, on the server, and what Syncthing and git leave each user to do by
hand. So:

- `contract/src/paths.ts` `unportable(name)`: a control character (`\p{Cc}`: C0, NUL and a
  newline among them, DEL, C1), any of `\ : * ? " < > |`, a trailing space or dot, or a device
  name Windows reserves (`CON`, `PRN`, `AUX`, `NUL`, `COM0`-`COM9`, `LPT0`-`LPT9` and the
  superscript `COM¹²³`/`LPT¹²³`, any case, any extension, the part before the first dot read
  without its trailing spaces). Each refusal names the name and the rule.
- **One rule for every writer.** The owner's `Files.#upsert` is the one place an entry is made
  or changed: a name it would make is refused `invalid` with that reason, so the file tools,
  `file_append`, a folder and the parents a write makes, a rename's target, an upload's
  commit, a restore and a sandbox's sync op are all held alike (the sync refuses that op
  alone, reported by the sync agent each sync as any refused file is, `sandbox/runner/sync.ts`).
  The cutover's importer keeps such a name in `refused.tar` (`newPath`, `import/survey.ts`),
  and its report says why (`import/build.ts`).
- **A name held before the rule** (the stage's `back\slash.md`) is read, written, renamed and
  removed as it is: refusing it would strand the owner's own file. Only a new entry meets the
  rule, so it can be renamed to a name that passes.
- `storedPath`'s one refusal is now three, each saying what it holds: absolute, no NUL,
  well-formed Unicode within 4,096 bytes.
- Tests: every writer and the held name (`owner/test/file-names.test.ts`), the MCP doors
  (`mcp.test.ts`). The app's payload test wrote file names of markup (`<img onerror=…>`,
  `"><svg onload=…>`), which the rule now refuses: it asserts that refusal, and proves the
  app escapes a name through names the rule lets be that are markup if built into HTML
  unescaped (an attribute broken out of by `'`, markup written as entities), its markup
  instrument and `__xss` count unchanged; the markup payloads stay in the file text, frames,
  results, posts and hash it renders.

**Left.** Two names that differ only in case (`Notes.md`, `notes.md`) are two files here, as on
Linux, and one on a case-insensitive disk (macOS's default, Windows): Nextcloud does not
enforce it either, and Windows holds case-sensitive folders. Where a device place lands both
is its sync's to decide, owed below to the ADR that designs that place, with Windows'
260-character `MAX_PATH` (a Windows app opts out of it with long-path support, which is that
app's to declare, never a name rule here).

## 2. Code mode ran an endless loop past the client's 60 s

**Cause.** A script that computes without calling a tool holds no clock: the VM has no timers
and `Date.now()` stands still while it computes (pi-codemode 1.4.0's README, "Limits on
Cloudflare"), so a busy loop waiting on `Date.now()` is an endless one. The only bound on it is
the codemode Worker's interrupt budget, `CODEMODE_INTERRUPT_BUDGET`, which was 1,000,000 polls:
about 100 s on Cloudflare by pi's measurement (100,000 polls, about 10 s), set as a third of
the Worker's `cpu_ms` (300 s). The client waits 60 s (the MCP SDK's default request timeout,
pi's, and claude.ai's as the founder saw it), so the script outlived its caller and its
`timeout` answer had no reader. `timeoutMs` would end the execution in the owner at once, but
the remote VM goes on computing to its budget either way (pi's README), and the wall time is
the caller's own wait by design (`code-caller-leaves.md`): a caller that waits (Claude Code,
1e8 ms; a run's engine) keeps a script of many tool calls running, so a wall clock is not the
fix.

**Measured.** 100,000 polls of a tight loop took 46 s in workerd on this host (the codemode
Worker's own test, budget raised); pi measured about 10 s on Cloudflare.

**Fix.** The budget is 50,000 polls, recorded once in the contract (`contract/src/codemode.ts`
`CODEMODE_INTERRUPT_BUDGET`, with `CODEMODE_MEMORY_BYTES`), where both the codemode Worker's
cap (`codemode/src/codemode.ts`) and the owner read it: about 5 s on Cloudflare and 23 s at
the slowest rate seen, both well within 60 s (the slowest at 38% of it), and far under
`cpu_ms`. The owner's sandbox asks for exactly the recorded budget and memory in each request
(`owner/src/codemode.ts`), and a script that reaches the budget is answered `timeout` with
`CODE_COMPUTE_LIMIT`: the limit by number and in seconds, that the clock stands still while it
computes, and to compute less or split the work across `code` calls. Fact 15: a recorded
technical ceiling (`limits-audit.md`'s row restated). H2's probe 1 measures the rate live.

## 3. Code mode ran out of memory and said only "out of memory"

**Cause.** The VM's cap is 32 MiB (`CODEMODE_MEMORY_BYTES`, a quarter of the isolate's 128 MB),
and the VM's own words were passed on as they were.

**Fix.** The VM's own error (QuickJS's `InternalError`, which it raises past its memory, its
message "out of memory") is answered with `CODE_VM_LIMIT` after its words: the cap, to hold
less at once, to read a file a window at a time, and that a file past 4 MiB is uploaded (the
owner app's `files/upload` doors, or made in a run's sandbox, whose sync uploads it), never
written through a script. It is known by the error's name, never its message, which the
refusal guard holds no code reads (`ci/kit/refusals.ts`). `UPLOAD_LARGER` (`owner/src/files.ts`) is that sentence once: `file_write`'s
own refusal past 4 MiB says it too, where it said only "larger ones are uploaded".

**No whole file is buffered needlessly.** A script reads a file through `file_read` and
`fetch`, each at most one answer (50,000 characters); nothing hands a script a whole file. A
write's whole text is the script's own string (`file_write` takes the file whole, at most
4 MiB), so its memory is the script's choice.

## 4. "Missing" five ways

**The internal error is gone already.** `file_read` of a missing path answered "internal
error" in batch 1 because a file fault was no refusal; `stage-findings-2.md` finding 2
(#5818) made every file fault a refusal of its code. Probed at `5c6a53ef9`, both MCP revisions
and code mode: `not_found: no such file`, with "This refusal is final". The new code-door test
holds it through a script (`code-door.test.ts`), beside the doors' own (`mcp.test.ts`).

**One rule, kept: a door answers absence in its own shape when that shape can say it, and
refuses `not_found` only when it cannot.**

- A read of what stands answers absence as its value: `file_stat` `{entry: null}`,
  `collection_get` `{row: null}`, `file_history` no versions. Null and an empty list are never
  a real value of these, the app and the agents read them to learn whether to create, and the
  server's instructions build claims on a row's absence.
- A compare-and-swap against what is gone is a conflict: `collection_delete` and `file_remove`
  at an etag answer `{conflict: null, message}`, as every etag-held change does (finding 6).
  The "null" the founder saw from `collection_delete` was this conflict, now said in words.
- A read of a file's content refuses `not_found` (`file_read`, `fetch`): text has no empty
  value that is not also a file's (`""` is an empty file), and ChatGPT's `fetch` contract
  expects an error for an id it cannot fetch.

## 5. A NUL byte in a file name, through code mode

**Probed.** In workerd a NUL in a path is refused at once, by every door and through code mode,
in both MCP revisions, and the connection serves the next call: a raw NUL in a script's source
and an escaped one alike, `file_write` and `file_stat`. "This call did not run" is no text of
ours (searched), so it is the client's own answer; the drop is not reproduced here, owed
below as a live retest.

**Fix.** The refusal says what it is: a NUL is refused by `storedPath` ("a path holds no NUL
character"), and every other control character in a name about to be made by `unportable`
(finding 1), so a tab or newline in a name, made until now, is refused too. Tests: the doors'
(`mcp.test.ts`: NUL, `\u0001`, a newline, a backslash, `CON.md` and a trailing dot, each
refused by `file_write` and `file_append` with its reason, nothing made, the next call served,
both revisions); code mode's (`code-door.test.ts`: a NUL, a control character and a
backslash through a script, then the next script).

## 6. An outdated etag answered `{"conflict": {…}}` alone

**Fix.** Every conflict answer carries one line saying what happened and what to do: a file's
`FILE_CONFLICT` (`owner/src/files.ts`, every file door's lost swap, `file_edit`'s own check
among them) and a row's `ROW_CONFLICT` (`owner/src/collections.ts`): it is not at the etag
sent, `conflict` is it as it now stands (null: nothing there), read it again and make the
change against its etag, or `""` to create. The door table declares no conflict field, so its
shape allows a sibling field; `owner-object`'s requirement says it.

## 7. The sign-in page hid every reason

**Cause.** `doors/app/signed-in.js` showed "The sign-in was not finished" for any `error`, and
the doors sent a reason for only two of the four refusals. The founder's non-admitted account
on the stage met the maintenance gate in its canary mode (`ordinary: canary`, the two stage
accounts its `canary`): `ownerRead` refused it `unavailable` with `MAINTENANCE`, answered
`temporarily_unavailable` with no description, which reads as "try later" when the account
will never get in.

**Fix.**

- **The gate says why, one rule** (`contract/src/control.ts` `gateClosed`, replacing
  `outsideOpen` and `ownerOpen`): outside access off is `OUTSIDE_OFF`; a maintenance gate
  admitting no owner is `MAINTENANCE`; one admitting only the canary owners tells any other
  owner `CANARY_ONLY` ("this account can't use TinyAssets here for now: only the accounts this
  deployment is being tested with can (on the stage, sign in with a stage account)"), naming no
  account. The doors' two gates, the hooks' (`ordinaryClosed`) and the object's own
  (`control-gate.ts`) read it, so an MCP call, `/api`, the app and a sign-in all say the same.
  It stays a refusal for the moment (`unavailable`, 503): the gate opens later, and a lane or an
  inbound sender must try again rather than settle. An outside call with outside access on and
  the gate closed to all now says maintenance, where it said "switched off".
- **Every sign-in refusal carries its reason** (`error_description`): a refusal for the moment
  its message (`asRedirect`); a sign-in too old `STALE_SIGN_IN`; an erased account
  `ERASED_SIGN_IN` (both `contract/src/signin.ts`); the upstream's own refusal (WorkOS's
  `error` and `error_description`, control characters made spaces, at most 300 characters,
  never a token) as "the sign-in was refused: …", logged as "the upstream sign-in failed: …";
  no code at all likewise.
- **The page shows it** with what to do: the reason as a sentence, then "Go back to the app to
  try again."; a code without one its plain default. It shows a reason only for a sign-in this
  tab began (its state matches), so no link made elsewhere puts its words on our page; any
  other return is "This sign-in was not started in this tab."
- Tests: the doors' (`refused.test.ts`, `oauth.test.ts`, `control.test.ts`: each reason's
  `error` and `error_description`, the canary refusal of an app sign-in and an MCP call); the
  page's (`app.e2e.mjs`: each reason's text, and a crafted link's words never shown).

## 8. "Command center": an old name for a space

It is the old stack's name for what the platform calls a space: the registered agent's
`command_center_id` is where a narrowed grant acts, the universe its scopes name, and the owner
types it in the app as the key's "Space" (`stage-findings-2.md` 7; `agent_register`'s argument
says so since #5821). It survives as two owner tables' columns (`outside_agents`,
`ledger_messages`, with their change-log triggers and the export header's schema), the
registration's and MCP Events' wire field (`agent_register`, `doors/src/events.ts`), the
importer's map from the old tables, the wake kinds and `app.js`. Renaming it is an owner
migration, an export schema step and a wire change with `Recorders-Differ`, in a dozen files
and their tests: too wide for this lane, and already a line of its own (`tasks.md`, "The names
the port carried", `untangle/old-names`), owed below.

## 9. "He" for the founder

Every he, his, him and himself for the founder in `docs/`, `openspec/`, `platform/` and the
root Markdown files is now "the founder" or they/them (`host-actions.md`, `REVIEW.md`,
`tasks.md`, `limits-audit.md`, ADR-035 and ADR-037's Status lines, the latter's quote marked
`[the founder]`, a concern, and six decision logs). The rest of the matches name test
personas (Bob). AGENTS.md's Direction holds none.

## Owed

- [ ] live: after the next deploy of the owner and the codemode Worker, the founder's stage
  retest of batch 3: an endless loop answered `timeout` with its limit before the client's
  60 s, a NUL path through code mode refused with its reason and the connection kept, a
  non-stage account's sign-in page saying it can't sign in there.
- [ ] live: H2's probe 1 (`platform/deploy/h2-probes.ts`) measures 50,000 polls of a tight loop
  on Cloudflare well within 60 s.
- [ ] #5865: a device place on a case-insensitive disk meets two owner names that differ only
  in case; ADR-040's device sync says which it keeps and how the other is reported.
- [ ] `untangle/old-names`: `command_center_id` renamed to the space in the owner's tables (a
  migration), the wire (`agent_register`, MCP Events' subscription) with `Recorders-Differ`,
  the importer's map, the wake kinds and the app.
