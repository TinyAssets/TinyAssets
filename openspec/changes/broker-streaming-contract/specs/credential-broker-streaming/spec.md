## ADDED Requirements

### Requirement: The broker authorizes every stream as it opens
The credential broker SHALL authorize each stream when it opens, with the same
checks `resolve_exact_scoped_proxy` applies today: the authenticated
principal, an active grant, the grant's owner and command center, the
connection's identity and owner, and revocation. It SHALL identify the
caller's role from the connecting process's authenticated identity against its
own configuration, refuse unmapped callers, and refuse frames the role may not
send. On a box channel the principal SHALL be derived from the box identity
that `boxhostd` authenticated, and any principal the request names SHALL be
refused. A stream refused here SHALL end `refused` without sending anything,
and SHALL report the operation's `side_effect_state` as the transmission
requirement defines it, never `none` for an operation it cannot establish
sent nothing.

#### Scenario: admission is acknowledged before anything is sent
- **WHEN** a stream passes authorization and the fence
- **THEN** the broker durably records its operation as possibly sent and
  acknowledges admission to the caller before writing the first byte upstream

#### Scenario: a revoked grant refuses the next stream
- **WHEN** a grant is revoked while its owner's earlier stream is still open
- **THEN** the next stream opened on that grant is refused before any network
  activity

#### Scenario: a box cannot name another principal
- **WHEN** a request relayed for a box names a principal or a connection its
  command center was not granted
- **THEN** the broker refuses it without network activity

### Requirement: Responses stream with backpressure and cancellation
The broker SHALL send a stream's status, reason and sanitized headers only
after the redirect chain and the OAuth refresh-once are settled, and SHALL
then forward the response body as it arrives, never more than the caller's
outstanding credit. Without credit it SHALL keep parsing framing but buffer
no more than a fixed look-ahead of body payload. After the
status is sent the broker SHALL NOT send the request again. Request bodies in
this version SHALL be collected within today's bound before sending. A
caller's cancel SHALL abort the upstream request and end the stream. An
absolute deadline SHALL run from admission through every phase; an idle bound
SHALL count bytes read from upstream and SHALL pause while reading is stopped
for lack of credit.

#### Scenario: the first token arrives before the last
- **WHEN** a model source streams its reply over several seconds
- **THEN** the caller receives the first body bytes before the upstream
  response has finished

#### Scenario: a slow consumer does not grow broker memory or look idle
- **WHEN** a caller stops granting credit while the provider is still sending
- **THEN** the broker buffers no more than its window, the look-ahead and the
  scan hold-back for that stream, and does not end it as idle

### Requirement: No byte of a held sensitive value is forwarded
The broker SHALL scan, for each stream, every sensitive value the
request/close path scans today, including values it derives for the request
and material accumulated across redirects, entirely inside the broker. It SHALL
scan intermediate redirect responses and targets before following them, every
destination-controlled field of the status and headers (reason phrase, header
names and values) before emitting them, and the body incrementally as raw
bytes and as its UTF-8 decoding with replacement, withholding the trailing bytes that could
still begin an occurrence until more bytes arrive or the stream ends cleanly.
On a match it SHALL end the stream as an unsafe destination response, forward
none of the withheld bytes, and record an audit entry. No forwarded byte SHALL
belong to a complete occurrence of a held value, raw or decoded.

#### Scenario: a value split across reads is caught
- **WHEN** a destination echoes a held credential split across two upstream
  reads
- **THEN** no byte of the credential reaches the caller and the stream ends
  failed

#### Scenario: a derived authorization value is still held
- **WHEN** a destination echoes only the encoded Basic authorization payload
- **THEN** the stream ends failed exactly as the request/close path refuses it

### Requirement: Transmission uncertainty is reported, never guessed
A stream SHALL report the `side_effect_state` of the operation its `op_id`
names: `none` only when the broker proves no request byte of that operation
ever left it, and `unknown` from the operation's first write onward, through
the OAuth resend, redirects, cancellation and any later refusal, including a
refused duplicate open. Whether this stream itself wrote SHALL be reported
separately. The broker SHALL NOT infer `none` from a response status.

#### Scenario: a cancelled stream after sending is unknown
- **WHEN** a caller cancels a stream whose request was already written
- **THEN** the stream ends `cancelled` with `side_effect_state` `unknown`

### Requirement: An operation is recorded durably and never sent twice
The broker SHALL record each stream's operation by authority namespace and
`op_id`, bound to the canonical effective request (including headers after
merging and the authentication-header selection), SHALL persist that a request may
have been sent before its first byte is written, and SHALL NOT send a recorded
operation again; a concurrent or later open of the same `op_id` SHALL return
the recorded state, and one with a different request identity SHALL be refused.
A status query SHALL distinguish not sent, unknown, the terminal states and
expired, and SHALL find nothing outside the caller's namespace. The broker
SHALL keep a durable nondecreasing expiry cutoff, refuse `op_id`s below it or
too far in the future, delete a record only once its `op_id` is below the
cutoff, and refuse new admissions rather than evict an admissible record.

#### Scenario: a crash after sending reads as unknown
- **WHEN** the broker crashes after writing a request and restarts
- **THEN** a status query for its `op_id` reports unknown and an open reusing
  the `op_id` does not reach the destination

### Requirement: Streams below the owner fence are refused and stopped
A fence barrier SHALL be admitted only with proof of holding the owner lease
at that generation, read from the lease authority by the broker, SHALL be
refused below the persisted fence, and SHALL be idempotent at the persisted
generation. Every owner-channel stream SHALL carry the generation and the
token the broker's last admitted barrier issued, and relayed box streams SHALL carry the
pair `boxhostd` received from its own barrier. The broker SHALL admit a stream
only if both equal its persisted fence, and SHALL re-check them immediately
before every write to the network. On a barrier for generation G it SHALL
durably persist G with a new token, stop every
older stream, and acknowledge only once no older stream can write again. It
SHALL enforce the persisted fence after a restart whether or not the
acknowledgement was delivered.

#### Scenario: a stale owner can neither open nor re-fence
- **WHEN** an owner of an older generation opens a stream after a newer
  barrier, whatever generation number it states, or sends a barrier for a
  higher generation without that generation's lease proof
- **THEN** it is refused as fenced with no network activity

### Requirement: Request/close callers keep their contract
`ScopedConnectionProxy.request` SHALL keep its signature, return document,
redirect fields, typed errors and closed-proxy refusal, implemented as one
stream collected to its end; the broker, not the wrapper, SHALL perform the
scan. No per-request or per-proxy process SHALL be spawned.

#### Scenario: an effector call is unchanged
- **WHEN** an effector calls `proxy.request("POST", {...})`
- **THEN** it receives the same document and the same typed errors as before
  this change

### Requirement: The in-box endpoint never reports a failed stream as success
The box's base-URL endpoint SHALL select the grant from the box's command
center and the connection named in its path, SHALL send only to that
connection's declared host and allowed endpoints, SHALL map a failure before
the status to an HTTP error status naming only the error class, SHALL remove
upstream framing headers before applying its own, SHALL relay a bodyless
response only after its stream completes, and SHALL end a response that does not complete
after its status by aborting the connection without a successful terminator.

#### Scenario: a scan hit after the status reaches the CLI as truncation
- **WHEN** the broker ends a stream failed after the CLI already received a
  200 status and part of the body
- **THEN** the CLI's connection is aborted without the terminating chunk
