# Design: rename-universe-to-command-center

## Context

The founder renamed the product concept on 2026-10-01: a person's universe is
now their **command center**. The rename is approved; this document decides
how to do it safely. The inventory and counts are in `proposal.md`.

What makes this more than a find-and-replace:

1. **The MCP input schema is strict.** FastMCP 3.2.0 rejects an unknown keyword
   argument (`unexpected_keyword_argument`; verified locally 2026-10-01 with a
   one-tool server called with `universe_id` against a `command_center_id`
   signature). A plain rename would break every chatbot conversation that cached
   the old tool list.
2. **Users' own code depends on the old names.** Custom UI bundles stored in
   accounts read `universe_id` and `universe_name` from the bridge identity
   (`onboarding/app_ui.js:346`).
3. **Storage keys are part of the deletion and ownership logic.** Account
   deletion builds its table set from the schema's universe column. Run actors
   are stored as `universe:<id>` and compared by prefix
   (`automation_events.py:75`, `:320`).
4. **The resident description budget has almost no headroom.** The served
   engine block is 29,839 of 30,000 characters
   (`tests/test_converse_turn_cost.py:79`), and it contains 34 occurrences of
   "universe".

## Goals / Non-Goals

**Goals**

- A person never reads "universe" in the app, the website, the store copy, a
  served description, the connector instructions, a prompt name, a parameter
  name, an error code, or the agent's own words.
- Every stored reference and first-party client moves to the new names in a
  verified cutover. A client still holding an old name or id gets a loud,
  specific error naming the new one or saying "unknown id". It never gets a
  silent alias (founder, 2026-10-01: clean cutover).
- One authority for the old-to-new name mapping.

**Non-Goals**

- Rewriting dated records: `docs/audits`, `docs/reviews`, dated design notes,
  `.agents/activity.log`, `archive/`, and archived changes. A rename sweep
  rewrites evidence; they describe what was true when written.
- The fiction domain's "universe" when it means a story world.
- Retiring the hidden legacy fat tool `universe`. It is not advertised and its
  retirement is a separate decision.
- The canary handle set. No handle name changes.

## Decisions

### D1. Vocabulary

| Use | Form |
|---|---|
| Prose | "command center", "your command center", plural "command centers" |
| Titles / buttons | "Command Center" only in title case contexts; buttons are sentence case: "Switch command center" |
| Identifiers / keys | `command_center`, `command_center_id`, `command_centers` |
| The place or account | "command center": "your command center's connections", "Switch command center", "No public command centers" |
| The actor | "your agent": "Your agent is thinking...", "your agent replied", "Suggested by your agent", "Message your agent" |
| Agent self-reference | First person; the agent works *in* "my command center" (the place) |
| New-user greeting | "Welcome, commander." (empty-thread heading, `app.html` `#thread-empty`) |
| The person | "commander" only in the greeting; elsewhere "you" as today |

**Actor or place (lead decision, 2026-10-01).** "Universe" did two jobs: it
named the place a person owns, and it named the mind that acts in it. The
rename splits them. When the old word is the **subject of an action** (it
thinks, replies, answers, sees, asks, builds, uses, is waiting on you), the
new copy says **"your agent"**. When it names **where or whose** (a connection,
a folder, an account, a setting, a list), the new copy says **"command
center"**. New copy follows the same rule; a test cannot enforce the
judgement, so review does.

**A command center can hold many agents** (founder, 2026-10-01; harness design
§4.18, #4227). So actor copy names the agent when a name is known and falls
back to "your agent" only when none is. Examples: `<name> asks` (already built
that way in `owner_notifications`), `<name> is thinking...`. Copy must never
hard-code a single agent ("your one agent", "the agent" as a fixed noun).

"Command center" is 6 characters longer than "universe". That matters only in
the resident description budget (D5).

### D2. Handles stay; one prompt is renamed

`read_graph`, `write_graph`, `run_graph`, `read_page`, `write_page`,
`converse` and `get_status` keep their names. `scripts/mcp_public_canary.py
--assert-handles` therefore does not change, and Hard Rule 11's canonical set is
untouched. The canary still runs after C1 deploys, because C1 changes the public
surface.

`meet_universe` becomes `meet_command_center`, titled "Meet Your Command
Center", and the old prompt is removed rather than aliased. People pick prompts
from a list, so nothing calls the old name, and the spec's catalog is exact. The
delta spec updates the catalog.

### D3. Retired input names are refused, naming the replacement (clean cutover)

*Founder, 2026-10-01, on this rename: "no old ids do not keep working, we are
still early in production so we are not maintaining old systems we dont have
old users we just have current testers that need to cleanly move to the new
system". This replaces the alias window the first draft proposed.*

`tinyassets/command_center_names.py` is the one authority. Every name is
derived by one rule (`universe` -> `command_center`), not a hand list. It does
three things:

- **Refuses retired names.** A retired argument name (`universe_id`) or enum
  value (`target=universe`, `universe_files`, `universe_file`, `scope=universe`)
  is refused as `{"error": "renamed", "retired": ..., "current": ...}`, for
  example "renamed: universe_id is now command_center_id". It is never
  silently accepted. The refusal comes from a FastMCP middleware
  (`CommandCenterNames`) registered innermost on both servers, so it runs
  before the tool's own validation and names the replacement instead of
  FastMCP's generic "unexpected keyword". On the connector it governs the
  seven advertised handles. The legacy fat tools are not registered as MCP
  tools at all (`universe_server.py:3080`).
- **Maps current values to the handlers' names.** `internal_value` maps
  `command_center` to `universe` (and so on) until the code rename (C3)
  changes the handlers themselves. A retired value reaching a router directly
  becomes `retired:<value>`, an unknown target, so a caller that skips the
  edge also fails.
- **Renames parameters with a local binding.** `read_page`, `write_page` and
  `get_status` take `command_center_id`; inside, it is bound to the internal
  name until C3. Direct Python callers are migrated in the same PR.

The owner door (the app's private HTTP reads, `owner_door/routes.py`) is an
internal first-party API, not part of the public surface. Its accepted
arguments are derived from the domain functions' signatures (`:42`, `:132`).
Targets pass through the same routers, so the app sends the current target
names and a retired one is refused. Its argument and response key names
(`universe_id`) are internal names and are renamed with the code in the
cutover (D10), in the same image as the app that calls them.

A workspace packet's `storage: "universe"` and a branch's declared
`delivery_sender_universe_id` input are **not** renamed here. Both are stored
inside people's branch definitions, so they move with the storage migration
(C4), which rewrites the stored definitions and the code in one step.

### D4. Responses carry current names only; every first-party reader moves in the same PR

Each JSON tool result is respelled before the result ceiling measures it. On
the engine, `CommandCenterNames` does this innermost. On the connector it
happens in the structured-result adapter, which is where that server bounds a
reply. The respelling covers:

- keys (`universe_id` becomes `command_center_id`, `universes` becomes
  `command_centers`);
- error codes (`no_home_universe` becomes `no_home_command_center`);
- stored actor ids in identity fields, presented as `command_center:<id>`
  until C4 rewrites the stored value.

Responses do not carry the old keys alongside the new ones. A person's own
content is never respelled:

- run output, run files, command-center files and conversation pages;
- `read_page`, and the engine's `read` / `write` / `edit` / `bash`;
- inside any other result, every **user-authored subtree** (`USER_CONTENT_KEYS`:
  graph nodes, edges, state schema, mappings, payloads, UI bundles, content).
  A state field a person named `universe` is data. The C1 refute reproduced
  `output_mapping={"universe": ...}` failing validation on its round trip
  (Hard Rule 9).

First-party readers switch in the same PR:

- the website read contract and its baked snapshot (`command_centers`);
- `scripts/mcp_tool_canary.py`;
- the custom UI bridge, whose `whoami()` returns `command_center_id` /
  `command_center_name`. Production on 2026-10-01 holds **zero** stored UI
  bundles that call `whoami` or read `universe_name` (read-only count over
  `universe_app_ui`), so the cutover breaks no stored bundle.

`get_status` bumps `schema_version` to 3, per its own contract, which now says
a rename bumps the version with no alias window.

**The default agent definition.** A published definition is immutable and
fingerprinted under its idempotency key, so C1 publishes a **new** one
(`platform:command-center-default` / `command-center-default-v1`, named "Your
agent") rather than editing the old one, which would raise `AgentConflictError`
at every onboarding. New homes bind to it. A home bound to the retired
definition is still recognised as the founder's platform binding and is used as
is, with its `provider_ref` intact. Re-pointing it during a serving gesture would
replace its configuration, dropping the `provider_ref` before the new provider
is validated (C1 refute). The cutover migration re-points every one in place
(D10). The retired definition is looked up, never re-published.

### D5. Paying for the longer word inside the description budget

A plain swap in the resident engine block costs +204 characters against 161 of
headroom. C0/C1 therefore rewrite the affected sentences rather than swap
words. Where a sentence already says "your" or "the owner's", it can drop the
noun ("your folder" for "your universe folder"). The ratchet value is **not**
raised. The `write_graph` description must stay under 12,000
(`tests/test_served_tool_guidance.py:369`). The connector tool docstrings carry
no equivalent ceiling today, but they get the same rewrite-not-swap treatment.
The channel-agnostic and vendor-neutral ratchets are unaffected: the change
introduces no channel or vendor words. `build_plugin.py` regenerates the mirror
in each slice.

### D6. Code identifiers: renamed (C3), in one freeze window

*Decided by the founder, 2026-10-01: "rename them too". This replaces the
earlier recommendation not to.*

**Scope.** Every identifier, module, test name and env var that spells the old
word:

- 7,059 identifier occurrences and 323 distinct names;
- 12 `universe_*` modules (`universe_server.py` becomes
  `command_center_server.py`, and so on);
- about 17,000 test lines that import or monkeypatch them by module path;
- the `TINYASSETS_*UNIVERSE*` env vars.

The plugin id `tinyassets-universe-server` is renamed too, with no forwarding
entry. Installed copies update by id, so a tester with the old plugin
reinstalls it (clean cutover, D10).

**Mechanics.**

1. A codemod (`scripts/rename_command_center.py`, written in C3) does the
   mechanical part. It renames identifiers with libcst, renames modules with
   `git mv`, and rewrites import strings and `monkeypatch.setattr("tinyassets.universe_...")`
   targets. It is deterministic, so running it twice is a no-op.
2. It writes a **non-mechanical report**: every site it refuses to touch, for a
   person to review. That covers strings that are machine values (SQL, JSON
   keys, stored identity, which belong to C4), `getattr` / `importlib` by
   string, and fiction-domain "universe" meaning a story world.
3. Env vars are renamed outright. A process that finds a retired
   `TINYASSETS_*UNIVERSE*` variable set refuses to start and names the new
   variable, so a stale setting never silently stops applying. The operator's
   compose files change in the same window
   (`compose-flags-inert-without-droplet-sync`).

**Freeze window.** The lead pauses the other lanes, the codemod runs on a fresh
`origin/main`, and the PR lands. Then the lead rebases the paused lanes, and the
same codemod run on each lane's branch does most of that rebase. The PR is
gated on:

- the full suite in the Linux oracle and in CI's merge group;
- the mirror rebuild (`build_plugin.py`);
- the channel-agnostic and vendor-neutral ratchets;
- the description budgets (D5);
- `cross-provider-drift`.

There are no compatibility shims for old module paths. Anything importing an
old path fails loudly, which is the point of doing it in one window.

**No serialization adapters: code and storage move in one image** (D10).
The refute (#4) found that renaming a persisted name changes what is written:
`BranchTask.universe_id` round-trips through `asdict` and a field-filtered
constructor (`branch_tasks.py:86`, `:126`). With the codemod and the migration
in one image, persisted keys, graph-state keys (`_universe_path` in SqliteSaver
checkpoints) and digest inputs move in the same run as the code that reads
them. The codemod still **reports** every `asdict` / from-dict / TypedDict /
JSON writer / digest site, and each is named in the migration's inventory, so
none is missed. `conversation_run_admissions.py:340` and
`provider_assignment_manifest.py:153` are the known digests: the migration
recomputes them (D11).

**Launch paths are verified by running them**, not by an import test:

- `pyproject.toml` entry points (`:82`);
- `deploy/deploy_fail_safe.sh`'s pre-deploy gate, which imports
  `tinyassets.universe_server` (`:1220`);
- Dockerfile CMD, systemd units, the plugin's `plugin.json` / `.mcp.json`,
  and the mcpb manifest;
- multiprocessing spawn targets.

C3's gate builds the image, runs the deploy gate script, and starts each entry
point.

**Ordering.** C3 ships inside the cutover (D10), in the same image as the
storage and id migration.

### D7. Storage: migrated (C4), guarded, from a schema-derived inventory

*Decided by the founder, 2026-10-01: "rename them too". This replaces the
earlier recommendation not to. C4 gets its own change directory
(`migrate-storage-to-command-center`), because a migration is spec-first. This
section is the safety contract that change must meet.*

**What moves.**

- Tables: `universes` becomes `command_centers`, `universe_acl` becomes
  `command_center_acl`, and so on.
- Columns: `universe_id` becomes `command_center_id`; `queue_universe_id`,
  `sender_universe_id` and the rest follow.
- Marker files: `.universe_id` becomes `.command_center_id`,
  `.universe-tool-slots` becomes `.command-center-tool-slots`, and
  `.universe_seats.db` becomes `.command_center_seats.db`.
- The stored actor prefix: `universe:<id>` becomes `command_center:<id>`.
- The account-deletion key, `UNIVERSE_KEY`, follows the column.

**The `u-` id prefix and the `u-<id>` folders move too** (founder, 2026-10-01:
"change it also"). They become `cc-<ulid>` in the same run (D11). The `/u`
mount point inside the tool jail is a path name, not an id, and stays.

*Note: "C4b" below means the cutover's migration run (D10). It carries the
storage, code and id changes together.*

**1. The inventory is derived, never hand-listed**
(`deletion-set-derived-from-schema`). `scripts/command_center_storage_inventory.py`
walks every data root a runtime can have. It reports, read-only:

- **SQLite**, in every file including per-home databases and satellites:
  - tables and columns whose name contains `universe`;
  - TEXT values `LIKE 'universe:%'` or equal to `'universe'`;
  - `CHECK` clauses and index, trigger and view SQL in `sqlite_master` that
    name either. For example, `workspace_pool.py:239` constrains
    `storage_class IN ('scratch','universe')` and `:264` constrains lock scope
    to `'universe','host'`.
- **LanceDB** table schemas (`retrieval/vector_store.py:123`: `universe_id`,
  `tag_universes`).
- **SqliteSaver checkpoints**: payloads decoded through the saver's serde,
  for state keys such as `_universe_path` (`fantasy_daemon/__main__.py:2208`).
- **JSON / JSONL files** under the data roots whose keys contain `universe`.
  For example, branch tasks are written at `branch_tasks.py:270`.
- **Marker files** and directories named `.universe*`.

That output is the migration's input. The migration rewrites what the
inventory finds, by rule:

- a table or column rename;
- a table rebuild, where a `CHECK` literal or a constrained value changes
  (SQLite cannot alter a CHECK);
- a value rewrite;
- a LanceDB schema rewrite;
- a serde-aware checkpoint rewrite;
- a JSON key rewrite.

A table nobody remembered is still migrated. A name or format the rule cannot
map stops the run before anything moves.

**Every runtime migrates its own data roots.** The same start-up migration code
runs in the production container, the desktop app and the local plugin
runtime, because those keep data outside the production volume:

- desktop homes default to `Documents/TinyAssets/default-universe`
  (`desktop/launcher.py:68`);
- the plugin opens a user-chosen root (`plugin.json:38`).

A production container migration cannot reach those installs, so each install
migrates itself the first time it runs a C4b build. The desktop's
user-visible default folder name changes only for **new** installs. An
existing folder is the person's own files: the launcher still finds it and
does not move it.

**2. A layout guard ships first, alone (C4a).** A new `data_dir()/.layout.json`
records `{"layout": 1, "state": "stable"}`, written if absent. Every image from
C4a on refuses to start unless it knows the layout **and** the state is
`stable`. It checks before opening any database, fails loudly and serves
nothing.

- **The crash window is closed** (refute #3). C4b writes
  `{"layout": 1, "state": "migrating"}` durably (fsync of the file and its
  directory) **before its first mutation**. A crash anywhere after that leaves
  a marker every C4a+ image refuses. Only a completed, verified run writes
  `{"layout": 2, "state": "stable"}`.
- **An image built for layout 1 cannot run on renamed data** (layout 2, or
  `migrating`). That is the deploy-rollback guard.
- **The automatic fail-safe is made migration-aware in C4a.**
  `deploy/deploy_fail_safe.sh` rolls back to the previous image on an
  unhealthy deploy (`:1356`, `:1383-1388`) without restoring data. Its stated
  contract is "startup migrations are backward-compatible" (`:91`), which C4b
  is not. C4a changes it: when the marker reads `migrating` or a layout newer
  than the previous image's, it does **not** start the previous image. It
  stops, reports `deploy_result=rollback_needs_restore`, and leaves the
  service down for the restore below. Serving nothing is the fail-safe; an old
  image on new data is not.
- **Images older than C4a cannot read the marker.** On renamed data they would
  see no `universes` table and could create a blank home. So C4b's
  pre-flight asserts with `deployed_sha.py` that the image the fail-safe would
  fall back to contains C4a. Release reconciliation deploys only main HEAD
  (`release-reconcile.yml:340`), which after C4a always contains the guard.
- C4a deploys and runs at least one full day before C4b, so the guard is the
  production baseline when the migration runs.

**3. One locked, idempotent, resumable migration (C4b).** It runs at container
start, before the server binds its port.

**Exclusion is a protocol, not an assumption** (refute #2). No single lock is
taken by every process today. Host jobs open databases directly: the backup
timer runs `deploy/backup.sh`, which opens databases at `:135`, and operator
scripts such as `scripts/universe_ownership_inventory.py:127` query directly.
So C4a puts each kind of process under exclusion:

- **In-container processes, including per-turn engine children,** take a
  shared lock on `data_dir()/.layout.lock`. The migration takes it
  exclusively.
- **Host jobs:** C4b's deploy step stops and disables the backup timer, and
  `backup.sh` takes the same lock file through the bind mount (`flock`).
- **Operator scripts** go through one helper that refuses unless the marker
  is `stable`.

A process that cannot get the lock waits or fails. It never reads half a
migration.

- **Per database:** one transaction holding all of that database's work:
  `ALTER TABLE ... RENAME TO` / `RENAME COLUMN`, rebuilds for changed `CHECK`
  literals, and value rewrites (`universe:` actor prefix, the stored
  `'universe'` enum values). A database is either all old or all new.
- **Progress record:** a dedicated `_command_center_layout` table per
  database. `PRAGMA user_version` is **not** used, because subsystems already
  own it for their own migrations (`storage/owner_devices.py:166`).
- **LanceDB, checkpoints and JSON files:** rewritten to a new file or table,
  then swapped atomically. The old copy is kept until verification passes.
- **Per file:** an atomic `os.replace`.
- **Progress:** recorded per database and file, so a crash resumes where it
  stopped. Every step checks whether it is already done (new name present, old
  absent), so a re-run is a no-op.
- **Finish:** the marker flips to layout 2 only after the post-migration
  verification passes.

**No reader needs two shapes.** Within one database the transaction leaves no
mixed state. Across databases, files and values, nothing reads during the run,
because the server binds its port only after verification. The code in that
image knows only the new shape (D10), so the first draft's fallbacks (old actor
prefix, old file names) are dropped. A process or script that meets the old
shape fails loudly.

**4. Deletion and export are proven against the migrated schema.** A test
builds a data dir with every real schema creator, migrates it, and then:

- **fails if any table or column still contains `universe`**, which guards
  against an unmigrated column;
- asserts `delete_account` removes every row of the deleted account and none
  of another's;
- asserts the operator reset (`scoped_reset`) does the same.

Export has no code path today: the `/account` page routes data-export requests
to the legal address, and they are answered by hand. C4b's runbook updates the
operator's export queries to the new names, and the dry run executes them
against the migrated copy. There is no export code to test.

The existing `tests/test_account_deletion.py` new-column guard moves to the
new key name.

**5. Dry run on a copy of production, never on production.**

- Take a consistent copy without touching the live service. Use SQLite's
  online backup API (`sqlite3 .backup`) per database, the LanceDB directory
  copied after a flush, and plain copies of the JSON and marker files. All of
  it is read-only on the droplet. **Not** `deploy/backup-restore.sh`, which
  *restores* (it stops consumers at `:282` and replaces the live volume at
  `:300`). And not `backup.sh`'s live tar, which tolerates files changing
  under it (`:164`).
- Copy it off the droplet, and run the migration in the Linux oracle container
  against the copy.
- The report gives, per database: row counts per table before and after (old
  name mapped to new name; they must match exactly), and actor-prefix counts
  (all moved, none left).
- Reader checks before and after: `get_status`, `read_graph` (status, graphs,
  runs) for every home, and the account-deletion refusal analysis for every
  account. The answers must be identical.
- The dry-run report is attached to the C4b PR. If any count or reader
  differs, C4b does not run.

**6. Backup and a written rollback.**

- **The backup is quiesced and consistent.** At the start of the window: stop
  the container and the backup timer, so nothing writes. Then take a full tar
  of the data volume with `deploy/backup.sh`'s full tier, run against the
  stopped volume, and record its timestamp and sha256. This is the **recovery
  point**.
- **Nothing is written after the recovery point until the migration has
  verified**, because the server binds its port only after verification. A
  rollback before the service reopens therefore loses nothing.
- **Rollback after the service has reopened loses data, and that is
  stated.** Restoring the snapshot discards every write since the recovery
  point: turns, runs, requests, uploads. It cannot undo external effects
  already performed, such as a sent message or an opened PR. So once the
  service has reopened, the default is to **roll forward** (fix and
  redeploy). Restore is chosen only when the migrated data is wrong, and the
  window's writes are then listed from the live database before the restore,
  so each affected person can be told.
- **Rollback steps:**
  1. Stop the container.
  2. Restore the snapshot with `deploy/backup-restore.sh`, its intended use.
  3. Deploy the image sha that ran before C4b (`deployed_sha.py` records it).
     That image is C4a or later, so it accepts the restored layout 1 / stable
     marker.
  4. Run the canary.
- The steps go in `docs/ops/` with exact commands, and are rehearsed once
  against the dry-run copy.

**7. A measured quiet window.** Every deploy interrupts in-flight turns. Turns
on production over the 14 days to 2026-10-01 17:00Z (read-only count of
`agent_turns.created_at`, by UTC hour):

| UTC hours | 17-21 | 22-03 | 04-16 |
|---|---|---|---|
| Turns/hour, 14 days total | 6-15 | 25-63 | 85-130 |

Proposed window: **18:00-20:00 UTC** (11:00-13:00 Pacific). It is re-measured
the day before, and the founder's own planned sessions are checked, because
the founder is the main live user.

### D8. Agent self-reference and existing brains

Served guidance, the persona and seed text, and `universe_tools.py`'s
self-description ("My universe is a folder, mounted at /u ...") switch to the
command center. **Existing users' brain and soul files are not rewritten**:
they are agent-authored memory in the user's space, and rewriting them would be
the platform editing a user's agent. The served guidance names the new term, so
an agent with older notes reads "command center" in its current instructions
and adopts it. The `/u` mount point is unchanged; it is a path, not the word.

### D9. Native shells carry their own copy and ship on their own release

The refute found copy that a live SPA deploy does not reach:

- the Android notification-channel description
  (`mobile/native/android/TinyAssetsMessagingService.java:225`);
- the iOS microphone permission string (`mobile/scripts/add_ios_scheme.py:46`);
- the bundled loading pages (`mobile/www/index.html:43`,
  `desktop-app/src/loading.html:43`).

C0 changes all four. Android also returns early when the channel already exists
(`:222`), so an updated binary would keep the old description. `ensureChannel`
therefore always calls `createNotificationChannel`. Android applies a new name
and description to an existing channel id and leaves the user's importance
setting alone. These reach people only with the next Play and desktop
releases. The founder runs those (`docs/host-actions.md`), and until then the
shells show the old word on those few strings.

### D10. One clean cutover after C0: code, storage and ids in one freeze window

*Founder, 2026-10-01: "no old ids do not keep working, we are still early in
production so we are not maintaining old systems we dont have old users we
just have current testers that need to cleanly move to the new system". Also:
"change it also" for the `u-` prefix. Together these replace every alias, dual
key and deprecation window in the earlier drafts. The migration's safety stays,
because the testers' data is real.*

**The shape: fewer slices.**

| Slice | What | Ships as |
|---|---|---|
| C0 | Copy | Landing (#4189) |
| C1 | The public MCP edge: current names only, retired names refused naming the new one (D3/D4) | Its own PR; its edge translation is temporary |
| C2 | Living docs and specs | Any time |
| C4a | The layout guard and a migration-aware `deploy_fail_safe` (D7.2) | Its own image, at least one day before the cutover |
| **Cutover** (C3 + C4 + C5) | Codemod-renamed code, the storage migration, and the id migration, in **one image, one freeze window, one locked migration run** | One change: `command-center-cutover` |

**Why C3, C4 and C5 merge.**

- Shipping code before storage would need serialization adapters (D6) and
  digest freezes. That is compatibility machinery whose only job is the gap
  between two deploys.
- In one image, code and data change together and nothing serves until
  verification passes. No reader ever sees two shapes, so D7's "readers accept
  both shapes" fallbacks and D6's adapters are dropped.
- The id migration touches the same rows and folders. Running it in the same
  locked run means one backup, one marker and one rollback, instead of three.

**The cutover run, in order, under D7's exclusion protocol:**

1. **Names.** Tables, columns, values and `CHECK` literals; marker files;
   serialized keys; LanceDB, checkpoint and JSON keys. Also:
   - stored branch-definition fields (`delivery_sender_universe_id`, workspace
     `storage: "universe"`);
   - stored custom-UI bundles' bridge keys (`universe_id` / `universe_name`
     become `command_center_*`). Production on 2026-10-01 has 0 bundles that
     use them, but the rule covers any;
   - existing bindings re-pointed from the retired default agent definition to
     the current one (C1 published it).
2. **Ids** (D11). Every stored `u-<ulid>` becomes `cc-<ulid>`, and every
   `u-<ulid>` folder is renamed.
3. **Local verification** (D7.4/D7.5). The inventory re-runs and must find
   zero retired names and zero `u-<ulid>` ids in any store; deletion and
   export are proven on the new shape.
4. **External records** (D11), after local verification and before
   reopening: idempotent, recorded per object, and reversible by a recorded
   inverse.
5. **The marker flips** to the new layout and the server binds its port.

**After the cutover:**

- C1's edge translation (`internal_value`, `public_response`) is deleted,
  because internal names now equal public names.
- The retired-name **refusals stay**. They are an error naming the new name
  (Hard Rule 8), not an alias.
- An old id, such as a connector's cached `graph_id` or an old link, gets the
  plain "unknown id" error. Testers reconnect or re-fetch.

**The freeze window.** You pause the other lanes. The cutover lands and
deploys in the measured quiet window (D7.7). Then you rebase the paused lanes
by running the same codemod on each branch.

### D11. The `u-` prefix becomes `cc-` (C5, inside the cutover)

**Mapping.** `u-<ulid>` becomes `cc-<ulid>`. The ULID body is unchanged, so an
id stays unique and time-sortable, and the mapping is deterministic and
reversible: the rollback inverse is `cc-X` back to `u-X`.

**Is `cc-` safe? Checked on 2026-10-01:**

- **Parsers.** `tinyassets/ids.py:23` and `:33` define
  `UNIVERSE_ID_PREFIX = "u-"` and `^u-[0-9a-hjkmnp-tv-z]{26}$`.
  `background_branch_authority.py:17` and `:190` key on that prefix. Two
  operator scripts match `^u-` (`scripts/remove_legacy_brain_artifacts.ps1:26`,
  `scripts/rename_live_data_universes_to_serial_ids.ps1:24`). The codemod
  moves all of them to one `COMMAND_CENTER_ID_PREFIX = "cc-"` and a matching
  regex. A test fails if any `"u-"` literal or `^u-` pattern survives in code.
- **Length.** Ids grow from 28 to 29 characters. SQLite TEXT has no limit.
  On Windows local installs, a deep path under `Documents/TinyAssets/<id>/...`
  grows by one character. The cutover's dry run on a production copy also
  reports the longest resulting path, checked against the Windows `MAX_PATH`
  guard the repo already uses for basetemps.
- **Ambiguity.** Other `cc-` values exist: craft cards generate
  `cc-<chapter>-<counter>` (`learning/craft_cards.py:65`). They cannot collide
  with `cc-<26-char ulid>`. The preflight checks the full target namespace
  `^cc-[0-9a-hjkmnp-tv-z]{26}$` for zero existing values; it does not forbid
  every `cc-` value. Every reader that classifies an id uses the full regex,
  never the prefix alone.

**What holds an id, and how each moves.** The inventory is derived (D7.1),
extended to values matching `u-<ulid>`:

- **Local stores, in every encoding**, migrated in phase 2. A prefix scan of
  TEXT columns is not enough (cutover refute #1, #4). The inventory decodes
  and rewrites each encoding by its own reader:
  - TEXT values;
  - JSON values **and JSON object keys** (`engine_mcp_http.py:308` keys a map
    by id);
  - **BLOB** canonical JSON. `storage/conversation_custody.py:66` stores
    thread JSON as a BLOB containing `universe_id` (`:181`), whose reader
    demands canonical bytes (`:200`) and equality with indexed columns
    (`:378`). It is re-canonicalised, not string-patched;
  - checkpoint payloads (via serde), LanceDB rows, and folder names;
  - FCM device rows, and notification and deep-link payloads in our
    databases.
- **Identities derived from an id**, recomputed with every reference to them
  in the same transaction:
  - **length-dependent keys**: `automations.py:1872` embeds the id's length in
    lease keys, and `:1881` slices by it. The code computes the length, but
    every stored key changes from `28:u-...` to `29:cc-...` and is rewritten as
    a whole key, never patched as a substring;
  - **hashed ids**: `api/http_connection.py:495` derives connection and grant
    ids from the id, and `:1202` recomputes them for credential rotation;
  - **digests over content that contains an id**:
    `conversation_run_admissions.py:340`, `provider_assignment_manifest.py:153`,
    branch-snapshot `content_hash` and admission `snapshot_sha256`
    (`storage/run_input_admissions.py:90`, `:182`).

  Where the hash input changes, the migration recomputes the digest and
  rewrites its references. The codemod's report lists every hash site, so
  none is missed.
- **Webhook tokens are not id-bound.** They are random (`storage/webhook_hooks.py:198`)
  and stored as a hash of the token alone (`:95`), so they survive unchanged.
  A webhook URL is affected only if its path carries the id; the inventory
  checks the URL shape.
- **Stripe**, in three places: subscription metadata (`billing/stripe_adapter.py:383`,
  read at `:186`), the **signed entitlement claim** over the id (`:163`,
  verified at `:205`), and checkout `client_reference_id` (`:378`). The cutover:
  1. **drains checkouts first.** New checkout creation is frozen at the start
     of the window. Open checkout sessions are expired through the API,
     because their frozen parameters must stay byte-identical for retries
     (`:295`) and cannot be rewritten in place;
  2. rewrites each subscription's metadata and **re-signs** its claim with the
     new id;
  3. **reconciles late events by Stripe object identity.** A webhook carrying
     an old id (an event generated before the rewrite) is resolved through the
     migration's recorded subscription/customer -> id map, never by the id in
     the payload. An old id that maps to nothing is **refused before any
     storage is touched**. The settlement path's database connector creates
     missing directories (`storage/subscription_state.py:415`, `:62`) and
     could otherwise recreate a retired home, so the cutover makes it refuse
     an unknown home instead.
- **WorkOS.** A repo search on 2026-10-01 found no WorkOS metadata write that
  carries a universe id. The cutover change re-runs that search and checks a
  live WorkOS user record before the run. It is not assumed.
- **Vault.** Credential vaults live inside the folder
  (`credential_vault.py:154`) and move with it. The vault is read as JSON
  (`:460`), with nothing derived from the id. The dry run proves every vault
  still reads, and resolves to the same connections, after the move.
- **Outside our reach, and stated:**
  - graph ids cached in testers' chat clients;
  - links in notifications already delivered;
  - ids pasted into third-party systems.

  These get the "unknown id" error, and testers reconnect or re-fetch. The
  cutover change's inventory names each kind, with evidence.

**Path builders.** Every path from an id to a folder goes through one
resolver, `data_dir()` / `command_center_dir(id)`. A test fails if code builds
`/data/u-...` or `/data/cc-...` by string.

**Deletion and export across the cutover.** Both run only on the new shape:
the server is down during the run, and the old shape no longer exists
afterwards.

Absence of old strings is necessary but not sufficient (refute #4). The proof
uses populated fixtures built by every real schema creator and asserts four
things:

- **No surviving operational identity:** no `u-<ulid>` value or `u-` folder in
  any operational store, in any encoding above, after migration;
- **Decoded round trips:** every migrated record reads back through its own
  reader (custody threads, checkpoints, LanceDB, bundles);
- **Reference and digest integrity:** every derived id, hash and foreign
  reference resolves;
- **Independent deletion and export:** `delete_account` removes every row of
  the account and none of another's, checked by direct row counts, not by
  the same scan.

**Verbatim content is exempt, by an explicit list.** A person's own content
and agent-written brain files keep any old word or id they contain (Hard Rule
9, D8): uploads, run outputs, conversation text, `soul.md` and notes. The
operational-identity scan skips exactly those stores and fields, named in the
cutover change, so that "nothing survives" never means rewriting what a person
wrote.

**Rollback.** Restore plus the previous image (D7.6). In addition, the
recorded Stripe inverse runs (metadata, claim, mapping), because restoring
the local snapshot does not rewind an external API.

**Irreversible effects, accounted for, not inverted:**

- push notifications already accepted by FCM, including ones not yet
  delivered. Delivery happens before the local receipt
  (`owner_notifications.py:349`, `:371`) and the payload contains the id
  (`:207`);
- any notification the migration itself would send. The migration suppresses
  owner notifications for its whole run, so it sends none;
- expired Stripe checkout sessions. Testers start a new checkout.

The cutover change lists each kind with its count from the dry run.

## Risks / Trade-offs

- **One big run.** Code, storage and ids change in one image (D10). The
  mitigations: a layout guard already in production, a dry run on a
  production copy, a quiesced backup, nothing served until verification, and
  restore plus the previous image plus the recorded external inverse as the
  rollback.
- **Old ids held outside our reach** break with a clear "unknown id": cached
  chat-client graph ids, delivered links, ids pasted into third parties (D11).
  The founder accepted this: testers reconnect.
- **Copy tests.** Many tests assert exact strings, so C0 updates them in the
  same slice. A green suite after C0 proves the tests moved, not that they were
  weakened: each changed assertion swaps the old string for the new one, and no
  assertion is deleted.

## Migration Plan

C0 (copy; the native strings ship with the next native releases) → C1 (the
public MCP edge as a clean cutover, D3/D4) → C2 (living docs and specs; any
time) → C4a (layout guard plus the migration-aware fail-safe, its own image, at
least one day of production) → **the cutover** (code, storage and ids in one
image, one freeze window and one locked run, D10/D11), followed by deleting
C1's edge translation.

Rollback:

- C0, C1 and C2 roll back by revert.
- The cutover rolls back only by restore plus the previous image plus the
  recorded external inverse (D7.6, D11). Never by a revert alone.

## Open Questions

None open. The founder decided the `u-` prefix on 2026-10-01: it becomes `cc-`
(D11).
