## ADDED Requirements

### Requirement: Address-based model connection
The connect dialog SHALL offer Connect any AI first, accepting a domain or HTTPS URL and discovering OAuth and a model catalogue on the server.

#### Scenario: Unfamiliar OAuth model source
- **WHEN** an owner supplies an unfamiliar provider with advertised OAuth and an OpenAI-compatible model API
- **THEN** PKCE authorization and broker exchange create the existing HTTP source shape, with model use subject to owner confirmation

### Requirement: Honest discovery fallback
Discovery SHALL offer an API key with discovered endpoints when OAuth is absent, and SHALL report no model API when none is evidenced.

#### Scenario: Missing metadata
- **WHEN** the address offers neither metadata nor a model catalogue
- **THEN** no endpoint or model is invented

### Requirement: Frozen exact destinations
The flow SHALL use HTTPS and exact discovered hosts and paths, refuse redirects, cache DCR by issuer and callback, and keep credentials in broker custody.

#### Scenario: Host changes during authorization
- **WHEN** provider metadata changes its token host after authorization begins
- **THEN** the flow cannot send the code to the new host

#### Scenario: Insecure address
- **WHEN** a submitted address uses HTTP
- **THEN** discovery refuses it before network access
