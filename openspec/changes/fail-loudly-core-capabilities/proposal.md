## Why

The owner-isolation cutover left basic user operations broken while deployment health remained green. Detect capability loss before merge and immediately after deploy, and page on systemic runtime failures without waiting for a user report.

## What Changes

- One executable capability catalogue drives synthetic production-image acceptance, live checks and failure reporting.
- Required CI builds the PR production image and exercises real owner cells, broker, launcher and relays with synthetic data and a deterministic model boundary.
- Deploy and five-minute hosted checks exercise a dedicated owner, report exact failed capability and revision, and use the existing GitHub/Pushover alert channel. Deploy failure participates in rollback.
- Stable runtime failure codes and rolling failure rates expose systemic faults even when synthetic traffic succeeds.
- Reintroduction tests document detection of the cutover regressions.

## Capabilities

### New Capabilities

- `core-capability-assurance`: executable acceptance, live capability monitoring and systemic failure alarms.

### Modified Capabilities

None.

## Impact

New assurance scripts and runtime telemetry; required CI, deploy and scheduled workflows; protected operational health output. No real owner data or production credentials enter PR CI. Owner: Codex; branch: `feat/fail-loudly-core-capabilities`; one infra-change PR. The parallel regression lane owns `scripts/role_image_oracle.py` and its repairs.
