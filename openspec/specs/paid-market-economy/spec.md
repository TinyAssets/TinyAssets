# Paid Market Economy

> As-built baseline (2026-07-19, change `spec-out-existing-platform`): describes landed behavior on `main` at baseline time, known limitations included. Future behavior changes arrive as OpenSpec change deltas against this capability.

## Purpose

The built token/market/settlement subset: the flag-gated escrow money path with integer MicroTokens, file-backed node bids with atomic claim and write-once settlement, and conserved basis-point treasury math.

## Requirements

### Requirement: The money path is flag-gated off and pre-launch
Every value-moving paid-market action SHALL be inert unless `TINYASSETS_PAID_MARKET` is truthy (`on`/`1`/`true`/`yes`), and the flag SHALL default off. Under flag-off, the escrow money actions (`escrow_lock`, `escrow_release`, `escrow_refund`, `escrow_fund`, `escrow_set_wallet`, `escrow_withdraw` in the paid-market API) SHALL return a `not_available` status instead of touching funds, and the `NodeBidProducer` SHALL NOT register, so the dispatcher never sees bid tasks. Read-only surfaces (escrow inspect/balance, treasury status) SHALL remain available regardless of the flag, because a read cannot move money. On-chain withdrawal defaults to a testnet chain (Base Sepolia), confirming the path is pre-launch.

#### Scenario: mutating escrow action is inert under flag-off
- **WHEN** `TINYASSETS_PAID_MARKET` is unset or off and a caller invokes `escrow_lock`, `escrow_fund`, or `escrow_withdraw`
- **THEN** the action returns `{"status": "not_available"}` with a message naming `TINYASSETS_PAID_MARKET=on`
- **AND** no escrow, settlement, or wallet record is written

#### Scenario: bid producer does not register under flag-off
- **WHEN** `register_if_enabled` runs while the flag is off
- **THEN** the `NodeBidProducer` is not registered
- **AND** the dispatcher produces no bid-backed `BranchTask`s

#### Scenario: reads stay available regardless of the flag
- **WHEN** the flag is off and a caller invokes `escrow_inspect`, `escrow_balance`, or treasury status
- **THEN** the read returns its summary without a `not_available` gate

### Requirement: Money actions operate only on the authenticated actor
Value-moving actions (fund, set-wallet, withdraw, lock) SHALL act on the authenticated actor resolved from auth context, never on a caller-supplied identity. A caller-supplied `staker_id` SHALL be honored only when it equals the authenticated actor, or when the authenticated actor is the configured host (`UNIVERSE_SERVER_HOST_USER`) acting explicitly on another's behalf; any other cross-actor attempt SHALL be rejected with an error and no state change. A lock SHALL reserve the caller's own funded budget, and release/refund SHALL be authorized against the lock's own staker (or host), so a write-scoped caller cannot fund, withdraw, redirect, or cancel another actor's escrow by id. As-built limitation (security concern, STATUS.md 2026-07-19): the "authenticated actor" is resolved by the ledger-attribution helper `_current_actor()` (`tinyassets/api/engine_helpers.py`), which falls back to the `UNIVERSE_SERVER_USER` environment variable on authless paths — escrow authorization then derives identity from daemon environment rather than a verified subject, and an env identity equal to `UNIVERSE_SERVER_HOST_USER` would carry host on-behalf rights. The hardened no-env-fallback resolver (`tinyassets/api/permissions.py`) is not yet the money-path authority; the default-off money flag is the operative mitigation.

#### Scenario: cross-actor escrow attempt is rejected
- **WHEN** an authenticated actor supplies a `staker_id` that is neither themselves nor (when they are host) an on-behalf target, for `escrow_fund`, `escrow_set_wallet`, or `escrow_withdraw`
- **THEN** the action returns a `rejected` status stating money actions operate on your own escrow only
- **AND** no funds, wallet address, or withdrawal are recorded

#### Scenario: host may act on behalf of another actor
- **WHEN** the authenticated actor equals `UNIVERSE_SERVER_HOST_USER` and supplies another actor's `staker_id`
- **THEN** the action proceeds against that actor's escrow

### Requirement: Payment-core conversions produce integers while legacy bids permit non-integer scalars
The payments core SHALL construct `MicroToken` through Python's `int` boundary
after rejecting values that compare below zero, and subtraction below zero
SHALL raise. Current money-action transports SHALL also call `int(...)`; a
fractional JSON number can therefore be truncated and `True` becomes `1`, while
`False` becomes `0` and mutating payment actions subsequently reject converted
amounts less than or equal to zero. A fractional string that `int(...)` cannot
parse SHALL be rejected. `NodeBid.bid` SHALL preserve the caller/YAML scalar
type without runtime coercion, so float bids are permitted, while v1 settlement
serialization SHALL coerce `bid_amount` to float.

#### Scenario: negative payment-core money is rejected
- **WHEN** code constructs `MicroToken(-1)` or subtracts past zero
- **THEN** a `ValueError` is raised

#### Scenario: current transport conversion can truncate a numeric fraction
- **WHEN** a money action receives a positive fractional JSON number such as `1.5`
- **THEN** current `int(...)` conversion passes `1` to the action rather than rejecting the fraction at transport
- **AND** a converted amount less than or equal to zero is rejected by the mutating payment action
- **AND** a fractional string such as `"1.5"` is rejected because `int(...)` cannot parse it

#### Scenario: legacy bid storage permits float amounts
- **WHEN** a `NodeBid` receives and serializes a fractional float bid
- **THEN** its runtime/YAML representation preserves that supplied scalar type
- **AND** a v1 settlement derived from it serializes `bid_amount` as a float

### Requirement: Node bids are file-backed with atomic single-claim
A node bid SHALL be a cross-universe, single-node execution request persisted as `bids/<node_bid_id>.yaml` under the repo root. Claiming a bid SHALL be atomic: under a per-bid file lock the claimer SHALL assert the bid is `open`, rename it to a `claimed_by_<daemon>` record with status `claimed:<daemon_id>`, then (when a git remote exists) commit and push; a failed push SHALL revert the working tree and delete any partial bid outputs, returning no claim so two daemons cannot both win the same bid. Reads SHALL treat the filename stem as the authoritative id (defeating rename-in-place tampering) and SHALL skip malformed YAML with a warning rather than raising.

#### Scenario: only one daemon wins a contested bid
- **WHEN** a bid is `open` and one claimer completes the rename-and-push while another loses the remote race
- **THEN** the losing claim reverts its working tree, removes partial outputs, and returns no bid
- **AND** exactly one `claimed_by_<daemon>` record exists

#### Scenario: malformed bid file is skipped, not fatal
- **WHEN** the bids directory contains an unparseable or non-mapping YAML file
- **THEN** it is logged and skipped
- **AND** the remaining valid bids are still read

### Requirement: Settlement recording rejects pre-existing paths sequentially but is not race-atomic
`record_settlement_event` SHALL validate `outcome_status`, derive the v1
repo-root settlement path, reject a path that already exists, and then write the
YAML record with ordinary `Path.write_text`. This check-then-write boundary
SHALL protect sequential calls but SHALL NOT provide an atomic single-winner
guarantee for concurrent writers that both observe the path as absent.

#### Scenario: sequential double-settle is refused
- **WHEN** a settlement path already exists and recording is attempted again
- **THEN** `SettlementExistsError` is raised
- **AND** the existing record is left unchanged

#### Scenario: invalid outcome status is rejected
- **WHEN** a settlement is recorded with an `outcome_status` other than `succeeded` or `failed`
- **THEN** a `ValueError` is raised and no file is written

#### Scenario: concurrent creation has no single-winner guard
- **WHEN** two writers both pass the path-existence check before either ordinary write completes
- **THEN** the current recorder does not guarantee a single winner
- **AND** a later write may replace the earlier record

### Requirement: Treasury take is conserved basis-point math with a read-only status surface
The treasury SHALL compute its take as pure integer basis-point math: a 1% platform take (100 bp) split 50/50 between a bounty pool and treasury retention, using floor division so `net_to_claimer + bounty_pool + treasury_retained` equals the settlement amount exactly. The treasury/cost-ledger status surface SHALL be strictly read-only — it SHALL NOT run migrations, create the database, or lock/release/refund/batch/spend — reporting `autonomous_spend_allowed: false` and treating a missing database or table as zeroed sections so a status check can never become an implicit payment write. It is exposed as an economy read on the universe MCP tool.

#### Scenario: the split conserves the settlement amount
- **WHEN** `split_take` runs on a settlement amount
- **THEN** it returns `(net_to_claimer, bounty_pool, treasury_retained)` summing exactly to the amount
- **AND** each part is a non-negative integer computed by floor division

#### Scenario: status never writes
- **WHEN** treasury status runs against a missing or empty database
- **THEN** it returns zeroed sections with `read_only: true` and `autonomous_spend_allowed: false`
- **AND** creates no database, table, or ledger entry
