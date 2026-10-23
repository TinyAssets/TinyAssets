## ADDED Requirements

### Requirement: Realtime exchanges use the one owner session and model call
The system SHALL admit two-way text, audio and video through the existing owner-box session entry. One duplex session SHALL own one parent allocation; each model turn SHALL be one streaming `model_call` with exactly one receipt under that parent. S3 SHALL control committed turns, tools and cancellation; interruption SHALL cancel the current turn only. No browser, shared daemon or bridge SHALL execute a separate agent turn loop.

#### Scenario: Voice interrupts an active reply
- **WHEN** the owner speaks while the agent is responding
- **THEN** S3 cancels or truncates that call, settles its receipt, and admits the next committed input through the same session and model-call path

#### Scenario: Structural one-loop proof
- **WHEN** `call-sites` and `opt-in-flags` inspect realtime routes and callers
- **THEN** every model exchange reaches `model_call` and S3, with no auxiliary Voice writer, `converse` callback, browser turn queue or provider-specific branch

### Requirement: Realtime source authority and transport stay at the broker
The system SHALL select the strongest connected source under the existing model policy and use its declared or discovered realtime contract for media and transport. If that source lacks the requested capability, admission SHALL report the gap visibly rather than select a weaker source. The owner box SHALL send and receive provider media only through a bound, bidirectional broker stream. The broker SHALL authenticate the box and per-call grant, keep credentials in its custody, apply the generation fence and bound both directions with credit, cancellation and deadlines. The router SHALL authenticate and forward the client's session without inspecting owner media.

#### Scenario: A source lacks duplex video
- **WHEN** a client requests video and the selected connected source declares no compatible video input
- **THEN** admission fails with a typed capability result before capture or provider dial

#### Scenario: A weaker source supports realtime
- **WHEN** the strongest connected source lacks realtime but a weaker one supports it
- **THEN** the app reports the strongest source's gap and does not switch sources without the owner's explicit selection

#### Scenario: Revocation during a media stream
- **WHEN** the owner's grant or admission is withdrawn during a realtime exchange
- **THEN** the broker stops upstream traffic and the box settles the call without exposing a credential or replaying input

### Requirement: Realtime capture and history are owner scoped
The client SHALL require explicit foreground capture and disclosure, expose accessible listening and speaking states, and stop tracks on leave or failure. The owner's box SHALL keep the committed speech transcript, output parts and receipts. It SHALL NOT retain input audio or partial playback frames by default. Under N9, complete media output parts SHALL be stored before owner-scoped references are published; raw capture and partial frames SHALL remain ephemeral.

#### Scenario: A session disconnects after input was sent
- **WHEN** transport drops after a committed input may have reached the source
- **THEN** the session reports the known or unknown outcome, preserves any committed owner-box history, and does not silently resend input

### Requirement: Realtime spending is bounded throughout a session
Before upstream traffic, the broker SHALL atomically reserve a bounded time-and-usage slice against the parent allocation and current cap. It SHALL meter input and output media within each model turn and renew the slice before expiry. On insufficient cap or failed renewal it SHALL gracefully stop upstream traffic, report budget exhaustion, and settle the turn's one receipt for actual measured usage or an unknown outcome pending reconciliation. The session SHALL admit no further turn without budget authority. Free-only defaults SHALL remain unchanged.

#### Scenario: Cap is reached during speech
- **WHEN** a duplex turn consumes its reserved slice and the remaining cap cannot fund renewal
- **THEN** the broker stops upstream media, the owner receives a budget-exhausted result, and the turn's one receipt records its measured usage or an unknown amount pending reconciliation
