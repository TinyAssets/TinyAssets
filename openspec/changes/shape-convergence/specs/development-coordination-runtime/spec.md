## ADDED Requirements

### Requirement: Declared shapes have one path and an executable guard
Every declared shape SHALL appear as one row of the Shape registry in
`docs/architecture.md` (a shape is declared by README Direction, an accepted
ADR or an accepted spec), and SHALL have a structural guard that runs on every
pull request. For a drifted shape, the guard SHALL fail when the old path's
measured count rises above its recorded ceiling. For a converged shape, it
SHALL fail when any old-path count is above zero or when a second entry point
for the shape appears outside its registered paths.

#### Scenario: A second model-call path is added
- **WHEN** a pull request from any fleet adds a module that calls a provider adapter outside the registered model-call paths
- **THEN** the structural guard fails and names the shape, the file and the pattern

#### Scenario: An old path is patched but not deleted
- **WHEN** a pull request grows a drifted shape's old path
- **THEN** the guard fails with the shape's ceiling and the measured count
- **AND** a pull request that only lowers the count passes without editing the ceiling

#### Scenario: A registry row without a guard
- **WHEN** the architecture registry gains a row that has no guard entry
- **THEN** the registry-parity check fails

### Requirement: A superseding change deletes what it supersedes
A change that replaces a path or another change SHALL delete the replaced
code path in the same release, and SHALL remove each superseded change from
`openspec/changes/` outside `archive/`. A cutover SHALL NOT leave an opt-in
environment flag that selects between the old and new path of one registered
shape.

#### Scenario: A superseded change stays open
- **WHEN** a proposal states that it supersedes a change that still exists outside the archive
- **THEN** the structural guard fails and names both changes

#### Scenario: A new path ships behind a switch
- **WHEN** a pull request adds a `TINYASSETS_*` flag that chooses between two paths of a registered shape
- **THEN** the structural guard fails

### Requirement: Reconciliation runs with no host online
The repository SHALL reconcile open pull requests, remote branches and
`openspec/changes/` against the Shape registry on a schedule in GitHub
Actions, without any developer machine or agent session online. It SHALL
maintain exactly one tracking issue that lists:

- lanes touching the same shape;
- pull requests that patch a drifted shape's old path without lowering its
  count;
- changes that are complete but unarchived, superseded but open, or stale;
- shapes whose count reached zero.

It SHALL NOT merge, close or edit code.

#### Scenario: Two fleets work the same shape
- **WHEN** two open pull requests from different branch prefixes both touch paths registered to one shape
- **THEN** the next scheduled run lists both under that shape in the tracking issue

#### Scenario: No host is online
- **WHEN** every developer machine and agent session is offline
- **THEN** the scheduled run still updates the tracking issue

### Requirement: Documents live only in the registered homes
A new file under `docs/` SHALL be added only to a registered home. The homes
are:

- `docs/architecture.md`
- `docs/decisions/`
- `docs/concerns/`
- `docs/host-actions.md`
- `docs/reference/`
- `docs/getting-started/`
- operations runbooks under `docs/ops/`

An accepted ADR SHALL cite an existing path under `openspec/specs/` or
`openspec/changes/`.

#### Scenario: A new design note
- **WHEN** a pull request adds a file under `docs/design-notes/`
- **THEN** the structural guard fails and points to the registry, an ADR or an OpenSpec change

#### Scenario: A decision without a spec
- **WHEN** an ADR with status Accepted cites no OpenSpec spec or change
- **THEN** the structural guard fails
