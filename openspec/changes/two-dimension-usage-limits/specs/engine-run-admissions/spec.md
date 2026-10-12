# engine-run-admissions (delta)

## REMOVED Requirements

### Requirement: Engine run admissions are charged as writes and settled by what fired

**Reason:** Founder directive 2026-09-30 — account usage limits are two
numbers, total cloud GiB and concurrent agent calls. The rolling-window write
and total caps (1200 / 3600 per hour) are neither, and they REFUSE work a
concurrency limit should queue. The settlement half of this requirement is not
a meter and survives, respecified below.

**Migration:** the caps' refusals (`usage_limit`, `run_usage_limited`,
`run_rate_limited`, `usage_limited`) and the `usage_notice` text are deleted,
not renamed. Callers that refused on them now wait for a seat
(`universe-seats`). The ledger's cap parameters (`write_max`, `total_max`,
`day_max`, `window_s`) are removed from the admission signature so no caller
can reintroduce a ceiling through it.

### Requirement: Usage, not shape, bounds a universe's runs

**Reason:** the rolling-day run meter (`RUN_DAY_LIMIT`, 20,000 per 24 h) bounds
how many times a loop went round, not what the universe occupies. Its stated
job — bounding "a self-launching chain paced under the hourly caps" — is done
structurally by seats: such a chain holds at most the background seat ceiling
and cannot grow.

**Migration:** a self-invoking chain is now bounded by seats and by the
interpreter stack, not by a day count. The "capacity returns at" notice has no
successor, because waiting work starts when a seat frees rather than when a
window rolls.

### Requirement: Outbound volume per universe is bounded per rolling hour

**Reason:** `DISPATCHES_PER_HOUR` (5,000) and `BYTES_PER_HOUR` (2 GiB) are
per-hour account counters, the category the directive removes.

**Migration:** nothing dispatches without holding a seat, so egress
concurrency is bounded by the tier. The per-call and per-run byte caps stay —
they are execution safety for one packet and one run, not account usage.

## ADDED Requirements

### Requirement: The admission ledger records what a run did, and charges nothing

The ledger SHALL continue to record, per run, whether everything it fired was
a read (`GET`/`HEAD` on the authenticated external call, or nothing at all) or
a write, because the effect boundary reads that classification for reasons
unrelated to usage. It SHALL admit every run unconditionally: there SHALL be no
rolling-window count, no per-hour or per-day ceiling, and no refusal reason
derived from a count of prior admissions. A write settlement SHALL remain final
against a later read settlement, and a settlement arriving before its bind
SHALL still be applied at bind time. Rows SHALL be pruned once they can no
longer be settled. No surface SHALL report a usage notice, a capacity-returns
time, or a rate-limit reason from this ledger.

#### Scenario: A run is never refused by the ledger
- **WHEN** a universe starts far more runs in an hour or a day than any former cap allowed
- **THEN** every admission succeeds and no surface reports a usage limit, rate limit or capacity-returns time

#### Scenario: Settlement still tells the effect boundary what a run did
- **WHEN** a run fires only `GET` calls, and another fires a `POST`
- **THEN** the first settles as a read and the second as a write, and a later read settlement cannot downgrade the write

#### Scenario: A self-invoking chain stops at seats and the stack, not a meter
- **WHEN** a branch blocking-invokes itself indefinitely in a universe
- **THEN** it is bounded by the universe's seats and by a named interpreter-stack limit, and no run is refused for exceeding a count

## MODIFIED Requirements

### Requirement: Workspace jobs are admitted and settled through their own ledger kind with the maximum charge reserved before the wire

The engine SHALL reserve the runtime-controlled maximum transport byte charge
before workspace network activity, keeping existing per-universe rolling-byte,
lease, pool, retained-storage and lock checks. It SHALL reconcile downward only
from trustworthy measurement, retaining the maximum for an unknown or interrupted
transfer. Workspace job-count rows SHALL remain observations and SHALL NOT
independently refuse work based on jobs per hour. No caller-supplied packet
ceiling SHALL decide quota consumption.

Push and discard SHALL preserve their deterministic operation identity and
idempotent reservations. A zero-byte discard SHALL NOT be refused merely because
earlier workspace starts exhausted the retired jobs count. Generic execution and
effect admission remain separate existing controls. Workspace transfer bytes
SHALL remain separate from generic delivered-result byte accounting; workspace
effect nodes still consume the generic effect-node dispatch count.

#### Scenario: More than ten light operations
- **WHEN** eleven authorized workspace operations have sufficient actual resource capacity
- **THEN** the eleventh is admitted without an independent jobs-per-hour refusal and the observational count records eleven

#### Scenario: A retried push does not duplicate its reservation
- **WHEN** the same identified push reservation is requested again
- **THEN** its existing reservation is returned without adding another workspace job/byte record

#### Scenario: The hourly workspace bytes are exhausted
- **WHEN** a new checkout's runtime-controlled reservation would exceed the existing transfer window
- **THEN** it is refused before transport with workspace_quota_exceeded and the actual byte constraint

#### Scenario: Two checkouts compete for remaining bytes
- **WHEN** two checkouts concurrently request more combined bytes than remain
- **THEN** only a fitting reservation commits and the other is refused atomically

#### Scenario: Cleanup after ten starts
- **WHEN** more than ten jobs are recorded and an authorized zero-byte discard is requested
- **THEN** the observational jobs total does not block cleanup

#### Scenario: Unknown transfer remains conservative
- **WHEN** checkout fails and no trustworthy transfer measurement exists
- **THEN** its existing maximum byte reservation remains; a deleted or small local tree is not proof of zero transferred bytes
