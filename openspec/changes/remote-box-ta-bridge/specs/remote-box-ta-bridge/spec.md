## ADDED Requirements

### Requirement: Bound remote capability authority
Remote ta SHALL use the trusted turn's owner and command center and SHALL refuse
foreign handles and requests after that turn closes.

#### Scenario: Owner call and foreign attempts
- **WHEN** a box sends a capability request
- **THEN** only its bound owner, center and live turn authorize dispatch

### Requirement: Credential custody and parity
Remote ta SHALL use the local ta capability dispatcher without exposing its
credentials in the box environment, process namespace or files.

#### Scenario: Real box inspection
- **WHEN** bash invokes ta and inspects env, proc and files
- **THEN** the capability succeeds and host credentials remain inaccessible

### Requirement: At most one dispatch per request
The bridge SHALL persist intent before dispatch and retain completed or unknown
outcomes across lost replies, retries and cancellation.

#### Scenario: Lost reply or cancellation
- **WHEN** a request is repeated after an interrupted response or cancelled call
- **THEN** no duplicate effect is dispatched and uncertain outcomes remain unknown
