## ADDED Requirements

### Requirement: Grant endpoint refusals explain the caller request
A connection request outside its permitted endpoint SHALL return error_kind grant_path_refused and name the caller-supplied method and path, without exposing other permitted endpoints, broker credentials or redirect destinations.

#### Scenario: Disallowed connection path
- **WHEN** a caller requests GET /private outside its grant
- **THEN** the broker refuses before dispatch and the caller receives a grant_path_refused error naming GET /private
