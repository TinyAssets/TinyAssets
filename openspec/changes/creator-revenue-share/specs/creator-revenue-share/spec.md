## ADDED Requirements

### Requirement: A payable piece is one immutable published version under a stable bundle identity

The platform SHALL treat as a payable piece only an immutable published `universe-custom-agents` public definition carrying a command-center bundle manifest, attributed to that definition's author, and SHALL group a piece's versions under a stable `bundle_id` that only its original author can publish under. A user's unpublished harness, a platform-owned published template, and any piece authored by the paying account SHALL NOT be payable. A remix by a different author SHALL receive its own `bundle_id`, and its parents SHALL earn only through the remixer's declared, verified component credit shares.

#### Scenario: A new version keeps the bundle identity
- **WHEN** an author publishes version 2 of their command center
- **THEN** version 2 is a new immutable definition with the same `bundle_id`, and metrics roll both versions up under that bundle

#### Scenario: Someone else cannot publish under a bundle
- **WHEN** a different author publishes a definition claiming an existing `bundle_id`
- **THEN** the publish is refused, and no definition is written under that bundle

### Requirement: Credited agent time counts only useful agent work

The platform SHALL credit each seat-held agent call with its inference seconds, meaning the wall time of model rounds that returned a valid response, plus its tool seconds capped at a founder-set ratio of those inference seconds. Time spent waiting for a seat, waiting on the owner, in rounds that errored, were refused or returned nothing, and in calls refused before any model round SHALL earn nothing. A piece version SHALL take part in a call only when its content entered that call through the owner-activated loading gate: its agent instructions, a skill body actually loaded into context, or an extension actually invoked. Being listed in an index SHALL NOT count. The call's credited time SHALL be split equally among the piece versions that took part.

#### Scenario: A sleeping tool earns no extra time
- **WHEN** a call's model rounds took 20 seconds and its tools slept for 3600 seconds, with the ratio set to 1.0
- **THEN** the call is credited 40 seconds

#### Scenario: A listed but unused skill takes no share
- **WHEN** another piece's skill appears in the skill index but its body is never loaded during the call
- **THEN** that piece earns no share of the call's credited time

#### Scenario: Pending approval earns nothing
- **WHEN** an activity waits two days in `waiting_on_you` before resuming
- **THEN** none of the waiting time is credited

### Requirement: Each paid account's pool is split across third-party pieces by that account's own run-time shares

For each paid account and closed month, the platform SHALL compute the account's pool as the founder-set share percentage of that account's net collected revenue. It SHALL allocate to each eligible third-party piece version the pool multiplied by that piece's credited time over the account's total credited time across all of its agent work. It SHALL keep the unallocated remainder as platform revenue rather than redistributing it among pieces. No allocation SHALL draw on any other account's revenue. Amounts SHALL be integers in micro-USD, split by largest remainder, so that each account's allocations plus its remainder equal its pool exactly.

#### Scenario: Own work dilutes the payable share
- **WHEN** a paid account with a 3,824,000 micro-USD pool spends 10 credited hours, 3.5 of them on creator A's pieces, 0.5 on creator B's, and 6 on its own unpublished harness
- **THEN** A is allocated 1,338,400 micro-USD, B is allocated 191,200 micro-USD, and 2,294,400 remain with the platform

#### Scenario: A sock puppet cannot profit
- **WHEN** an author pays for a second account and spends all of its agent time on their own pieces
- **THEN** the author's allocation from that account is at most the share percentage of what that account paid net of fees, which is less than what it paid

#### Scenario: A free account contributes nothing and is measured the same way
- **WHEN** a free account runs a creator's piece
- **THEN** its usage is recorded and counted in the creator's metrics exactly as a paid account's is, and it allocates nothing because it collected no revenue

### Requirement: The pool comes only from matured net revenue

The platform SHALL derive net revenue from Stripe events it ingests read-only and idempotently by event id: collected invoice amounts less processor fees, refunds and lost disputes, with taxes excluded. A month SHALL close deterministically after a founder-set grace period and SHALL write an immutable allocation set with a hash of its inputs. A month's allocations SHALL become payable only after a founder-set maturity period has passed without a dispute. A failed conservation check SHALL abort the close with nothing written. A refund or dispute that lands after its month closes SHALL produce negative adjustments in the same proportions as that account's allocations for the month. Those adjustments SHALL be netted against the affected creators' unpaid and future balances, and SHALL never be reclaimed on-chain.

#### Scenario: A close is re-runnable and identical
- **WHEN** the close for a month is run twice over the same usage records and revenue events
- **THEN** both runs produce the same allocation set and input hash

#### Scenario: A late chargeback becomes an adjustment
- **WHEN** a paid account's March payment is disputed and lost in June, after March closed
- **THEN** each creator March paid from that account receives a negative adjustment proportional to their March allocation, and no on-chain transfer is reversed

#### Scenario: Immature revenue is not payable
- **WHEN** a payout run is prepared before a month's maturity period has passed
- **THEN** that month's allocations are excluded from the run and stay maturing

### Requirement: Creator balances are an append-only US-dollar ledger

The platform SHALL keep creator earnings as append-only, integer micro-USD balance entries (accrual, adjustment, payout), each with a status of shadow, maturing, payable or paid. A balance SHALL always equal the sum of its entries. Balances below the founder-set minimum payout SHALL roll over without expiry. The ledger and every creator-facing view SHALL show amounts in US dollars, and SHALL NOT show a token price, a token-denominated projection, or a promise of token value.

#### Scenario: A small balance rolls over
- **WHEN** a creator's payable balance is below the minimum payout at a payout run
- **THEN** no line is created for them and the balance carries into the next run unchanged

### Requirement: No payout moves without the founder signing that month's batch

The platform SHALL hold payout funds only in a smart-account treasury whose every transfer and swap requires the founder's signature, and SHALL install no allowance or spending-limit module on it. A platform-held proposer key SHALL only propose transactions, and a platform-held gas key SHALL only submit transactions the owners have already signed, holding no more than a founder-set gas float. Both keys SHALL live in the secrets vault, never in plaintext. Each monthly run SHALL present the founder with the close id, the ledger hash, every creator line, flagged and held lines, the route, the input amount, the minimum output and the quoted price impact before signing. Payouts SHALL stay disabled while `TINYASSETS_CREATOR_PAYOUTS` is off, which SHALL be the default. Accrual and metrics SHALL continue while payouts are disabled.

#### Scenario: A compromised platform key cannot spend
- **WHEN** an attacker obtains the proposer and gas keys
- **THEN** they can propose transactions and spend the gas float, and cannot move any treasury token

#### Scenario: Payouts off still accrues
- **WHEN** the payout flag is off at month end
- **THEN** the month closes and balances accrue, and no transaction is proposed

### Requirement: Swaps respect price-impact and minimum-output limits, and shortfalls roll over

A payout run SHALL buy the token in one batched purchase per run, split into tranches. Each tranche SHALL have its price impact bounded by a founder-set cap and its output bounded by a minimum enforced on-chain. The run SHALL NOT place an order without an output floor. When the limits stop a purchase short, every creator line in the run SHALL receive tokens pro rata to its dollar amount, and the unbought dollar amount SHALL stay owed and roll to the next run. Each line SHALL be marked paid only from its confirmed on-chain transfer, and a replacement batch SHALL reuse the original's treasury nonce so that both can never execute.

#### Scenario: A thin pool fills half the run
- **WHEN** the impact cap allows buying only half of a run's dollar total
- **THEN** each creator receives tokens for half of their line, and the other half stays owed in dollars for the next run

### Requirement: A payout address is bound by a signed ownership proof and changes only after cooling off

The platform SHALL accept a creator payout address only with a single-use EIP-712 typed-data signature over the purpose, an opaque account digest, the address, the chain id, a nonce and an expiry, verified by signature recovery, EIP-1271 or ERC-6492 as the wallet type requires. It SHALL screen the address against sanctions lists before every run. A changed address SHALL receive nothing until a founder-set cooling-off period has passed and the account's sign-in email has been notified. During that period, lines due to the creator SHALL wait rather than go to the previous or an unverified address.

#### Scenario: An address change waits out the cooling-off period
- **WHEN** a creator binds a new address two days before a payout run with a seven-day cooling-off period
- **THEN** their line in that run waits, and the account's sign-in email is notified of the change

#### Scenario: A replayed proof is refused
- **WHEN** a previously used ownership signature is submitted again
- **THEN** the binding is refused because its nonce is spent

### Requirement: Creator metrics are aggregates suppressed below k distinct accounts

The platform SHALL show creators metrics only for their own pieces, aggregated over the accounts that ran them: active and paid-active accounts, credited run time, sessions, retention by activation cohort, churn, version adoption, the import-to-activation funnel and dollar earnings. It SHALL suppress any cell, cohort or version breakdown backed by fewer than a founder-set k distinct accounts, and SHALL fold small versions into an aggregate bucket rather than show them separately. Time SHALL be no finer than one week. No creator view SHALL contain an account or universe identity, conversation, file, prompt or effect content. Creator metrics and earnings SHALL be owner-door projections, complete and unbounded for the owner, and SHALL NOT add an MCP handle.

#### Scenario: A small cohort is suppressed
- **WHEN** a piece was run by 4 distinct accounts in a week and k is 10
- **THEN** the creator's weekly active-account cell shows suppressed, not 4

#### Scenario: No identity is ever shown
- **WHEN** a creator reads every metric view for their piece
- **THEN** no view contains an account id, universe id, email, content or a timestamp finer than one week

### Requirement: A deleting payer's usage and allocation detail is erased while closed creator totals survive

When an account is deleted, the platform SHALL erase that account's usage records, revenue events and per-account allocation detail in the same deletion. It SHALL keep closed creator balance entries and payout records, which carry no payer identity. Those retained entries SHALL be listed among the items account deletion discloses as retained. A creator account's own deletion SHALL stop further payouts to it, and the fate of its remaining balance SHALL follow the founder's recorded decision.

#### Scenario: Payer erasure keeps the creator's earnings
- **WHEN** a paid account that ran creator A's piece last quarter deletes itself
- **THEN** its usage records and allocation rows are gone, and creator A's closed balance entries from that quarter remain unchanged

### Requirement: Mainnet payout stays unreachable until the real-currency phase is opened

The platform SHALL restrict payout chains to an allowlist that contains only Base Sepolia (chain id 84532) until the founder explicitly opens the real-currency phase in a recorded change. That change SHALL update the token-boundary note and the legal page together. Before that change, no code path SHALL propose, sign or submit a mainnet transaction.

#### Scenario: A mainnet payout is refused before the phase opens
- **WHEN** a payout run is configured with Base mainnet (chain id 8453) while the allowlist holds only 84532
- **THEN** the run is refused before any proposal is made
