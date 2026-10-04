# background-branch-execution-authority (delta)

## ADDED Requirements

### Requirement: An invoke_branch edge never widens execution authority

The system SHALL run every invoked child branch (via `invoke_branch_spec` or
`invoke_branch_version_spec`, blocking or async, at any nesting depth) under an
immutable execution context derived from the AUTHENTICATED top-level run — carrying
the authenticated actor, the execution universe, the running definition's provenance,
and a capability ceiling. The child SHALL run as that actor, in that universe, with
capabilities that are a subset of the parent's. Execution authority MUST NOT be read
from the node spec, from a caller-supplied `child_actor`, or from a mutable run
record, and MUST NOT fall back to an anonymous identity.

#### Scenario: child runs as the parent's authenticated actor, not a spec actor
- **GIVEN** a branch node whose `invoke_branch_spec` names any `child_actor`
- **WHEN** the node invokes its child
- **THEN** the child run executes as the parent run's authenticated actor and in the
  parent run's universe, and the spec's `child_actor` has no effect (the field is
  rejected/ignored)

#### Scenario: missing authenticated context fails closed
- **WHEN** an invoke edge is reached without an authenticated execution context
- **THEN** the invoke is refused rather than defaulting to an anonymous or
  run-record-derived actor

#### Scenario: nested invoke cannot widen authority
- **GIVEN** a chain parent → child → grandchild of invoke edges
- **WHEN** each edge is taken
- **THEN** each descendant runs under the same actor + universe and a capability set
  no broader than its parent's, at every depth up to the recursion cap

### Requirement: A child branch reference is authorized by delegated, not ambient, authority

The system SHALL authorize an author-chosen child `branch_def_id` /
`branch_version_id` against the authoring definition's delegated authority, never the
running actor's ambient readability. A definition authored by the running actor in the
running universe MAY reference branches that author may read (own-private or public).
A foreign (public) definition MAY reference ONLY public branches or child references
explicitly pinned into the definition at authoring time. Any other reference SHALL be
refused with the uniform not-found envelope, before any raw definition load. This rule
SHALL apply transitively: a public child invoked from a foreign parent retains foreign
provenance for its own sub-edges.

#### Scenario: foreign branch cannot invoke the runner's private branch
- **GIVEN** a victim V runs a public branch authored by A, whose `invoke_branch_spec`
  names a `branch_def_id` that is one of V's PRIVATE branches
- **WHEN** the invoke edge is evaluated
- **THEN** it is refused (uniform not-found) — V's ambient readability does not
  authorize a reference chosen by A; the private branch is never loaded or run

#### Scenario: foreign branch may invoke a public child
- **GIVEN** a foreign public parent whose child reference is a public branch
- **WHEN** the invoke edge is evaluated
- **THEN** it is authorized and runs under V's execution context (actor + universe)

#### Scenario: own-universe branch may invoke its own private child
- **GIVEN** a parent authored by the running actor in the running universe referencing
  that actor's own private child branch
- **WHEN** the invoke edge is evaluated
- **THEN** it is authorized (the same-author path is unchanged)

#### Scenario: version reference is authorized before the snapshot loads
- **GIVEN** an `invoke_branch_version_spec` naming a `branch_version_id`
- **WHEN** the edge is evaluated
- **THEN** the version's definition is authorized under the delegated rule BEFORE the
  snapshot is loaded, and a version that does not belong to the authorized definition
  is refused

### Requirement: Cross-branch data mappings and awaits are confidentiality-scoped

For an invoke edge whose parent definition is foreign, `inputs_mapping` SHALL map only
declared, delegable parent fields — never secrets, credentials, internal metadata, or
authorization/control state — and `output_mapping` SHALL write only declared writable
data fields. A child run handle consumed by an await/poll step SHALL be verified to
belong to the parent run + actor + universe before its status or output is returned.

#### Scenario: foreign spec cannot map a parent secret into its child
- **GIVEN** a foreign parent whose `inputs_mapping` maps a secret/credential/auth-state
  parent key into the child
- **WHEN** the child inputs are assembled
- **THEN** the disallowed key is rejected and never passed to the child

#### Scenario: await binds a state-supplied run id to the caller
- **GIVEN** an await/poll step reading a run id from run state
- **WHEN** it resolves status/output
- **THEN** it returns only for a run bound to the parent run + actor + universe, and
  refuses a run id belonging to another actor/universe
