## ADDED Requirements

### Requirement: Saved connectors bind tested revisions and scoped credentials
Agent-authored connectors SHALL be saved as exact-revision tested extensions with declared credential slots and local connection bindings, listed and revocable through existing connection controls. Updates SHALL require a new test receipt and applicable exact-revision activation or owner author/ceiling auto-update permission; a test alone SHALL NOT grant authority. Exports SHALL omit secrets, private responses and live grants. Non-destructive self-tests SHALL be the default; effectful tests SHALL use current owner approval policy.

#### Scenario: Agent builds a connector from OpenAPI
- **WHEN** the owner's agent writes an extension from API docs/OpenAPI and a safe self-test succeeds
- **THEN** the tested revision and receipt are saved as a reusable connection extension with revocation controls
- **AND** an update requires a new test receipt; failed tests remain visibly failed

#### Scenario: Extension tries another credential or leaks its own
- **WHEN** an extension requests another connection's slot or the response contains injected credential bytes
- **THEN** the broker denies the foreign slot or withholds the secret-bearing result and reports failure
- **AND** credentials remain absent from sandbox files/environment, transcripts, logs and published packages
