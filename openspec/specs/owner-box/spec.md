# Owner Box

## Purpose

Define the owner-box erasure boundary and its durable recovery contract.

## Requirements

### Requirement: Erasure fails closed independently of box availability
Authentication and every broker box admission SHALL consult the single root-owned
erasure ledger. Only explicit cutover or fresh-install initialization may create
its schema and sealed initialization header. Reads SHALL be read-only; absent,
empty, corrupt or unreadable authority SHALL refuse authentication with a loud log.
Billing, custody and router credentials SHALL each run and persist completion
independently of box cleanup. External identity deletion SHALL run last, only
after every preceding phase is recorded complete in the ledger. Failed box
cleanup and deferred identity deletion SHALL retry.
Erasure and absent-tree completion SHALL require the mounted XFS device and UUID
recorded by host setup, including for retained and quarantined trees. The visible
mount MUST expose the whole filesystem (mountinfo root `/`) and be the topmost
mount at that path. Immediately after fencing, deletion SHALL retry a separate
`broker_channels` ledger phase closing all of the principal's broker channels.
Every established broker stream, including public tunnels, SHALL recheck erasure
at most every second and close on a fence or unavailable ledger. Cleanup admission
SHALL be permanently deletion-only, including after a local 409 refusal.
Cleanup targets SHALL contain only store-relative paths and UIDs, resolved against
each process's configured store. Startup SHALL record cleanup failures, keep those
owners fenced, and open control listeners for the remaining owners.

#### Scenario: A fenced owner's box is still running
- **WHEN** a fenced owner requests any broker box operation
- **THEN** admission is refused through the authentication ledger check

#### Scenario: Cleanup cannot reach the box or its filesystem
- **WHEN** resolving the box fails or the owner store is unmounted
- **THEN** billing, custody and router credential deletion still run and record their results
- **AND** tree completion remains pending until verified on the expected mount
- **AND** external identity survives until every earlier phase is recorded complete

### Requirement: Cutover launch and erasure completion use production paths
The extracted harness root SHALL be traversable by an owner UID (0555).
The archive rehearsal SHALL execute the image Python and launch an owner daemon
with the production argv from the production extraction. The Linux jail workflow
SHALL also launch that extraction through real runsc.

HTTP connection commits SHALL consult `erasure_ledger.py` inside the row-writing
transaction; a fenced principal SHALL write nothing. Close-all SHALL cancel
in-flight HTTP connection channels by their registered principal.

Startup recovery and account deletion SHALL call the same owner-destroy
function. It SHALL retire admission, stop the runtime, erase every recorded
tree, clear quota, and remove relay, app socket and secret before recording
owner_tree complete. A root host may retire only a ledger-fenced principal's
matching permanent UID through the broker. A missing reservation cannot retire
another principal's UID.

External identity deletion SHALL run last, after every other phase is recorded
complete in the erasure ledger. Billing cancellation SHALL still run when box
cleanup fails. Durable retries SHALL finish identity deletion once eligible.

#### Scenario: A newly extracted image launches an owner
- **WHEN** deploy or rehearsal extracts the image into a private staging directory
- **THEN** the final root is 0555 and the owner UID traverses it, imports TinyAssets with the image Python, and serves the production daemon

#### Scenario: Cold-start recovery needs broker admission retirement
- **WHEN** the host restarts with pending erasures and no running broker
- **THEN** registration and erasure listeners open before cleanup waits for broker readiness
- **AND** recovery calls full owner destruction and retains every task if any sub-step fails

#### Scenario: A connection commit resumes after account erasure
- **WHEN** an HTTP_CONNECT worker resumes after the owner's fence and custody deletion
- **THEN** its channel is cancelled and the row-writing transaction refuses the fenced principal
