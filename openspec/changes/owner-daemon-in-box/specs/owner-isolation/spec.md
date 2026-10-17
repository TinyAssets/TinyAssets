## ADDED Requirements

### Requirement: Owner work runs in the owner's box

Everything that reads or writes an owner's data SHALL run in that owner's
box, as the owner's daemon process: the agent loop, history, schedules,
pause, owner stores, and the app and MCP handlers for that owner. The shared
process SHALL NOT hold a path to any owner's data.

#### Scenario: The shared router reads no owner data
- **WHEN** an authenticated request for owner A arrives
- **THEN** the router forwards it to A's box socket, with A's per-box secret
- **AND** the router opens no owner file or owner database

#### Scenario: Another owner's box is unreachable
- **WHEN** owner B's authenticated request names owner A's center
- **THEN** it is forwarded only to B's box, which has no such center
- **AND** B receives not-found

### Requirement: Owner egress uses the admitted broker channel

The owner daemon SHALL send model calls, streaming replies and authenticated
external calls through the per-owner relay to the broker. The broker SHALL
derive identity from channel admission, resolve credentials and enforce its
existing model accounting, policy and connection grants. The box SHALL hold
no provider, OAuth or Stripe credential and SHALL refuse direct non-loopback
outbound connections at its socket seam.

#### Scenario: A model turn streams through the broker
- **WHEN** an owner chats using a connected HTTP model source
- **THEN** the model executor uses the existing broker OPEN stream and usage gate
- **AND** the broker injects credentials outside the box
- **AND** loss of the allocation's channel closes its broker-held liveness lease

#### Scenario: Model registration permanently latches metering
- **WHEN** the broker accepts a source descriptor naming an HTTP grant, or installs a model capability on its connection
- **THEN** it latches the grant's inference requirement in the same operation
- **AND** later removal or disabling of metadata cannot permit a raw model POST
- **AND** platform registration publishes to the broker before succeeding

#### Scenario: Existing sources adopt metering without classifying ordinary HTTP
- **WHEN** the broker upgrades existing grants
- **THEN** capability rows, accepted descriptors and non-owner platform definitions latch their referenced grants
- **AND** an unknown connection retains its existing fail-closed metering default
- **AND** an unregistered grant without model capabilities remains ordinary regardless of its destination name

#### Scenario: Missing channel and direct egress fail
- **WHEN** a model call has no broker channel, or an SDK or raw HTTP call attempts direct outbound access
- **THEN** it fails without a direct provider or credential-store fallback
- **AND** `api_key_http` is the only model access method; provider CLI executors and their connection actions are absent
- **AND** users reconnect through the broker HTTP connection flow at cutover

#### Scenario: An authenticated effector uses its grant
- **WHEN** the owner invokes an authenticated external call
- **THEN** its policy decision, connection grant and HTTP dispatch use the admitted box channel
- **AND** revoking the grant prevents dispatch

### Requirement: Shared reads contain no box-local shared authority

The daemon SHALL read its plan and published commons through named broker
operations. The broker SHALL select plan state using the relay-bound owner,
and SHALL read published data independently of owner-private roots.

#### Scenario: Plan and catalog reads
- **WHEN** the daemon requests its plan or a published definition
- **THEN** BOX_SHARED returns the shared billing or published catalog result
- **AND** no shared database or Stripe credential is placed in the box

#### Scenario: Explicit account admission and missing plan
- **WHEN** the broker admits an owner's first center
- **THEN** its explicit free row and center admission commit in one transaction
- **AND** an existing paid row is preserved
- **AND** a later missing row raises PlanMissing through BOX_SHARED rather than becoming free

#### Scenario: Billing at the shared door
- **WHEN** an authenticated owner requests billing status, checkout or cancellation
- **THEN** the router uses fenced BILLING_PLAN operations keyed by that owner
- **AND** the broker keeps tier, end date, processor mapping and checkout attempt in .broker/plans.db
- **AND** neither home resolution nor an owner store is read at the router
- **AND** status includes plan limits and omits private seat/storage usage

#### Scenario: Verified billing event reaches the box
- **WHEN** a signed, mode-matching subscription event arrives at the router
- **THEN** its service claim and stored customer/subscription mapping select the owner
- **AND** ordered tier application preserves de-escalation on equal timestamps
- **AND** checkout settlement matches its stored session/attempt rather than a client reference hint
- **AND** the owner's next BOX_SHARED plan read observes the shared row

#### Scenario: A box publishes to the shared commons
- **WHEN** the box submits a definition through BOX_PUBLISH
- **THEN** the broker validates and writes it in .published with the bound owner as author
- **AND** a request cannot select another author or append to another owner's bundle
- **AND** public-run and catalog reads use only the published copy
- **AND** neither the box nor the public router writes a shared preview cache

### Requirement: Boxes sleep and wake without a person

A box SHALL sleep when idle, and SHALL wake on an inbound request or at its
reported next-wake time.

#### Scenario: A scheduled run in a sleeping box
- **WHEN** a sleeping box's next-wake time arrives
- **THEN** the waker wakes it and the owner's tick runs the due work


### Requirement: Credential custody and refresh have one broker writer

Only the broker SHALL persist, rotate or erase connection credential material.
The router SHALL hand key deposits or authorization codes and PKCE verifiers
to HTTP_CONNECT over its private broker channel. Token-endpoint requests SHALL
run only in the broker, using broker-owned registered-client configuration.
The broker SHALL own its custody locks, ownership metadata and deletion tombstones independently of daemon and owner stores.

#### Scenario: The door deposits a credential
- **WHEN** the door deposits a key or the broker exchanges an authorization code
- **THEN** the broker rechecks the revision before writing custody and committing its grant
- **AND** a refused deposit leaves no new grant
- **AND** the router performs no vault write

#### Scenario: An OAuth token expires during a box call
- **WHEN** an admitted model or effector call needs a refreshed OAuth token
- **THEN** the broker refreshes and durably saves the replacement before using it
- **AND** registered-client credentials are resolved inside the broker
- **AND** REFRESH and REFRESH_ACK are unknown box-channel operations

#### Scenario: Disconnect erases custody
- **WHEN** DISCONNECT erases a fenced connection with the expected incarnation
- **THEN** the broker calls the directory's RFC 7009 endpoint under the custody lock, using the current refresh token or otherwise the access token and the registered client authentication
- **AND** it erases custody and grants even when provider revocation fails
- **AND** a provider failure leaves a credential-free, revoked connection row with `revocation_error` and `custody_erased`; a fresh connect may replace that receipt
- **AND** a failed erase leaves the connection fenced and retryable

#### Scenario: Connection controls need no owner store at the router
- **WHEN** the authenticated owner opens `/app/connections` or disconnects a listed connection
- **THEN** an explicit center must name that owner in broker admission, or the broker must find exactly one live center for that owner; otherwise the door returns 404
- **AND** the router uses CONNECTION_CATALOG and DISCONNECT without reading a home, owner database, pending request or provider assignment
- **AND** the catalog can include fenced connections whose local custody cleanup needs retry

#### Scenario: Model connection flows need no owner store at the door
- **WHEN** an authenticated owner starts preset PKCE, inline popup, generic OAuth, source sign-in or a key deposit at `/app/model-connect/*`
- **THEN** the door selects the center only through broker admission, using SOLE_CENTER when omitted and returning 404 for a foreign, missing or ambiguous center
- **AND** source and connection definitions come from data or the broker catalog; request_ref is opaque and no home, pending request, universe file or owner database is read
- **AND** the router stores only bounded flow state and public definition metadata, with inline verifier and callback code sealed

#### Scenario: Broker exchanges and commits a grant
- **WHEN** the door consumes an OAuth or preset PKCE flow
- **THEN** it sends code, verifier, redirect_uri and request_ref to the broker
- **AND** the broker prepares the policy, exchanges at the selected endpoint, writes custody and commits the grant with request_ref on its credential-free connection row
- **AND** registered client authentication is resolved only in the broker
- **AND** the consumed router flow cannot be replayed and the broker response contains no credential
- **AND** box channels cannot invoke these exchanges

#### Scenario: Box connect actions carry no secret
- **WHEN** a box invokes a connection action or answers a connect approval
- **THEN** its accepted fields contain no secret, token, key, password or capability URL value
- **AND** a keyless connection uses HTTP_CONNECT on the bound box channel
- **AND** a secret or sign-in connection returns a door link carrying request, center and source or connection ID
- **AND** browser credential submissions go directly to the shared door

#### Scenario: A grant completes the requesting box's pending connection
- **WHEN** the box sees a live GRANTED_RESOURCE with the pending connection's request_ref and matching policy
- **THEN** it applies the requested uses and resolves the pending request through the existing answer and continuation path
- **AND** a different request_ref leaves it pending and repeated polling does not deliver a second answer
- **AND** a completion-storage or model-acceptance failure leaves the request pending with an explicit failure
- **AND** completion needs neither push delivery nor a router read of a pending request

#### Scenario: Every supported authentication type completes its lifecycle
- **WHEN** none, bearer, basic, header, oauth1a, url_secret, DCR, registered-client or preset authentication is connected
- **THEN** a model or authenticated effector call from a separate box process succeeds through the broker
- **AND** disconnect through the shared door or box DISCONNECT makes the next box call fail
