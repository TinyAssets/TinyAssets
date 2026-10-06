# Remote box ta bridge

## Purpose

Turn-bound, credential-free capability RPC over the BoxProvider execution protocol.

## Requirements

### Requirement: Bound remote capability authority
Remote ta SHALL use the trusted turn's owner and command center and SHALL refuse
foreign handles and requests after that turn closes. Execution stdin replies
SHALL require the exact owner, center, epoch and turn of the running execution.

#### Scenario: Owner call and foreign attempts
- **WHEN** a box sends a capability request or receives a reply
- **THEN** only its bound owner, center and live turn authorize that operation

### Requirement: Credential custody and parity
Remote ta SHALL use the same capability dispatcher and signed grant as local ta,
without exposing credentials in the box environment, process namespace or files.
RPC replies SHALL remain outside durable command-center files.

#### Scenario: Real box inspection
- **WHEN** bash invokes ta and inspects env, proc and files
- **THEN** its capability succeeds, host credentials stay inaccessible and no RPC reply files remain

### Requirement: At most one dispatch per request identity
The bridge SHALL persist intent before dispatch and retain completed or unknown
outcomes across lost replies, same-ID retries and cancellation. A client reconnect
SHALL retain its request identity. A command rerun with a new identity is new intent.

#### Scenario: Lost reply or cancellation
- **WHEN** a request identity repeats after an interrupted reply or cancelled call
- **THEN** no duplicate effect is dispatched and uncertain outcomes remain unknown

### Requirement: Bounded protocol and explicit startup failure
The bridge SHALL transport existing ta request and response bounds independently
of shell argument limits and decoded bash output limits. Missing Python worker
support SHALL fail explicitly without falling back to another execution surface.

#### Scenario: Large values and unavailable worker
- **WHEN** a request exceeds 128 KiB or a response exceeds 64 KiB
- **THEN** the RPC transport carries it within ta's existing bounds
- **WHEN** the box cannot start the worker
- **THEN** remote_ta_worker_unavailable is reported and the command does not run
