# Cost-driver receipts

## Purpose
Measure what each owner costs the platform (W10, row N12), one receipt per owner per day,
without limiting anything.

## Requirements

### Requirement: Receipts measure and never limit
W10-R1. The platform SHALL record box-awake intervals where boxhostd starts and stops
sandboxes, with the size class the wake reserved and the cgroup's peak memory and CPU at
stop; relay bytes in the broker's public egress; and one storage sample per owner image per
day. An interval opened at box start SHALL close on stop, on a crash, or, after a boxhostd
restart, at its best known end. Each store SHALL live in its process's private state, never
where a box can reach. A metering write failure SHALL NOT stop a box from starting or
stopping; it SHALL be logged as an error and counted against that day's receipts. No
admission module SHALL import `tinyassets.metering`, so the account limits stay two (S26).

#### Scenario: A box crashes
- **WHEN** an awake box's sandbox exits on its own
- **THEN** its interval closes with the reason `crashed` and the cgroup facts read before the stop

#### Scenario: boxhostd restarts
- **WHEN** boxhostd starts and finds intervals its previous process left open
- **THEN** a box still running closes now and any other at the last time it was seen running

#### Scenario: The store cannot be written
- **WHEN** a metering write fails
- **THEN** the box still starts or stops, the failure is logged and that day's receipts report it

#### Scenario: Admission reads a receipt
- **WHEN** any module outside the `check_shapes.py` N12 allowlist imports `tinyassets.metering`
- **THEN** `check_shapes.py` N12 fails

### Requirement: One receipt per owner per day
W10-R2. A daily job SHALL roll each finished day up into one CloudEvents record per owner,
driver and size class: box-seconds, relay GB and storage GB-days. Thin-loop seconds SHALL
stay a driver with no producer until the loop-worker pool records them (ADR-023). The read
SHALL be founder-only: root on the box host, which alone can open both stores.

#### Scenario: A day is rolled up
- **WHEN** the first lifecycle pass of a UTC day runs
- **THEN** every earlier day not yet rolled up has one record per owner and driver, clipped to the day

#### Scenario: A non-founder reads receipts
- **WHEN** the receipt read runs as any uid but root
- **THEN** it refuses before opening a store
