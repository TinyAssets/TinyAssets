## ADDED Requirements

### Requirement: Realtime exchanges use the one owner session and model call
The system SHALL admit two-way text, audio and video through the existing owner-box session entry and streaming `model_call`, with one S3 loop controlling committed turns, tools, cancellation and receipts. No browser, shared daemon or bridge SHALL execute a separate agent turn loop.

#### Scenario: Voice interrupts an active reply
- **WHEN** the owner speaks while the agent is responding
- **THEN** S3 cancels or truncates that call, settles its receipt, and admits the next committed input through the same session and model-call path

#### Scenario: Structural one-loop proof
- **WHEN** `call-sites` and `opt-in-flags` inspect realtime routes and callers
- **THEN** every model exchange reaches `model_call` and S3, with no auxiliary Voice writer, `converse` callback, browser turn queue or provider-specific branch

### Requirement: Realtime source authority and transport stay at the broker
The system SHALL use a source's declared or discovered realtime contract to select supported media and transport. The owner box SHALL send and receive provider media only through a bound, bidirectional broker stream. The broker SHALL authenticate the box and per-call grant, keep credentials in its custody, apply the generation fence and bound both directions with credit, cancellation and deadlines. The router SHALL authenticate and forward the client's session without inspecting owner media.

#### Scenario: A source lacks duplex video
- **WHEN** a client requests video and the selected connected source declares no compatible video input
- **THEN** admission fails with a typed capability result before capture or provider dial

#### Scenario: Revocation during a media stream
- **WHEN** the owner's grant or admission is withdrawn during a realtime exchange
- **THEN** the broker stops upstream traffic and the box settles the call without exposing a credential or replaying input

### Requirement: Realtime capture and history are owner scoped
The client SHALL require explicit foreground capture and disclosure, expose accessible listening and speaking states, and stop tracks on leave or failure. Committed inputs, outputs and receipts SHALL be stored only in the owner's box; raw capture and partial frames SHALL remain ephemeral unless the owner explicitly saves them.

#### Scenario: A session disconnects after input was sent
- **WHEN** transport drops after a committed input may have reached the source
- **THEN** the session reports the known or unknown outcome, preserves any committed owner-box history, and does not silently resend input
