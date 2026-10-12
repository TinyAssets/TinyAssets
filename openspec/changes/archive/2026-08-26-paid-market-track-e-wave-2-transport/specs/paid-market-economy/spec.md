## MODIFIED Requirements

### Requirement: Money actions operate only on the authenticated actor
Value-moving actions (fund, set-wallet, withdraw, lock, claim, settle, refund, release, slash) SHALL act only under an authenticated subject and tenant/universe derived from verified request authority, never from caller-supplied identity/tenant fields or `UNIVERSE_SERVER_USER` / `UNIVERSE_SERVER_HOST_USER` environment fallback. Every business, offer, claim, account, escrow, collateral, idempotency, posting, and receipt lookup SHALL use composite tenant keys, and mixed-tenant commands or posting sets SHALL fail before mutation. A caller-supplied `staker_id` SHALL be honored only when it equals the authenticated actor, or when an authenticated configured host presents an immutable signed on-behalf grant that binds grant id, host, target tenant/actor/account, allowed action set, maximum amount, issue/expiry, and revocation generation. The transport SHALL derive buyer, seller, escrow, and collateral accounts from tenant-scoped locked business rows and SHALL accept the treasury account only from fixed server configuration. Wave 2 SHALL reject caller-supplied treasury accounts and all `external:*` or `pool:*` accounts; external funding requires a separately reviewed receipt-verified ingress. Release/refund SHALL also be authorized against the lock's persisted tenant and owner, so a write-scoped caller cannot fund, withdraw, redirect, claim, or cancel another actor's money by id.

#### Scenario: cross-actor escrow attempt is rejected
- **WHEN** an authenticated actor supplies a `staker_id` or account owner that is neither themselves nor an explicitly authorized on-behalf target
- **THEN** the action returns a rejected status stating money actions operate only within the actor's authority
- **AND** no funds, wallet address, claim, posting, or withdrawal is recorded

#### Scenario: environment identity grants no money authority
- **WHEN** an unauthenticated request runs while `UNIVERSE_SERVER_USER` or `UNIVERSE_SERVER_HOST_USER` names a privileged actor
- **THEN** every value-moving action is rejected
- **AND** the environment value is not used as the ledger actor

#### Scenario: authenticated host acts only under an explicit grant
- **WHEN** the authenticated actor is the configured host and presents a current signed on-behalf grant whose target, action, account, amount, tenant, and time bounds cover the request
- **THEN** the action proceeds against that target's escrow
- **AND** the audit record identifies the grant id, host actor, target actor, target tenant, action, and amount

#### Scenario: caller-selected or mixed tenant is rejected
- **WHEN** a command names a tenant different from verified request authority or any locked row/account belongs to another tenant
- **THEN** the trusted wrapper rejects the command before a business or ledger mutation
- **AND** the privileged service role does not act as a cross-tenant deputy

#### Scenario: revoked or overbroad host grant is rejected
- **WHEN** an on-behalf grant is expired, revoked, exceeds its amount, or omits the requested action/account/target
- **THEN** the action is rejected before any lock or posting
- **AND** no environment or host identity broadens the grant

### Requirement: Paid-market computation library is pure and I/O-free
The `tinyassets/paid_market/` package (spot index, buckets, forwards, ceiling, training, pools, fund, licenses, shuttles, fabrication, matching, ledger) SHALL contain no I/O: it reads no files, opens no database, and reads no environment. Transport code SHALL sit outside the package, call its matcher and named posting adapters, and persist their outputs without recomputing settlement math. Every money-path computation SHALL be integer or `Fraction` exact with conservation invariants asserted internally, so a rounding residue can never silently create or destroy value. Persisted results SHALL be differential-checked against the pure `Ledger` oracle in tests and SHALL fail loud on divergence.

#### Scenario: transport consumes a pure adapter without adding I/O
- **WHEN** a transport settlement is computed and persisted
- **THEN** the named `tinyassets.paid_market.ledger` adapter produces the posting list
- **AND** no file, database, environment, or network access occurs within `tinyassets.paid_market`

#### Scenario: library modules perform no I/O
- **WHEN** the `tinyassets.paid_market` modules are imported and exercised
- **THEN** they read no file, database, environment variable, or network resource

#### Scenario: exact-arithmetic conservation holds
- **WHEN** a paid-market computation apportions or splits an amount
- **THEN** the parts sum exactly to the input with no float residue

#### Scenario: matching uses the executable oracle
- **WHEN** persisted offers must cover a requested standard-size amount
- **THEN** the transport calls `match.best_execution` with the eligible snapshot
- **AND** it does not substitute greedy, partial, or hand-written SQL matching

### Requirement: Settlement recording rejects pre-existing paths sequentially but is not race-atomic
Legacy repo-file node-bid settlement SHALL remain a repo-root-level record at `settlements/<bid_id>__<daemon_id>.yaml` with `schema_version: "1"`, the requester/owner/daemon identities, bid amount, evidence URL, completion timestamp, an `outcome_status` of exactly `succeeded` or `failed`, and `settled: false`. `record_settlement_event` SHALL continue to validate `outcome_status`, derive that v1 path, reject a path that already exists, and then write with ordinary `Path.write_text`; this protects sequential calls but SHALL NOT claim a race-atomic single winner for concurrent legacy writers. v1 YAML and `public.ledger` SHALL remain byte-for-byte historical and SHALL receive no new Wave 2 money writes. New dark-path accounting SHALL write only through the separate double-entry `market.*` transaction transport, with no shim or dual-write path.

#### Scenario: v1 settlement history stays frozen
- **WHEN** Wave 2 transport artifacts are installed or exercised in dark mode
- **THEN** existing settlement YAML and `public.ledger` rows remain unchanged
- **AND** no new `public.ledger` row is written by the Wave 2 path

#### Scenario: sequential double-settle is refused
- **WHEN** a v1 settlement already exists for a `(bid_id, daemon_id)` pair and recording is attempted again
- **THEN** `SettlementExistsError` is raised
- **AND** the existing record is left unchanged

#### Scenario: invalid outcome status is rejected
- **WHEN** a v1 settlement is recorded with an `outcome_status` other than `succeeded` or `failed`
- **THEN** a `ValueError` is raised and no file is written

#### Scenario: concurrent legacy creation retains its known limitation
- **WHEN** two legacy writers both pass the path-existence check before either ordinary write completes
- **THEN** the v1 recorder does not guarantee a single winner
- **AND** Wave 2 does not misrepresent or reuse that path as its atomic settlement transport

## ADDED Requirements

### Requirement: Paid requests follow one durable tenant-scoped workflow
The paid-market control plane SHALL own one versioned request workflow from submission through settlement or refund. A request SHALL bind the verified requester, requester tenant/universe, capability descriptor/version, visibility, budget and spend cap, bid window, deadline, acceptance policy, settlement-policy version, and optional bounded fan-out before it becomes eligible. The authoritative lifecycle SHALL be `pending -> bidding -> claimed -> running -> completed -> accepted|auto_accepted -> settled`, with `pending|bidding -> cancelled|expired`, `claimed|running -> failed -> refunded`, and `completed -> disputed -> accepted|refunded|running` as explicit branches. The reviewed `disputed -> running` edge authorizes a new fenced correction attempt and later returns through `completed`; it is not a hidden `corrected` terminal state. `completed` SHALL mean that the claimed host submitted an evidence-bound deliverable and the domain owner produced its semantic acceptance-gate verdict; it SHALL NOT mean requester acceptance or money release. A dispute SHALL freeze settlement until an authorized reviewed resolution selects acceptance, a newly fenced correction attempt, or refund.

Submission and every transition SHALL be body-bound idempotent and compare the current state/version in one transaction. The transition history SHALL be append-only and record request id, prior/new state, command digest/version, verified actor and authority/grant, related bid/match/claim/execution/delivery/settlement identities, and timestamp. UI, MCP, tray, worker, and webhook adapters SHALL delegate to this workflow owner and SHALL NOT update request lifecycle columns directly. Public behavior SHALL compose through the then-current canonical handle routers; this change SHALL NOT add a standalone advertised MCP action.

#### Scenario: identical request submission replays one request
- **WHEN** the requester retries the same body-bound submission after response loss
- **THEN** the workflow returns the original request identity and version
- **AND** creates no second request, budget reservation, or notification

#### Scenario: changed-body submission conflicts
- **WHEN** an idempotency key is reused with a changed capability, payload commitment, budget, bid window, acceptance policy, deadline, visibility, or fan-out
- **THEN** submission fails with an idempotency conflict
- **AND** the original request remains unchanged

#### Scenario: invalid or unauthorized transition is rejected
- **WHEN** an actor lacking the required requester, selected-host, or reviewed-dispute authority attempts a transition, or a command names a state edge not allowed from the locked current version
- **THEN** the transition fails before mutation
- **AND** possession of an internal service role or environment identity grants no positive workflow authority

#### Scenario: cancellation and claim race has one winner
- **WHEN** authorized requester cancellation races an eligible host claim against the same bidding request version
- **THEN** exactly one compare-and-set transition commits
- **AND** the losing command observes the committed state without a partial claim or budget effect

#### Scenario: adapters cannot create a second workflow
- **WHEN** chatbot, web, tray, worker, or webhook code submits or advances a paid request
- **THEN** it delegates to the same workflow command boundary
- **AND** no adapter maintains an alternate lifecycle, hidden balance mutation, or compatibility-side request row

### Requirement: The paid inbox is capability-sharded, replayable, and privacy-minimal
Committed eligible request versions SHALL be announced through capability-sharded Realtime channels keyed by a stable capability digest. The same database transaction that changes request eligibility SHALL append a durable per-shard outbox event with a strictly increasing cursor. Postgres request rows, transition history, and outbox SHALL remain authoritative; websocket frames SHALL be at-least-once invalidations, never queue truth, and the system SHALL NOT depend on native broadcast-replay retention for correctness. Each announcement SHALL carry only an opaque event id, shard cursor, opaque request id, request version, capability/version digest, public routing constraints, bid-window/deadline timestamps, visibility, and bounded fan-out metadata. It SHALL NOT include private payloads, credentials, deliverables, requester secrets, private node content, or money authority.

An authenticated daemon SHALL prove current host ownership, non-revoked capability eligibility, visibility eligibility, and a server-configured positive subscription maximum before subscribing or fetching request details. Reconnect SHALL first subscribe and buffer live frames, then call one authorized repeatable-read database operation that returns the eligible snapshot plus durable per-shard watermark `W`, query durable outbox events with cursor greater than `W` through the current head, merge those with buffered frames by event id/request version, and only then enter live-tail mode. If `W` has been compacted or cannot be proven, the daemon SHALL discard incremental state and repeat from a fresh snapshot/watermark; a provider replay window SHALL NOT weaken this fallback. Duplicate/out-of-order frames SHALL deduplicate by event id plus request version. Backpressure SHALL coalesce superseded invalidations, preserve the newest version, apply server-configured positive page/retry/subscription limits plus per-principal and per-shard fairness, expose degraded/stale status, and use bounded exponential retry; it SHALL NOT fall back to polling all requests or broadcasting every request to every daemon.

#### Scenario: durable commit precedes notification
- **WHEN** an eligible request is submitted
- **THEN** its authoritative row and transition event commit before the Realtime announcement is emitted
- **AND** notification failure leaves durable pending work available to snapshot reconciliation

#### Scenario: reconnect closes the snapshot-tail gap
- **WHEN** a daemon disconnects while eligible requests are inserted or changed
- **THEN** subscribe-and-buffer followed by the atomic snapshot/watermark and durable outbox catch-up yields every current eligible request version at least once before live-tail mode
- **AND** no request depends on a missed websocket frame for discoverability

#### Scenario: duplicate and reordered frames are harmless
- **WHEN** the notification layer redelivers, delays, or reorders events
- **THEN** the daemon reconciles by event id and authoritative request version
- **AND** creates no duplicate bid, claim, execution, or accounting effect

#### Scenario: an unauthorized subscriber learns no private request data
- **WHEN** a host lacks the required capability, visibility, tenant/network, or current enrollment authority
- **THEN** subscription or detail fetch is rejected
- **AND** public routing metadata cannot be expanded into payload, credential, requester-secret, or deliverable access

#### Scenario: hot-shard backpressure does not become poll-all
- **WHEN** one capability shard exceeds its delivery budget or the Realtime service is degraded
- **THEN** the system coalesces superseded versions, preserves fair bounded catch-up, and reports degraded freshness
- **AND** no daemon starts global inbox scans or unbounded retry loops

### Requirement: Paid-workflow storage is deny-by-default and rechecks positive authority
Request, bid, match, fan-out-slot, claim, transition-event, delivery-receipt, dispute, and outbox tables SHALL revoke direct insert/update/delete from `PUBLIC`, anonymous, authenticated, and ordinary application roles. Reads SHALL use least-privilege views/functions plus row-level policies scoped to the verified requester, selected host, explicitly authorized reviewer/collaborator, or privacy-minimal public projection. Mutations SHALL pass only through fixed-search-path command functions owned by non-login roles and explicitly granted to dedicated internal command roles. Possession of an internal/service role SHALL be necessary but never sufficient: each function SHALL independently lock and verify current request/bid/version, actor/tenant/host/grant, capability/visibility, state edge, idempotency digest, expiry/revocation generation, and delivery ACL before mutation. RLS SHALL remain defense in depth and SHALL NOT be the source of positive authority.

#### Scenario: public and application roles cannot write workflow tables
- **WHEN** public, anonymous, authenticated, or ordinary application roles attempt direct request, bid, match, claim, event, delivery, dispute, or outbox DML
- **THEN** PostgreSQL denies the operation
- **AND** no lifecycle or authority-bearing row changes

#### Scenario: privileged command role cannot invent authority
- **WHEN** an internal command caller names an actor, tenant, host, grant, request version, state edge, or delivery ACL that does not match the locked authoritative rows
- **THEN** the command function rejects before mutation
- **AND** service-role possession does not widen the actor's allowed action

#### Scenario: cross-tenant reads reveal no row existence
- **WHEN** an unrelated tenant or unselected host reads private request, bid, match, transition, dispute, or delivery state
- **THEN** the least-privilege surface returns no row and no enumerable identifier, artifact location, or private count
- **AND** only the explicitly public privacy-minimal projection remains available

#### Scenario: hostile search path cannot redirect a workflow command
- **WHEN** a caller creates lookalike objects in a writable schema and invokes a privileged workflow function
- **THEN** the fixed trusted search path resolves only intended objects
- **AND** no attacker-controlled function, table, or operator executes

#### Scenario: revoked authority fails despite cached RLS visibility
- **WHEN** a host, capability, collaborator, reviewer, or on-behalf grant is revoked after a prior authorized read
- **THEN** the next command rechecks the current revocation generation and fails closed
- **AND** stale cache or RLS-session state cannot preserve positive mutation authority

### Requirement: Bids and match decisions are versioned, authorized, and reproducible
Only an authenticated, non-revoked host owner or a target/action/time-bounded on-behalf grant SHALL create or replace a bid. Each bid SHALL bind immutable request identity/version, bid identity/version, host and owner identities, capability descriptor/version, executable quantity, landed price and fee-policy version, delivery/acceptance terms, capacity fence, expiry, and canonical digest/signature where required. A persistent bid SHALL materialize exactly one immutable pure `BookOffer` for matching, with `offer_id = bid_id` and identical version, quantity, and economic terms; “offer” SHALL name only that pure adapter value, not a second persisted lifecycle. When price discovery selected a native firm quote, the bid SHALL additionally bind and revalidate its quote id/version/digest. One host-capacity slot SHALL expose at most one current bid version for a request; replacement, cancellation, expiry, capability revocation, capacity consumption, or owner/grant revocation SHALL make earlier versions ineligible without erasing history.

Within one explicitly chosen paid request/path, the market SHALL resolve a bid window by invoking the canonical pure matcher over one versioned eligible bid snapshot. Before `bid_window_ends_at`, only the requester or its target/action/time-bounded grant SHALL record the match and thereby close the window early. At or after the window boundary, an eligible request-bound host MAY invoke the deterministic matcher, but the resulting total SHALL NOT exceed the requester's spend cap. Neither match nor claim SHALL proceed at or after the request deadline. Cross-lane quote evaluation SHALL remain owned by `paid-market-live-price-discovery`; Wave 2 SHALL allocate only request-bound bidders/fan-out slots and SHALL NOT repeat or override that routing decision. It SHALL record an immutable match decision containing the request and selected bid versions, any linked quote versions, rejected bids with bounded reason codes, matcher/oracle version, hard constraints, requester-authorized objective and weights, requester early-close authorization, deterministic tie-break inputs, fan-out slot assignment, and decision digest. Hidden platform preferences, maintainer capacity, provider credentials, or post-window bid mutation SHALL NOT affect selection. A later claim SHALL atomically revalidate the exact recorded versions, requester cap, early-close/window state, and deadline before consuming them or recomputing through the bounded contention path; a match decision alone SHALL authorize neither execution credentials nor money movement.

#### Scenario: expired or revoked bid cannot win
- **WHEN** a bid expires or its host, capability, capacity, owner, or grant authority is revoked before the match snapshot
- **THEN** the matcher excludes that bid with a recorded reason
- **AND** no stale signature, cached announcement, or internal role restores eligibility

#### Scenario: equal bids resolve reproducibly
- **WHEN** eligible request-bound bids are equal under the requester-authorized objective
- **THEN** the recorded matcher version and deterministic tie-break produce the same selection for the same snapshot
- **AND** no arrival-order race after the bid window or hidden platform weighting changes the result

#### Scenario: match receipt binds the later claim
- **WHEN** a host attempts to claim from a recorded match decision
- **THEN** the claim rechecks the exact request, bid, capability, capacity, expiry, authority, version, spend-cap, bid-window, requester-early-close, and deadline bindings
- **AND** any stale binding aborts the entire claim or enters the bounded recomputation path

#### Scenario: host cannot race the requester at an unbounded price
- **WHEN** an eligible host attempts to record or claim a match before the bid window closes or above the requester spend cap
- **THEN** the command is refused unless a requester-authorized early-close receipt binds the selected match within cap
- **AND** a cheaper bid, host timing, or caller-chosen quantity cannot turn host eligibility into unbounded self-selection

#### Scenario: request deadline terminates matching and claiming
- **WHEN** match or claim is attempted at or after the request deadline, including with a bid whose own expiry is later
- **THEN** the command is refused before any request, bid, capacity, match, or claim mutation

#### Scenario: fan-out allocates only declared slots
- **WHEN** a request declares bounded `top_n` fan-out
- **THEN** the match decision assigns at most the declared number of independently fenced slots
- **AND** concurrent claims cannot consume the same slot or silently widen the fan-out

#### Scenario: matching grants no provider or payment authority
- **WHEN** a bid wins deterministic selection
- **THEN** the decision records economic eligibility only
- **AND** provider credentials, execution leases, logical reservations, and real-fund effects still require their separate authoritative grants and receipts

### Requirement: Paid-market claims are narrow, exact, and atomic
The Postgres paid-request claim transport SHALL claim only eligible bid work in one transaction. Every bid row SHALL carry a monotonic `version` used by compare-and-set and SHALL materialize one immutable pure `BookOffer`. A single-request claim SHALL lock only the addressed request/bid rows. A multi-bid allocation SHALL call `match.best_execution` on a versioned eligible bid snapshot, lock the selected bid IDs in canonical order, verify state and version, and transition all selected rows atomically. Every request-state write SHALL pass through the canonical allowed-transition guard; an unlisted edge SHALL raise before mutation, and changing the transition table SHALL change command behavior rather than only enumeration output. A stale selected bid SHALL roll back and permit at most three jittered recomputations; exhaustion SHALL return an honest contention result rather than a partial fill or retry storm. This claim domain SHALL NOT replace the separate repo-file node-bid claim path.

#### Scenario: exactly one claimer wins a paid request
- **WHEN** multiple eligible actors concurrently claim the same offered paid request
- **THEN** exactly one atomic state transition succeeds
- **AND** every loser receives a clean contention result with no partial state

#### Scenario: selected bid changes before claim
- **WHEN** `best_execution` selects multiple bid-derived `BookOffer` values and any selected bid version is stale at lock time
- **THEN** no selected bid is claimed in that transaction
- **AND** the transport either recomputes within the bounded retry budget or reports contention

#### Scenario: insufficient supply stays honest
- **WHEN** `best_execution` returns no covering set
- **THEN** the transport records no claim
- **AND** it does not silently accept a partial fill

#### Scenario: transition table is runtime authority
- **WHEN** a required claim edge is removed or a forbidden edge is added to the canonical transition table
- **THEN** the corresponding command behavior changes before any state mutation
- **AND** no caller-specific hardcoded source-state list bypasses or overrides the table

### Requirement: Paid delivery is fence-bound, replay-safe, and dispute-aware
Only the host holding the current paid claim and distributed-execution lease SHALL advance a request to `running` or submit completion. Completion SHALL bind the exact `job_id:lease_fence:accepted_result_sha256` identity, request/claim/match versions, immutable deliverable artifact reference and digest, media/schema type, byte count, producer identity, execution receipt, declared domain acceptance gates and the domain owner's semantic verdict, and delivery ACL. The workflow SHALL store the deliverable receipt and `completed` transition atomically or store neither, but it SHALL NOT compute, replace, or upgrade the domain verdict. Progress messages and host self-attestation SHALL NOT constitute completion, acceptance, a paid observation, or settlement authority.

The request SHALL bind the acceptance class permitted by its separately reviewed domain contract. A machine-gate-only bounty or standing-goal task SHALL use policy-driven lifecycle acceptance from the first positive immutable machine-gate verdict; the requester SHALL NOT bind discretionary review, withhold acceptance, or reverse a positive verdict by subjective preference. A dispute in that class MAY challenge evidence integrity, actor/lease authority, or gate execution and MAY trigger a deterministic rerun or higher-tier evaluator, but the resolver SHALL NOT replace a valid machine verdict with unstructured human judgment. Explicit requester/inspector review or a disclosed bounded dispute window with policy-authorized `auto_accepted` SHALL be available only to domains whose contracts declare those human/inspection semantics, such as fabrication inspection or training checkpoint review. Acceptance SHALL revalidate the immutable delivery receipt, current request version, bound domain verdict, and allowed acceptance class; `paid-market-economy` owns the lifecycle transition but not the domain semantics. An authorized dispute SHALL record bounded reason/evidence references, preserve the deliverable and history, block settlement, and route resolution through the domain's reviewed dispute boundary plus moderation only for abuse/process integrity. A reviewed correction SHALL transition `disputed -> running` only by issuing a new fenced execution attempt; its result SHALL return through `completed` with a new immutable receipt and domain verdict. A failed or rejected terminal result SHALL invoke the same atomic accounting transport for the policy-defined refund only after its required evidence is recorded. Delivery reads SHALL enforce requester, selected-host, reviewer, and explicitly granted collaborator ACLs; public routing metadata SHALL never make a private deliverable public.

#### Scenario: stale lease cannot submit a deliverable
- **WHEN** a former claimer or worker submits completion under an expired or superseded lease fence
- **THEN** completion is rejected before storing a delivery receipt or changing request state
- **AND** the current claim remains authoritative

#### Scenario: completion response loss replays one receipt
- **WHEN** the selected host loses the response after an evidence-bound completion command commits
- **THEN** an identical retry returns the original delivery receipt and completed transition
- **AND** creates no duplicate artifact, transition, acceptance timer, or settlement trigger

#### Scenario: changed deliverable retry conflicts
- **WHEN** the completion idempotency key is reused with a changed artifact digest, accepted-result hash, gate result, receipt, or ACL
- **THEN** the command conflicts and preserves the original immutable delivery
- **AND** no changed body inherits the original authority

#### Scenario: dispute freezes settlement
- **WHEN** the requester files an authorized dispute before the bound acceptance deadline
- **THEN** the workflow enters `disputed`, preserves evidence, and blocks settlement or price-observation admission
- **AND** only the authorized reviewed resolution can select acceptance, refund, or a new fenced correction attempt returning through `running -> completed`

#### Scenario: auto-accept follows only the bound policy
- **WHEN** the disclosed dispute window expires without a valid dispute and the request bound an auto-accept policy
- **THEN** the policy worker rechecks the bound positive domain verdict and compare-and-sets the unchanged completed version to `auto_accepted`
- **AND** an absent, changed, premature, or stale policy cannot release settlement

#### Scenario: requester cannot veto a positive machine-gated bounty
- **WHEN** a machine-gate-only bounty or standing-goal task has a valid first positive immutable domain verdict and the requester attempts discretionary rejection or withholding
- **THEN** the workflow follows the domain's policy-driven acceptance and settlement path
- **AND** any integrity dispute can only invoke the reviewed evidence/gate rerun process, not substitute subjective requester judgment

#### Scenario: private delivery remains private
- **WHEN** an unrelated tenant, unselected host, public quote reader, or Realtime subscriber requests the deliverable
- **THEN** access is denied without revealing artifact location or private content
- **AND** only privacy-minimal routing and terminal-status metadata permitted by policy remains visible

#### Scenario: failed work refunds through the accounting owner
- **WHEN** the accepted failure/refund policy and required failure evidence authorize a refund
- **THEN** the workflow invokes the single body-bound market accounting transport and records the linked refund identity
- **AND** no API, worker, or dispute adapter writes balances directly

### Requirement: One authenticated transaction transport owns all logical market accounting transitions
Every market-accounting debit, credit, logical reservation transition, fee entry, refund entry, and collateral-account transition SHALL use the named pure adapter and the single internal versioned `market.apply_tx` transaction boundary owned by `paid-market-economy`; application, API, SQL, HTTP, MCP, and workflow code SHALL NOT compute and write balances or ledger rows through an alternate path. The canonical fee function SHALL charge at least one micro on every positive-gross settlement; a zero-gross full refund has no seller fee base and is not a route or actor exemption. SQL SHALL validate the adapter's structural conservation and positive fee but SHALL NOT fork the fee formula from the pure oracle. In Wave 2, account names beginning `escrow:*` denote logical reservation accounting only, never proof of custody or real-fund reservation. Wave 2 transport SHALL combine tenant-scoped business-state compare-and-set, actor/account authority, body-bound idempotency, adapter-derived postings, `market.apply_tx`, and every required logical reservation/collateral drain assertion in one server-side database transaction. The trusted wrapper SHALL ignore any caller-computed hash, recompute SHA-256 over a versioned domain-separated canonical encoding of the complete command, and bind the deterministic tenant-scoped idempotency key to that digest; an identical replay returns the original result, while a supplied-hash mismatch or changed canonical body conflicts. It SHALL coalesce duplicate accounts and acquire every touched row in one reviewed global order: tenant-scoped business rows by type/id, logical reservation/collateral rows by type/id, idempotency transaction row, then balance accounts lexicographically; postings/audit rows are inserted only after required locks. Any authorization, oracle, overdraft, residual, state, deadlock-order, or transport failure SHALL roll back the entire transaction, and persistent results SHALL be differential-tested against the canonical pure `Ledger` and settlement oracles. A completion-dependent settlement SHALL NOT create a releasable accounting result until the domain owner validates every normalized delivery field required for that completion; missing or implausible required evidence SHALL reject the completion or enter its domain dispute path before transport invocation. Direct SQLite accounting side paths SHALL be removed before launch, and schema history SHALL precede prototype migrations 006–008. `market.apply_tx` success SHALL neither prove nor authorize wallet funding, real-fund reservation, payout, refund, or chain settlement. User-owned wallets remain the source of real-fund authority; PostgreSQL stores only bounded logical reservation/accounting intent and independently verified receipt state, and Wave 2 adds no user signing-key storage, signer, payout dispatcher, or smart-contract escrow. The transport SHALL remain unreachable from live claim/settle paths while `TINYASSETS_PAID_MARKET` is off and until S14/B36 from `2026-07-18-distributed-execution-platform.md (archived in git)`, the required chain-settlement successor from `docs/design-notes/2026-04-18-full-platform-architecture.md` §18.6, independent review, and host-approved cutover are complete.

#### Scenario: successful dark settlement conserves and drains
- **WHEN** an authorized dark-path settlement uses a pure adapter and valid current business state
- **THEN** integer-micro postings commit exactly once and sum to zero
- **AND** every temporary logical reservation/collateral account is zero in the same committed transaction

#### Scenario: inference completion without normalized counts moves no value
- **WHEN** an inference completion omits required normalized input, output, or applicable cached-token evidence
- **THEN** the inference domain rejects completion or opens its dispute path before invoking the transaction transport
- **AND** no completion-dependent funds movement or null-count settlement observation is recorded

#### Scenario: same-owner settlement still pays the canonical fee
- **WHEN** locked tenant-scoped request and winning-bid rows prove exact immutable equality of `request.requester_user_id` and `winning_bid.host_owner_user_id`
- **THEN** any TinyAssets settlement invokes the canonical fee-bearing adapter and records the treasury posting
- **AND** any policy that excludes same-owner work from market volume or price formation changes neither the fee nor the non-custodial boundary

#### Scenario: no actor relationship creates a settlement-fee exemption
- **WHEN** requester and host are common operators, organization members, on-behalf principals, payout-root linked, or otherwise economically linked but their locked immutable user ids are not identical
- **THEN** the canonical pure paid-market settlement applies the ordinary fee
- **AND** no caller field, exact identity, grant, external route, or economic-principal inference can remove the fee from a settlement

#### Scenario: logical reservation is not real-fund authority
- **WHEN** a logical reservation or balanced `market.apply_tx` transaction exists without a separately verified wallet or chain-authority receipt required by the `docs/design-notes/2026-04-18-full-platform-architecture.md` §18.6 successor
- **THEN** the request remains unfunded and non-executable for real-value work
- **AND** no work release, payout, refund, or chain effect is authorized from the database row alone

#### Scenario: residual logical reservation aborts everything
- **WHEN** a settlement posting set would leave any required logical reservation or collateral account non-zero
- **THEN** the drain assertion fails loud
- **AND** no business-state change, transaction, posting, or balance update commits

#### Scenario: live payout remains unavailable before authority cutover
- **WHEN** the distributed-execution exec plan’s S14/B36 gates, the required chain-settlement successor from `docs/design-notes/2026-04-18-full-platform-architecture.md` §18.6, or host-approved cutover is incomplete
- **THEN** no public/API path can activate a market claim, settlement, or on-chain payout
- **AND** read-only market state remains available only with an explicit dark/unavailable status

#### Scenario: database response loss cannot apply twice
- **WHEN** a caller loses the response after a settlement attempt
- **THEN** a retry with the same body-bound idempotency key returns the prior database transaction
- **AND** the system creates no second database effect

#### Scenario: concurrent identical replay applies once
- **WHEN** 100 callers concurrently submit the same tenant-scoped idempotency key and identical canonical command
- **THEN** they observe one transaction identity and one applied effect
- **AND** no balance or posting is duplicated

#### Scenario: changed-body replay conflicts
- **WHEN** a caller reuses an idempotency key with a changed memo, account, amount, posting order, or other canonical body field
- **THEN** the transport returns an idempotency conflict
- **AND** the original transaction remains unchanged

#### Scenario: caller hash cannot bless a changed command
- **WHEN** a caller supplies a digest that does not equal the server-recomputed versioned canonical command digest
- **THEN** the wrapper rejects the command before row locks or mutation
- **AND** stores neither the caller digest nor a transaction result

#### Scenario: global lock order avoids cross-family deadlock
- **WHEN** concurrent commands touch overlapping business, logical reservation, collateral, idempotency, and account rows in different input orders
- **THEN** the wrapper coalesces and locks them in the single canonical order
- **AND** commits or returns clean contention without a deadlock or partial effect

#### Scenario: persistent settlement matches the pure oracle
- **WHEN** randomized adapter transactions run through both the persistent transport and pure `Ledger`
- **THEN** balances, conservation, overdraft decisions, and drain outcomes match
- **AND** any divergence rolls back and fails loud

### Requirement: Future market transports remain differential-tested against canonical pure oracles
Every transport introduced by this or a dependent market change SHALL consume the canonical `tinyassets.paid_market` input/output contracts or prove behavioral equivalence through generated differential tests covering valid inputs, rejection boundaries, rounding, state transitions, fees on every settlement including same-owner and external-supply settlement, refunds, collateral, and conservation. A transport SHALL NOT silently fork a formula into SQL, HTTP, MCP, API, or workflow code; any intentional rule change MUST first modify the canonical oracle requirement and tests through its own OpenSpec change.

#### Scenario: transport and oracle cannot drift silently
- **WHEN** a transport implementation changes a price, settlement, apportionment, fee, refund, collateral, or NAV result for the same inputs
- **THEN** the differential gate fails until an explicit reviewed behavior change updates both canonical contract and implementation

### Requirement: The ledger boundary is least-privilege and bounded
Ledger tables, sequences, raw apply functions, and drain helpers SHALL deny access to `PUBLIC`, anonymous, authenticated, and ordinary application roles. A fixed-search-path `SECURITY DEFINER` wrapper owned by a non-login role SHALL be callable only by a dedicated internal settlement role after actor/account binding. The wrapper SHALL derive business accounts from locked rows, use only the configured treasury account, and reject `external:*` and `pool:*` accounts. Wave 2 SHALL reject more than 16 postings, idempotency keys over 128 UTF-8 bytes, memos over 512 bytes, account names over 256 bytes, or canonical posting payloads over 16 KiB before mutation.

#### Scenario: account provenance cannot be forged
- **WHEN** a caller supplies a treasury, external, pool, or business account that differs from the trusted configuration or locked business rows
- **THEN** the wrapper rejects the request before ledger execution
- **AND** no transaction, posting, or balance changes

#### Scenario: public, user, and wrapper-caller roles cannot invoke raw ledger writers
- **WHEN** public, anonymous, authenticated, ordinary application, or settlement-wrapper caller roles attempt direct table DML or raw ledger RPC execution
- **THEN** PostgreSQL denies the operation
- **AND** only the wrapper's non-login definer may invoke the raw ledger function while the dedicated settlement role can invoke only the bounded wrapper

#### Scenario: privileged wrapper independently rechecks row authority
- **WHEN** an internal caller submits command authority that does not match the tenant, actor, amount, accounts, or state on locked business rows
- **THEN** the wrapper rejects before `market.apply_tx`
- **AND** possession of the internal service role does not grant positive market authority

#### Scenario: hostile search path cannot redirect the ledger function
- **WHEN** a caller creates lookalike objects in a writable schema and invokes the settlement wrapper
- **THEN** the fixed trusted search path resolves only the intended ledger objects
- **AND** no attacker-controlled object is executed

#### Scenario: oversized settlement is rejected before mutation
- **WHEN** any posting-count or byte-size bound is exceeded
- **THEN** the wrapper rejects the request with a bounded validation error
- **AND** creates no transaction or posting row

### Requirement: Paid-market migrations are replay-safe and production-native
The v0 fixture PostgreSQL chain SHALL use unique, gap-free, strictly increasing identifiers in dependency order: `001_core_tables`, `002_flags`, `003_rls`, `004_indexes`, `005_seed`, `006_discover_nodes`, `007_token_normalization`, `008_forwards`, and `009_market_ledger`. An advisory-lock-protected runner SHALL store `schema_migrations(version, name, sha256, applied_at)`, check exact-byte hashes, establish/check the discovery vector dependency before use, and commit each migration with its history row in one transaction. Duplicate, missing, reordered, drifted, unverifiable, or lock-contended fixture migrations SHALL fail closed. Public and application roles SHALL NOT alter migration history. Before any live paid-market SQL is authored, a read-only inventory SHALL record the deployed Supabase schemas, extensions, auth, policies, functions, roles, indexes, migration history, and deployment mechanism; production SQL SHALL be authored from the host-approved production baseline and migration home, never copied from the prototype.

#### Scenario: fixture chain applies and resumes exactly once
- **WHEN** the fixture runner applies a fresh, partially applied, or previously verified fixture chain
- **THEN** each pending version and its immutable history row commit exactly once in order
- **AND** a failed version leaves no history row or partial schema mutation

#### Scenario: untracked existing fixture is baselined only after exact verification
- **WHEN** a pre-existing fixture schema has no `schema_migrations` rows
- **THEN** the runner verifies the exact expected tables, columns, functions, policies, constraints, and migration bytes before recording any baseline
- **AND** any mismatch aborts before a later version can apply

#### Scenario: drift or concurrent application fails closed
- **WHEN** migration identifiers have a gap, duplicate, or ordering error, applied bytes change, or concurrent runners contend
- **THEN** no unsafe pending SQL executes
- **AND** the advisory lock serializes one valid runner or returns a bounded failure

#### Scenario: application roles cannot rewrite migration truth
- **WHEN** a public, anonymous, authenticated, or ordinary application role attempts migration-history DML
- **THEN** PostgreSQL denies the operation
- **AND** only the migration role can append a committed history row

#### Scenario: prototype SQL cannot become live authority
- **WHEN** the deployed Supabase inventory, approved production baseline, or deliberate live-apply approval is absent
- **THEN** no production database applies the paid-market migrations
- **AND** the market remains default-off

#### Scenario: populated fixture upgrade remains compatible
- **WHEN** the runner upgrades a populated supported fixture database
- **THEN** both the upgraded application and the prior application version pass their read/write compatibility suites while the market flag remains off
- **AND** rollback to the prior application requires no destructive schema reversal

### Requirement: Wave 2 activation requires concurrency, recovery, and zero-host proof
The Wave 2 request workflow and transport SHALL remain dark until a production-shaped isolated environment records dated evidence for role isolation, actor binding, body-bound replay, Realtime disconnect/reconnect and duplicate delivery, hot-shard backpressure, bid replacement/expiry, deterministic match replay, matcher/claim contention, fenced completion and delivery replay, dispute/acceptance races, response-loss recovery, ledger conservation, terminal logical-reservation contention, migration recovery, and zero-host behavior. Evidence SHALL include environment, exact commands, load, event-to-visible and command latency distributions, reconnect/catch-up lag, resource occupancy, raw failure counts, duplicate/lost event and effect counts, tenant/shard fairness, and independent review before host-approved activation.

#### Scenario: capability-sharded claim storm stays correct and bounded
- **WHEN** 500 synthetic daemons receive 1,000 paid requests over five minutes through the production-shaped capability push and narrow claim boundary without mocked delivery
- **THEN** no request is lost, no eligible current request version remains undiscoverable after bounded catch-up, no request or fan-out slot is claimed twice, and event-to-visible plus claim latency p99 remain below three seconds
- **AND** the system creates no poll-all retry storm

#### Scenario: reconnect and backpressure preserve current work
- **WHEN** 20 percent of 500 synthetic daemons repeatedly disconnect and reconnect while one capability shard is saturated and request versions are replaced or cancelled
- **THEN** snapshot-plus-cursor reconciliation converges every authorized daemon to the current eligible set with no private cross-shard disclosure
- **AND** duplicate frames, superseded versions, and bounded coalescing create no duplicate bid, claim, execution, delivery, or accounting effect

#### Scenario: bid-book contention never double-sells
- **WHEN** 100 buyers concurrently match and claim from one versioned bid book
- **THEN** no bid-derived capacity slot is sold twice
- **AND** every committed selection equals `best_execution` for its valid snapshot

#### Scenario: overlapping writers preserve conservation
- **WHEN** at least 64 writers apply at least one million overlapping transfers in the production-shaped test environment
- **THEN** aggregate throughput is at least 5,000 committed transactions per second, p99 is below 250 milliseconds, and balances and postings remain zero-sum with no negative internal balance, deadlock, timeout, or duplicate effect
- **AND** sustained CPU and pool occupancy remain below 80%, with p50, p95, and p99 recorded

#### Scenario: one logical reservation account has one terminal result
- **WHEN** 500 callers concurrently attempt to settle and drain the same logical reservation account
- **THEN** exactly one terminal settlement succeeds
- **AND** every other caller receives the prior result or a clean state/idempotency conflict

#### Scenario: fault injection never applies twice
- **WHEN** execution stops before or after request submission, notification emission, bid replacement, match recording, claim CAS, completion receipt storage, acceptance/dispute CAS, ledger apply, drain assertion, commit, or response delivery
- **THEN** recovery yields zero or one committed effect
- **AND** no retry creates a duplicate transaction or posting

#### Scenario: completion and dispute race cannot release twice
- **WHEN** duplicate completion, requester acceptance, auto-accept expiry, and dispute commands race on one delivered request
- **THEN** each body-bound command replays or conflicts against one versioned lifecycle history and at most one settlement/refund disposition becomes authoritative
- **AND** a timely committed dispute prevents settlement until reviewed resolution

#### Scenario: zero hosts remains honest
- **WHEN** every tray and daemon host is offline
- **THEN** market reads and durable state remain available while unfulfilled work stays pending
- **AND** no settlement is fabricated or attributed to platform/maintainer compute
