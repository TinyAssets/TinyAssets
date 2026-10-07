## MODIFIED Requirements

### Requirement: Engine run admissions are charged as writes and settled by what fired
The engine SHALL admit every engine-triggered run and every scheduled
automation run through one per-universe rolling ledger
(`<data_dir>/.engine_run_admissions.db`), charging each as kind `write`
atomically at admission time, with schema inspection and migration inside
the same immediate transaction. The engine SHALL admit every durable engine
write (`write_graph`, remix, brain write) through the same ledger as kind
`engine`. The engine SHALL refuse a run admission when the universe's
`write` admissions in the window have reached the write cap (300 per 3600 s)
OR its admissions of any kind have reached the total cap (900 per 3600 s);
it SHALL refuse an `engine` admission at the total cap without a separate
engine-mutation ceiling or a reserved share for the same universe's runs; and
SHALL say which cap refused. An `engine` row SHALL never be bound to a run
or reclassified. A refusal caused by an unusable or untrusted ledger SHALL
say so and SHALL NOT be reported as a quota. Rows outside the window SHALL
be pruned on the next admission. Admission SHALL return a
ticket (the ledger row id), or the sentinel `ADMITTED_UNRECORDED` when a
tolerated ledger error admitted without a row (fail-open callers only);
`run_graph` and the automation runner SHALL bind a real ticket to the run
they start the moment its id exists. A ledger whose parent directory does
not exist yet SHALL be created there, never treated as "no cap"; its path
SHALL come from the canonical data-dir resolver, never the CWD.
When the run has finished, its admission SHALL be settled: reclassified as
`read` if and only if every effect that ran was a `GET`/`HEAD`
`authenticated_external_call` (a refused-before-the-wire packet counts by the
verb it declared), or no effect ran at all — including a run that failed or
was cancelled, which fires nothing; any other sink (known or unknown), any
other verb, or an unnamed verb SHALL settle it as `write`, and a `write`
settlement SHALL be final: a later `read` settlement for the same run SHALL
change nothing. A settlement that arrives before the bind SHALL be kept and
applied at bind time. Settlement rows SHALL expire two hours after they are
written, pruned on every settle and every admission. Every engine surface's
refusal SHALL name the cap that refused. The ledger
SHALL be refused (fail closed) when it is a symlink or resolves outside its
data dir. Older ledgers SHALL be migrated additively, their rows counting as
writes.

Because effects fire at node time, a run MAY fire a write and then fail or be
cancelled. On every terminal status the runtime SHALL settle the run's
admission from the effect chain's record of what fired — a fired write
settles as `write` (final) even when the run ends `failed`, `cancelled` or
`interrupted`; a run whose chain fired nothing settles as `read`. Settlement
SHALL have exactly one owner per run (the chain, via the terminal status
write); the "failed run fired nothing" shortcut SHALL apply only to runs that
had no chain. A `write` settlement SHALL be final in both directions: it
SHALL promote an admission a `read` settlement reclassified earlier, and a
later `read` SHALL change nothing. The clause "a run that failed or was
cancelled … fires nothing" is withdrawn. All other clauses of this
requirement are unchanged.

#### Scenario: A GitHub job's reads do not spend the write budget

- **WHEN** a universe's engine runs a branch whose only effects are `GET`
  calls (read the ref, read the file) and the run completes
- **THEN** its admission is reclassified as `read` and the universe's write
  budget is unchanged by that run

#### Scenario: A write is charged before it runs and stays charged

- **WHEN** the engine admits a run and the run fires a `PUT` or `POST`
  authenticated call, or any other sink
- **THEN** the admission was counted at admission time and remains kind
  `write` after settlement

#### Scenario: A loop of read-only runs is still bounded

- **WHEN** a universe has 900 admissions of any kind in the rolling hour
- **THEN** the next engine run is refused even if no write budget was spent

#### Scenario: Two first touches of a legacy ledger cannot both pass

- **WHEN** two admissions race on a ledger that still has the old schema and
  one write of budget left
- **THEN** exactly one is admitted and exactly one row is recorded

#### Scenario: A scheduled automation settles like a foreground run

- **WHEN** a scheduled automation's run fires only `GET` effects
- **THEN** its admission, bound to that run when it started, is reclassified
  as `read`

#### Scenario: A branch cannot be pre-classified as read-only

- **WHEN** a run is admitted
- **THEN** it is charged as a write regardless of the branch's shape, because
  the verb an effect uses is decided by the packet the run produces

#### Scenario: A run that was never admitted has no admission

- **WHEN** a browser-triggered run's effects fire
- **THEN** settlement changes no admission; when a ledger already exists it
  may leave a settlement row that expires within two hours; no ledger is
  created for it

#### Scenario: A write that later fails stays a write

- **WHEN** an admitted run's `PUT` effect fired and its status is then
  rewritten to `failed` (provider-authority release failed)
- **THEN** the admission stays `write`

#### Scenario: A run that failed settles as a read

- **WHEN** an admitted run fails, is cancelled or times out before its
  effects could fire
- **THEN** its admission is reclassified as `read` when the terminal status
  is recorded

#### Scenario: A fast run that settles before its bind is not lost

- **WHEN** a run's settlement arrives before `run_graph` has bound the
  ticket to its id
- **THEN** the settlement is kept and applied when the bind happens

#### Scenario: An unknown sink is a write

- **WHEN** an admitted run's node names a sink the platform does not know
- **THEN** the admission stays `write`

#### Scenario: A verb that disagrees with the request method is a write

- **WHEN** a packet declares `verb: GET` and `request.method: PUT`
- **THEN** the adapter refuses it before the wire and the admission stays
  `write` (the declared verb says nothing about intent)

#### Scenario: Branch authoring does not spend the effect budget

- **WHEN** a universe's engine has made 30 `write_graph` calls in the rolling
  hour and then runs a job that writes externally
- **THEN** the job's writes are admitted against an untouched 300-write budget

#### Scenario: Engine mutations can use the owner's total allowance

- **WHEN** a universe has 600 engine edits in its rolling hour and no other admissions
- **THEN** edit 601 is admitted, and engine edits can fill the existing total of 900
- **AND** the next admission of either kind refuses at the total cap, while another universe remains independent

#### Scenario: Mixed kinds compete atomically for the total

- **WHEN** engine edits and run submissions race for the final total admission with write capacity remaining
- **THEN** exactly one receives a recorded ticket and all others refuse at the total cap

#### Scenario: Edit usage does not permanently pause an automation

- **WHEN** engine edits exhaust the total allowance and a scheduled run becomes due
- **THEN** that period records `run_rate_limited` without launching or pausing the automation
- **AND** a later due period can be admitted after the old charges expire

#### Scenario: A read that arrived first does not hide a write
- **WHEN** a terminal status settles `read` while an adapter is still running and that adapter then delivers a PUT
- **THEN** the later `write` settlement promotes the admission row to `write`

#### Scenario: A write that fired before a failure stays a write
- **WHEN** `create_branch` (POST) delivered and then `write_readme` was refused, failing the run
- **THEN** the run's admission settles as `write`, and no later `read` settlement changes it

#### Scenario: A run that failed before any effect settles as a read
- **WHEN** a run fails at its first node before any effect fires
- **THEN** its admission settles as `read`

The settlement rule that every sink other than a GET/HEAD
`authenticated_external_call` settles a run as a write SHALL have exactly one
exception: a `workspace` `checkout` (which is charged as a `workspace` job)
SHALL settle the run's external-write admission as a read. A `workspace` `push`
settles as a write; `discard` is a workspace job and SHALL NOT be classified as
a read. All other clauses of this requirement are unchanged.

#### Scenario: a checkout does not spend the external-write budget
- **WHEN** a run checks out a repository, reads and runs it, and writes nothing externally
- **THEN** its admission settles as `read` and one `workspace` job is charged
