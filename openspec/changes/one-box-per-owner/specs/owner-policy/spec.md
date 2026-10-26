## ADDED Requirements

### Requirement: Owner policy lives outside the box in broker-private state
Each account SHALL have one owner policy, versioned by a monotonic
generation. It SHALL hold approval rules per action class, pre-approval
grants, the egress mode and allowlist, spending caps and the ask threshold,
per-source model-spend ceilings, and declared operation kinds. It SHALL be
stored in broker-private state that only the broker opens. It SHALL NOT be
stored in a box, in a command-center directory or in daemon-writable records.

#### Scenario: The box cannot reach the store
- **WHEN** any process in the owner's box searches for or opens the policy store
- **THEN** the store is not present in the box's filesystem or reachable through its channel
- **AND** a fixed platform floor sits beneath owner policy and the owner cannot loosen it

### Requirement: Only the owner writes policy, through the protected owner session
Policy SHALL be written only through the owner-door policy route, carrying
an interactive owner-session token bound to the account. Bearer, MCP and
`/api` clients SHALL NOT acquire that token. The broker SHALL look up the
opaque cookie's hash read-only in the existing owner-session store and verify
its interactive identity, account binding and expiry before it writes.

#### Scenario: Session authority moves into broker custody
- **WHEN** P1 serves policy writes
- **THEN** the daemon forwards the cookie on the intended owner route
- **AND** the session store remains daemon-writable, so a compromised daemon can mint a session
- **AND** P2/P3 SHALL move session minting and validation into broker custody

#### Scenario: Only the protected owner door offers writes
- **WHEN** a caller uses the model door, `/mcp`, `/api`, the box channel or any `ta` command
- **THEN** no policy-write operation is offered
- **AND** tightening and loosening require the same interactive owner-session token

#### Scenario: A prompt injection tries to change a rule
- **WHEN** content the agent reads instructs it to allow sending, edit `/policy/policy.json`, or call a policy write through `ta`
- **THEN** the projection write fails read-only, no `ta` write operation exists, and the policy generation is unchanged
- **AND** each attempt is recorded as a receipt

#### Scenario: A daemon path forwards a write without the token
- **WHEN** a request reaches the broker's policy write without a valid interactive owner-session token
- **THEN** the broker refuses it and the policy is unchanged

### Requirement: The agent can read policy and propose changes, never apply them
The box SHALL see a read-only projection of the current policy, with its
generation and digest. `ta policy show` SHALL return the same projection.
`ta policy propose` SHALL create an owner request showing the literal change.
The change SHALL take effect only when the owner approves it in the
protected owner session, and the owner door then performs the write.

#### Scenario: Rule suggestion awaits the owner
- **WHEN** the agent proposes a different approval rule through `ta policy propose`
- **THEN** a pending owner request shows the literal proposed policy and the effective generation stays unchanged
- **AND** only the protected owner session can apply that request
- **AND** `agent_review.py` proposes suggestions through the existing pending owner-request path and never writes `rules.db`
- **AND** every box process has only read-only access to the effective projection

### Requirement: One default policy per account
The account SHALL have exactly one broker-private policy under
`.broker/accounts/<sha>/policy.db`. The broker SHALL create its directories
with mode 0700 and its database with mode 0600. Every account SHALL start
with `DEFAULT_RULES`; the agent asks and the owner re-grants once.
Entries SHALL carry account or command-center
scope, and evaluation SHALL apply the strictest matching entry.

#### Scenario: Legacy files remain outside policy initialization
- **WHEN** the broker initializes an owner's policy
- **THEN** legacy `rules.db` files remain untouched until P6
- **AND** the broker never reads or traverses `.agent-sessions`

#### Scenario: One account cannot initialize its policy
- **WHEN** one owner's policy fails to initialize
- **THEN** the broker refuses that owner with an alert without stopping the broker or other owners

#### Scenario: The agent proposes a looser egress policy
- **WHEN** the agent proposes adding a host to the allowlist
- **THEN** an owner request appears and the allowlist is unchanged until the owner approves

### Requirement: The broker enforces policy on every request that leaves the box
For each request that crosses the box channel, the broker gate SHALL:

- classify it by action class, connection, operation, destination and
  amount;
- apply the platform floor;
- apply the owner policy at the current generation: allow, allow with a
  grant, hold and ask, or deny with the hand-off note;
- reserve spend atomically against the caps.

A spend above the threshold SHALL ask. A spend above a cap SHALL be denied,
and no approval SHALL exceed a cap.

#### Scenario: Internal edits do not require policy approval
- **WHEN** a process changes files inside its box
- **THEN** those changes are not policy-gated and snapshot restore can undo them

#### Scenario: Ask before send
- **WHEN** the owner's rule is "ask before people.message" and the agent sends an email through the owner's connected mail account
- **THEN** the send is held, the owner is asked, and nothing is sent until approval

#### Scenario: A spend cap
- **WHEN** a purchase would exceed the owner's monthly cap
- **THEN** the gate denies it, even if an approval is supplied

#### Scenario: Unpriced model request under a cap
- **WHEN** a model request has a configured spend cap but no trusted pre-dispatch amount
- **THEN** the broker holds the request and receipts the missing amount

#### Scenario: Policy changed after approval
- **WHEN** the owner tightens a rule after a once approval was issued but before dispatch
- **THEN** the stale approval cannot execute the action
- **AND** a once approval re-checks the current generation before dispatch

#### Scenario: An unrelated edit preserves a standing grant
- **WHEN** the owner adds another scoped policy entry while a site grant remains in the policy
- **THEN** the site grant can still authorize its exact operation under the current rules
- **AND** removing that entry or tightening its matching rule stops it

### Requirement: Policy writes and decisions leave receipts
Every policy write SHALL leave a receipt in the broker's receipts ledger,
carrying the old and new digest, the owner and the session-token id. Every
proposal and every enforcement decision SHALL also leave a receipt, carrying
the request digest, policy generation, rule, decision and outcome. The
owner's activity view SHALL list these receipts.
Account deletion SHALL erase the broker-private policy and its receipts and
prevent a stale owner session from recreating that store.

#### Scenario: Audit after a blocked exfiltration
- **WHEN** a prompt-injected request to an unlisted host is denied
- **THEN** the owner's activity view shows the denied request with the rule that denied it

### Requirement: Email goes through the owner's connected account
The agent SHALL send email only as a connector call on a mail account the
owner connected, classed `people.message`. SMTP ports SHALL stay refused at
the egress floor. The platform SHALL hold no sending mail domain.

#### Scenario: Direct SMTP attempt
- **WHEN** a process in the box connects to an SMTP port
- **THEN** the egress floor refuses it and a receipt records the refusal
