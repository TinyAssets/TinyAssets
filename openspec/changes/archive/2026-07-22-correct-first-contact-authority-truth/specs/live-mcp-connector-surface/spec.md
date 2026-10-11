## MODIFIED Requirements

### Requirement: Read-Open, Write-Challenged Authentication Boundary

Pure-read handles (`read_graph`, `read_page`, and the `read_graph target=status` alias) SHALL remain callable anonymously. Pure-write / costly-effect handles (`write_graph`, `run_graph`, `write_page`, `converse`) SHALL answer an anonymous `tools/call` with an HTTP 401 + `WWW-Authenticate` challenge pre-dispatch so the MCP client launches OAuth, since a tool-JSON rejection would not prompt sign-in. `converse` is the connector's opening call: it requires an authenticated actor with write or admin access to the target universe and relays the actor's turn to the universe's own intelligence. For an authenticated founder with create scope and no home universe it resolves-or-creates and binds that home as a one-time onboarding side effect before continuing the originating conversation entry. Completion of that provisioning step SHALL NOT by itself assert that downstream provider execution or a first-person reply succeeded. `get_status` and the `read_graph target=status` alias are both pure reads (`readOnlyHint=True`) and never provision. Per the 2026-07-22 host directive (`2026-07-22-first-contact-birth-moves-to-converse.md (archived in git)`) birth moved off `get_status`, because a mutating *opening* call proved refusable in production — the assistant declined to call it, citing the side effect its own tool description advertised, which contradicted the shipped instruction to call it first.

#### Scenario: Anonymous write handle triggers an OAuth challenge

- **WHEN** an unauthenticated client calls `write_graph`, `run_graph`, `write_page`, or `converse`
- **THEN** the server answers HTTP 401 with a `WWW-Authenticate` header pre-dispatch, launching the OAuth sign-in flow

#### Scenario: Anonymous read handle stays open

- **WHEN** an unauthenticated client calls `read_graph` or `read_page`
- **THEN** the read is served without an auth challenge

#### Scenario: First-contact provisioning via converse

- **WHEN** an authenticated founder with create scope and no home universe issues their opening `converse` with no `graph_id`
- **THEN** a home universe is created and bound and the originating conversation entry continues with that home as its target
- **AND** completion of provisioning does not by itself assert that provider execution or a first-person reply succeeded
- **AND** a later `converse` for the same founder reaches the same home with no further creation

#### Scenario: Founder without create scope does not provision

- **WHEN** an authenticated founder without create scope and no home universe issues their opening `converse` with no `graph_id`
- **THEN** no universe or home binding is created
- **AND** the result reports that the home could not be created or loaded with `auth_scope_required=true`

#### Scenario: get_status never provisions

- **WHEN** an authenticated founder with no home universe calls `get_status`
- **THEN** no universe is created and the call is a pure read (`readOnlyHint=True`)

#### Scenario: The read alias never provisions

- **WHEN** any caller invokes `read_graph(target="status")`
- **THEN** no universe is created

#### Scenario: Caller without target write access is refused converse

- **WHEN** an anonymous caller or an authenticated caller without write or admin access reaches `converse` for a universe
- **THEN** the reply is an auth error and no message is relayed to the universe intelligence
