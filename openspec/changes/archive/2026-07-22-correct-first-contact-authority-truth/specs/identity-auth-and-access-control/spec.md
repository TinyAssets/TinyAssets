## MODIFIED Requirements

### Requirement: Founder home auto-births exactly once on first authenticated contact

The server SHALL, on the first authenticated `converse` call with no `graph_id` (the founder's opening
relay, and the only handle that performs first-contact birth), ensure the founder has a home universe.
It SHALL check the create scope BEFORE reserving any home id, so a founder lacking create scope leaves
no phantom binding and the conversation entry returns a creation failure with
`auth_scope_required=true`. Reservation SHALL be atomic (an `INSERT ... ON
CONFLICT DO NOTHING` on the founder key) so concurrent first-contact calls across worker threads yield
exactly one home id, and materialization SHALL be serialized so a reserved id is created once, with
success defined as the universe's `soul.md` being present. After successful materialization and
binding, the resolver SHALL return the bound home id to the originating `converse` entry path.
Whether that conversation can select and invoke universe intelligence is a subsequent
authority/execution decision outside this birth contract; successful birth SHALL NOT guarantee
provider execution or a first-person reply. Anonymous sessions SHALL never trigger birth. Both `get_status` and the
`read_graph target=status` alias SHALL pass through as pure reads without first-contact birth. This
logic lives in `tinyassets/api/first_contact.py` with the atomic claim in
`tinyassets/daemon_server.py`.

Per the 2026-07-22 host directive
(`2026-07-22-first-contact-birth-moves-to-converse.md (archived in git)`), birth moved off
`get_status` and its `allow_first_contact_birth` parameter was deleted, because a mutating *opening*
call proved refusable in production: the assistant declined to call `get_status` on the grounds that
its own tool description advertised a side effect. The 2026-07-15 commitment this replaces — a founder
never needs to know an incantation — is upheld, since the opening message is itself the relay.

#### Scenario: first authenticated converse births one home
- **WHEN** an authenticated founder with create scope and no bound home issues their opening `converse` with no `graph_id`
- **THEN** exactly one home universe is reserved, materialized, and bound to the founder
- **AND** the originating conversation entry continues with that bound home as its target
- **AND** completion of birth does not by itself assert that provider execution or a first-person reply succeeded

#### Scenario: read-only founder leaves no phantom binding
- **WHEN** an authenticated founder lacking create scope issues their opening `converse` with no home
- **THEN** no home binding is created
- **AND** the result reports that the home could not be created or loaded with `auth_scope_required=true`

#### Scenario: anonymous first contact never births
- **WHEN** an anonymous session calls `converse` or `get_status`
- **THEN** no home universe is created

#### Scenario: get_status never births
- **WHEN** an authenticated founder with create scope and no bound home calls `get_status`
- **THEN** no home universe is created and the call is a pure read
- **AND** a repeated call returns the identical snapshot
