# Design: command-center-cutover

The safety contract is in `rename-universe-to-command-center/design.md`: D6
(code), D7.1-D7.7 (storage), D10 (one cutover), D11 (`cc-` ids). This file
covers execution: order, tooling, the freeze window, and the evidence each
step must produce.

## E1. Read-only first: the inventory is a deliverable

**R1 folded (2026-10-02, #4273).** The scanner never reads its source twice over:
it copies the root into a private artifact outside it -- regular files only, no
symlink or reparse traversal, bounded, with the descriptor re-checked after
opening -- and runs SQLite's online backup API on *that copy* to get the
standalone database it scans. No source database or LanceDB store is opened by a
library, and `immutable=1` is gone. A source file that changes while it is being
copied fails the run -- a measured fact, not an assertion. It is NOT a refusal of
a live root: a writer that holds still for the copy is indistinguishable from an
idle one, which is why the report calls itself consistent per database and never
a proven cross-store snapshot.

`scripts/command_center_inventory.py` prints a machine-readable report:

- every SQLite table, column (including generated ones, via `table_xinfo`),
  `CHECK` / index / trigger / view clause, and TEXT or BLOB value naming
  `universe` or holding a `u-<ulid>` (D7.1, D11);
- LanceDB schemas and id rows, JSON keys and values, and marker files;
- the exempt verbatim stores (uploads, run outputs, the agent's workspace, brain
  files), listed by name so the scan's skips are explicit;
- what it CANNOT see, as `deferred` rather than as a zero: derived identities --
  length-prefixed lease keys, hashed connection and grant ids, content digests
  over records that contain an id. Raw byte matching cannot discover a digest of
  an id, so that discovery belongs to tasks 3/4 (E4b).

The report is fail-closed. `complete`, `coverage`, `exemptions`, `unscanned` and
`deferred` are separate keys; an unknown home entry, an unreadable store, an
exhausted limit or `--no-values` makes it incomplete and exits nonzero with no
flag to opt in; and `migration_ready` stays false while `deferred` is non-empty,
so this round cannot claim readiness by construction. Checkpoint serde decoding
and the trusted-acquisition fence in
[the inventory repair contract](inventory-repair-contract.md) remain open there.

Run it first on a production copy. Its counts are what the migration must
bring to zero, and what the dry run compares.

## E2. The migration is code with its own tests before it ever runs

`tinyassets/command_center_migration.py` runs phases 1-5 under the exclusive
`.layout.lock` that C4a introduced:

- it writes `{"layout": 1, "state": "migrating"}` before its first change;
- it makes one transaction per database and keeps a per-database progress
  table;
- every step is idempotent and resumable.

It is tested against fixture data roots built by every real schema creator.
Those tests cover crash and resume at every phase boundary, digest and
reference integrity, decoded round trips, independent deletion and export
counts, and a test that fails on any surviving operational old name or id.

## E3. Order of work

1. The inventory script, then a production-copy report attached to the PR.
2. The codemod: deterministic, plus its report of non-mechanical sites.
3. The migration module and its tests.
4. A dry run on a consistent production copy (SQLite online backup, never
   `backup-restore.sh`). The row-count and reader report is attached.
5. The rollback runbook in `docs/ops/`, rehearsed on the dry-run copy.
6. The freeze window, in the measured quiet window (18:00-20:00 UTC,
   re-measured the day before):
   - the lead pauses the other lanes;
   - stop the backup timer, take the quiesced backup and record its sha256;
   - deploy;
   - the migration runs and verifies before the port binds;
   - external phase;
   - reopen;
   - canary (`--assert-handles`) and `deployed_sha.py`;
   - rebase the lanes with the codemod.

## E4. What blocks the window

Any of these stops the cutover:

- the **measured migration duration** from the production-copy dry run, times
  3, does not fit the cutover deploy's deadlines. The migration runs before
  the port binds, and `deploy_fail_safe.sh` fails health after 180 s by
  default (`:190`, `:272`). The deploy job stops at 15 min
  (`deploy-prod.yml:68`). The cutover deploy sets both explicitly from the
  measurement, and the external phase is included in the count;
- **old-layout fixtures are missing.** The migration's tests need data roots
  built by the **pre-cutover** creators, with real 26-character ids
  (`ids.py:33`). Those fixtures are generated and committed, with a
  creator-coverage manifest naming every schema creator, **before** the
  codemod changes the creators. Today's deletion tests hand-build simplified
  schemas with 16-character ids (`test_account_deletion.py:97`, `:39`), so
  they cannot stand in;

- an inventory category the migration does not handle;
- a dry-run count or reader mismatch;
- an unrehearsed rollback;
- C4a not in production for at least a day;
- a WorkOS record holding an id that the live check finds.

## E4b. Credential custody digests move with the id (refute #5)

The vault is not id-independent. Both custody-reference formulas hash the
universe id (`credential_vault.py:1234`, `:1256`), and the reader rejects a
mismatched reference (`:1645`). Phase 2 (ids) therefore recomputes every
custody reference and every downstream copy of it. The dry run proves each
credential still **resolves and is usable** through the vault reader. Loading
the JSON file is not enough. D11's statement that nothing in the vault is
derived from the id is corrected here.

## E5. Ids are never shown to people (founder, 2026-10-02)

*"keep the cc prefix, never show ids to users."* `cc-<ulid>` is the single id
form after the cutover. It is a machine value only.

**Covered surfaces:** the app (`app.html`, `app_ui.js`), the website, push and
in-app notification text, served agent guidance about how to refer to the
command center, the **desktop** launcher, tray and tray notifications, and
**human-readable error text**. None of them renders an id. The refute found
that the desktop uses the home directory's basename as the name
(`desktop/launcher.py:479`), shows it in the tray (`tray.py:332`) and prefixes
desktop notifications with it (`host_tray.py:208`), and that error text
interpolates the id (`api/universe.py:3916`). They show the command center's
name, falling back to **"Your command center"**.

**Guard test:** `tests/test_ids_never_shown.py`. It drives the app's render
paths with a home whose id is `cc-<ulid>` and fails if the id appears in
visible text. It also checks two more things:
- notification titles and bodies, through `owner_notifications`;
- served descriptions and instructions, which must never instruct the agent to
  speak or show an id.

The guard also drives the desktop name path and scans error-message
templates. It lands with or after notify-prompt's app-header fix. The desktop
and error-text fixes ship in the cutover image.

## E6. The target on-disk layout: storage moves once

*Agreed with openshell-spike on 2026-10-02 and written as the shared text in
`target-architecture` design D8a (#4263). The founder's rule: "do things correct the
first time". This cutover moves storage straight into the target layout, so
the later sealed-box slice is an image build, not a second data migration.*

**One rule decides every item.** Anything the daemon *trusts* (authority,
identity, owner settings, records) is **platform** state. Anything the person
or their agent may write is **user content**, which the daemon treats as
untrusted.

Under `data_dir()`:

| Path | Holds | Mounted into the jail/box |
|---|---|---|
| `cc-<ulid>/` | User content: brain files (identity, founder, origin, body, orgchart, projects, goals, index, log, voice, `AGENTS.md`), **`soul.md`, `soul_versions/` and `config.yaml`**, harness dirs (skills, prompts, extensions, workflows, bin, notes, wiki) and `notes.json`, upload bytes (verbatim, Hard Rule 9), run output files, permanent workspaces, anything the agent creates | Yes. It is the future box volume (`/cc`). The daemon reads every file here as untrusted |
| `.platform/cc-<ulid>/` | Per-command-center platform state: the credential vault and file-OAuth CLI credentials (`.credentials/`); the per-home DBs (runs, consent, usage, attention, conversation custody and journals, checkpoints, `outbound.db`, `knowledge.db`, `story.db`, `lancedb`); rules, auto-review, activity, pending effects, proposals, import quarantine, browser profile; the `.command_center_id` marker, lease/seat/slot/lock/stamp files, worker-supervisor state and the egress proxy socket (today's `.universe-sidecars/<id>/` folds in here); upload custody records; and the **trusted policies** `soul.edit.md` (which learning edits are allowed, `soul_edit.py:133`) and `dispatcher_config.yaml` (external-request and paid-bid acceptance, `dispatcher.py:449`) | Never |
| `.platform/accounts/<account_id>/` | Per-account platform state: storage allocation ledger, compute-hour meter, seats. Created empty by the cutover | Never |
| root DBs (`.tinyassets.db`, `.runs.db`, `.langgraph_runs.db`, ...) | Unchanged location; tables and columns renamed (D7) | Never |

**`soul.md` and `config.yaml` stay agent-editable** (lead, 2026-10-02). The
agent is self-improving: it edits its own persona, instructions, config and
skills, with versioning, and writes its brain continuously. That is
founder-approved harness behaviour. Moving these files to platform state with a
read-only copy, as the first draft of this table did, would take that away. The
refute found no trust reason to do so: the daemon already reads these files as
untrusted (`daemon-reads-universe-files-as-untrusted`). What the daemon *does*
trust about them is the edit **policy**, `soul.edit.md`, and that moves to
platform state, so the agent can change its soul but not the rules for changing
it.

**`config.yaml` is split: authority moves to platform state** (#4263 D8a
refute round 3). Today `config.yaml` also holds server-owned authority:
`engine_assignment_state`, `engine_assignment_generation` and
`provider_authority_bindings` (`config.py:60-66`), plus `allowed_providers`,
the routing ceiling the router enforces (`providers/router.py:158-177`). A
file the agent can write must not hold what the daemon trusts. So phase 1
moves those fields into `.platform/cc-<ulid>/assignment.json`, a
platform-owned assignment record. Every consumer then reads authority only
from that record. `config.yaml` keeps only the agent's preferences, read as
untrusted. A test fails if an authority field is read from `config.yaml`, or
if writing one there changes routing. **Ordering:** the split ships standalone
before the cutover (tasks.md prerequisite). It is a hard prerequisite of any
change that makes `config.yaml` agent-writable, so the self-improving harness
cannot open the file to the agent while it still carries authority.

**Mixed consumers get both roots explicitly** (refute #1). These readers span
both sides and are adapted, then tested end to end:

- **soul editing** reads its policy from `platform_dir(id)` and writes brain
  files under `command_center_dir(id)` (`soul_edit.py:149`, `:375`);
- **self-model and persona** read `soul.md` from `command_center_dir(id)`
  (`universe_self_model.py:46`);
- **config** reads preferences from `config.yaml` in `command_center_dir(id)` (`config.py:158`), and authority (assignment state, provider bindings, `allowed_providers`) from `platform_dir(id)/assignment.json`;
- **the dispatcher** reads `dispatcher_config.yaml` from `platform_dir(id)`;
- **vault ownership lookup** finds the root database through the resolver, not
  `universe.parent`, which would become `.platform`
  (`credential_vault.py:273`).

No caller passes one directory and expects both kinds of file under it. A test
fails if a trusted policy is read from `command_center_dir`, or a user file
from `platform_dir`.

**Answers that set the split:**

- **Permanent workspaces** are user content.
- **Conversation history** is platform state; the agent sees it only through
  read-only projections.
- **Run output files** are user content, while run records, receipts and
  checkpoints are platform state.

**One resolver** builds every path: `command_center_dir(id)`,
`platform_dir(id)` and `account_platform_dir(account_id)`. A test fails on
any hand-built path (D11).

**What this adds to the migration.** Phase 1 (names) also *moves* each item
to its target place, with progress recorded:

- **Database families move as one journaled unit** (refute #2). The storage
  helper documents committed-data loss when a primary moves without its WAL
  (`storage/__init__.py:323`), and knowledge storage runs in WAL
  (`knowledge_graph.py:143`). So each SQLite database is checkpointed
  (`wal_checkpoint(TRUNCATE)`) and closed. Its `-wal`/`-shm` family then moves
  under a journal entry, and a crash between any two renames resumes to a
  consistent family. Tests crash between every pair.
- **The migration releases its own handles,** including the cached LanceDB
  connection (`retrieval/vector_store.py:47`), before moving a store.
- **Preflight:** every source and destination is on the same device
  (`st_dev`), and no submount survives under a home. Today's jail binds
  individual entries (`universe_tools.py:303`), and the server is down, so
  none should exist, but the run checks rather than assumes.
- **No symlinks** are used to relocate anything. The file API rejects links
  (`universe_files.py:105`), so every reader is moved to the resolver instead. The inventory (E1) classifies every entry of
every home into one of the four rows above. An entry it cannot classify stops
the run, so nothing is guessed into the box.

Once this lands, target-architecture's S2 ("platform state out of the home
dir") is delivered by this cutover.
