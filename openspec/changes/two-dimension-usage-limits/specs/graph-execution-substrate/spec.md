# graph-execution-substrate (delta)

## ADDED Requirements

### Requirement: An agent node holds a seat while it executes, and a blocking nested invoke inherits it

Each agent node execution SHALL acquire one of its universe's seats before it
runs a model and SHALL release it on every terminal path. A node that cannot
get a seat SHALL wait rather than fail, and its run SHALL report the waiting
state to the owner rather than appearing stalled. A sub-branch invoked
BLOCKINGLY SHALL re-enter its caller's seat, because a blocked caller is not
executing a model; an asynchronous or by-version invocation SHALL take its own
seat. The seat bound SHALL NOT limit how many agent nodes a graph may contain,
how deep branches may nest, or how many effects a branch may declare.

#### Scenario: A graph wider than its seats completes
- **WHEN** a graph with more concurrent agent nodes than the universe has background seats runs
- **THEN** the nodes run in waves as seats free and the run completes successfully

#### Scenario: A waiting node is visible, not stalled
- **WHEN** an agent node is waiting for a seat
- **THEN** the run's status names it as waiting for a free seat and how many are running

#### Scenario: Graph shape is still unbounded
- **WHEN** a user builds a graph with many more agent nodes than their tier has seats
- **THEN** authoring, saving and starting the graph all succeed

## ADDED Requirements

### Requirement: Workspace resource observations distinguish allocation and transfer
Resource observations SHALL distinguish observational workspace starts, current
lease allocations, retained workspace bytes and rolling transport reservations.
They SHALL NOT change admission, locks, outbox ownership or authorization.
Unavailable measurements SHALL remain unknown, and a universe-local lock
observation SHALL NOT claim host-global exclusion.

#### Scenario: Released lease remains in a rolling window
- **WHEN** a lease is released but its job/byte observations remain within the hour
- **THEN** live allocation and historical consumption are reported separately

#### Scenario: Broader universe storage is not measured
- **WHEN** only retained workspace bytes are available
- **THEN** evidence labels that coverage rather than claiming total universe storage

## MODIFIED Requirements

### Requirement: Interrupted runs resume from checkpoint under owner, status, checkpoint, and admitted-definition guards

`resume_run` SHALL resume a run only from its `SqliteSaver` checkpoint and only
when four guards pass: the caller `actor` owns the run (else `auth_failed`), the
run is `interrupted` (a run already `resumed` is idempotently returned; any other
status raises `not_interrupted`), a checkpoint exists for the run's `thread_id`
(else `no_checkpoint`), and the run's own durable **admission envelope** resolves
(else `admission_not_reconstructable`).

The fourth guard replaces the retired `branch_version_mismatch` reason. The
resumed definition SHALL come from the run's private, nullable
`runs.admission_envelope_json` column — the frozen `BranchDefinition` plus the
effective `recursion_limit` and `concurrency_budget_override` captured at
admission, in the same transaction that claims the `thread_id` and strictly
before executable submission. The injected `branch_lookup` parameter is retained
for signature compatibility and SHALL NOT be consulted, because it resolves the
CURRENT editable definition. The envelope SHALL NOT appear in `_row_to_run`,
`get_run`, `list_runs`, or any MCP projection; it is read only after the
ownership gate. `runs.branch_version_id` keeps its existing meaning — the user
selected this published version — and SHALL NOT be repurposed as a platform pin.

There SHALL be no legacy reconstruction: a run admitted before the column
existed, or carrying a corrupt, unknown-schema, malformed or row-mismatched
envelope, SHALL refuse `admission_not_reconstructable` before provider admission
and before any effect fires, rather than substituting the current definition or a
guessed execution default. Admission truth SHALL NOT be inferred from a function
signature. A capture failure SHALL terminalize the reserved run as `failed`
instead of dispatching it.

#### Scenario: resume runs the admitted definition after a draft edit
- **WHEN** a def-based run is admitted, its draft definition is then edited, and the owner calls `resume_run`
- **THEN** the run resumes on the admitted node set, `branch_lookup` is never called, and the admitted `recursion_limit` and `concurrency_budget_override` reach `compile_branch` and `app.invoke`

#### Scenario: a pre-envelope run refuses instead of guessing
- **WHEN** `resume_run` is called on a run with no admission envelope, with or without a `branch_version_id`
- **THEN** `ResumeError` with reason `admission_not_reconstructable` is raised before provider admission and no graph is dispatched

Every path that submits a reserved run to `_invoke_prepared_branch` SHALL
capture that run's admission envelope before dispatch, from the exact frozen
definition and execution choices it dispatches with — including the receiver
delivery worker and the admitted run-input worker, which execute reserved runs
and are therefore admissions, not non-executing intents. The captured effective
concurrency budget SHALL equal `concurrency_budget_override` when one is
recorded and the frozen definition's own `concurrency_budget` otherwise,
verbatim — `0` (tracked, unbounded) is distinct from absent (untracked), and no
ceiling SHALL be imposed beyond what the compiler can execute. A malformed
execution choice SHALL refuse rather than degrade to absent. The admitted
`recursion_limit` SHALL be re-applied as the recursion **cap for the resumed
segment**, not as a remaining-step budget carried over from the interrupted
segment; this is the same per-invocation ceiling semantics `_invoke_graph`
applies, and it replaces the prior behaviour of silently taking LangGraph's
stock default on resume.

A storage fault anywhere in the transaction that claims the `thread_id` SHALL
fail closed as an envelope capture failure naming the reserved run, so the run
settles instead of remaining queued and undispatchable. Settlement SHALL NOT
rewrite a run that is already cancelled or otherwise terminal. As-built
limitation: when the settle path is itself unable to complete — the storage or
the execution-authority guard it needs is concurrently unavailable — the
reserved row MAY remain `queued`; in that case it is still never dispatched, so
the failure stays closed rather than executing an unprovable definition.

#### Scenario: envelope capture failure never dispatches
- **WHEN** the admission envelope cannot be written for a run already reserved
- **THEN** that run is terminalized `failed` and never submitted to the executor

#### Scenario: a reserved run executed by a worker is resumable
- **WHEN** the receiver delivery worker or the admitted run-input worker dispatches a reserved run
- **THEN** that run's envelope is captured first and a later `resume_run` resolves the definition and execution choices it actually executed with

#### Scenario: a cancelled run is not rewritten by a capture failure
- **WHEN** a reserved run is cancelled and its admission envelope then fails to persist
- **THEN** the run keeps its `cancelled` status and the reported outcome is that status
