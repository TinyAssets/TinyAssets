## ADDED Requirements

### Requirement: REST transport over the canonical handles
The platform SHALL serve `https://tinyassets.io/api` for programmatic clients. Each route SHALL run exactly one canonical handle's function (`read_graph`, `run_graph` or `converse`) for one named command center, under the key owner's identity, adding no gate and removing none. The first release SHALL cover: list command centers, read one, read its status, list its runs, read one run, list its connections, start a branch run, cancel a run, and send the agent a message.

#### Scenario: Script polls a run
- **WHEN** a client holding a key with `run_graph` and `read_graph` on a command center starts a run and then reads it
- **THEN** the run is queued exactly as `run_graph` queues it and `GET .../runs/{run_id}` returns the same record `read_graph target=run` returns

### Requirement: Per-user revocable API keys
An owner SHALL mint, list and revoke API keys only through the protected owner session. A key's secret SHALL be shown once and stored only as a digest. Each key SHALL be an outside client holding exact `{universe, agent, capability}` grants; wildcard or unknown capabilities SHALL be refused.

#### Scenario: Key outside its grant
- **WHEN** a key granted `read_graph` on one command center calls another command center, or calls `run_graph`
- **THEN** the request is refused with 403 and no handle runs

#### Scenario: Revocation
- **WHEN** the owner revokes a key, or the outside switch is off
- **THEN** every request with that key is refused with 401, and its saved work cannot resume as outside work

### Requirement: Credential separation
`/api` SHALL accept API keys and no other credential. An API key SHALL NOT be resolved by the OAuth provider and SHALL NOT authenticate `/mcp` or `/app`.

#### Scenario: OAuth bearer on the REST door
- **WHEN** a request to `/api` carries an OAuth access token or no credential
- **THEN** it is refused with 401 and `WWW-Authenticate: Bearer realm="tinyassets-api"`
