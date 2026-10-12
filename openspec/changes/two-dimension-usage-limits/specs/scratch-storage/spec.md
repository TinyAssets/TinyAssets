## MODIFIED Requirements

### Requirement: Permanent workspaces are immutable-by-host generations chosen at checkout

A checkout with `storage: "universe"` SHALL build a new opaque generation from
staging's bundle beneath a no-follow universe directory handle, and SHALL charge
it to the owning account's storage quota (`account-storage-quota`). No
workspace-specific quota exists.

Admission SHALL reserve the smaller of the lease bound and the account's headroom.
The headroom SHALL include the bytes of the generation this checkout would
replace, because publishing owes that generation's discard atomically.
Admission SHALL be refused as `storage_quota_exceeded` before any lease exists or
any bytes move when that reservation is below the minimum workspace size.

Before publication the runtime SHALL measure the new generation, including
provisioned files. When the generation exceeds its reservation, it SHALL be
refused and discarded, and the existing generation SHALL remain untouched.
`publish_generation` SHALL require an admitted storage reservation, so that no
path moves bytes into permanent space unadmitted. Publication SHALL only
atomically switch the repository key's authoritative generation and enqueue the
previous generation for `discard_permanent_generation`.

There is no `pin`, no `reuse` and no refresh in place: host git SHALL never open
a previous generation. A permanent workspace is removed only by `discard` (an
outbox transition that immediately revokes any capability over it), never by age.

#### Scenario: a second permanent checkout never opens the first generation
- **WHEN** a universe checks out the same repository twice with `storage: "universe"`
- **THEN** the second checkout populates a new generation from a fresh bundle, switches the authoritative generation atomically, and the first generation is quarantined and deleted through the outbox without any host git process opening it

#### Scenario: a quota refusal destroys nothing
- **WHEN** a permanent checkout would exceed the owning account's quota
- **THEN** it is refused as `storage_quota_exceeded` and the existing generation, if any, is untouched

#### Scenario: an empty free account holds a small workspace
- **WHEN** an account on the free tier with no stored bytes checks out a 300 MiB repository as a permanent workspace
- **THEN** the reservation fits within the free quota and the workspace is created

#### Scenario: provisioning past the reservation is not published
- **WHEN** provisioning grows a new generation beyond its reservation
- **THEN** the generation is refused and discarded before publication and the previous generation stays authoritative
