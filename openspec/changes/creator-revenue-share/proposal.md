# Creator revenue share: paid users' subscriptions pay the people whose command-center pieces they run

## Why

Founder idea, 2026-10-01 (verbatim):

> "if a subscription paying user is using a command center you designed and
> published then you should get some undecided portion of that paid to you in
> tiny the currency on basechain mentioned in the websites fineprint. the
> platform should purchase those tokens from a dex aggregator on basechain and
> send them monthly. if paid users use a combination of command center pieces
> published by others then its distributed accordingly to percent of command
> center total combined run time that month. if users are using things you have
> published you can see some of those metrics that startups like to have on
> their users"

Sharing whole command centers is now designed (`universe-agent-harness` §4.15,
§4.17, slices D9 and D11). Without a revenue share, publishing a good command
center earns its author nothing, while the subscriber who runs it pays only the
platform. This change makes publishing pay, using money the platform already
collects. Paid users do not pay extra.

This is **money**, and it touches **storage shape** and **authority**, so
AGENTS.md requires a proposal and design before any code. Nothing here moves
money, creates a wallet or signs a transaction. Paying out mainnet `Destiny
(tiny)` also stays behind the 2026-04-29 boundary
(`docs/design-notes/2026-04-29-token-naming-and-test-currency.md`): the roadmap
touches only `test tiny` on Base Sepolia until the founder explicitly opens the
real-currency phase. The legal questions in design §9 must be answered before
that happens.

The old paid-market code is being deleted as dead code (#4209). This change
starts fresh and does not bring it back.

> **Founder decision, 2026-10-04: payouts are DAILY.** "If paid users are using
> a command center you published then you get paid in tiny daily." This
> supersedes "send them monthly" above. The design must state how daily accrual
> settles on-chain (gas and Safe-signing cost per batch), whether by a daily batch
> or daily accrual with a settlement threshold, and still avoid any projected
> TINY value in the ledger. Command centers' recipients choose per installed copy
> between opt-in updates and automatic updates (`command-center-recipient-updates`).

## What Changes

1. **A payable piece** is one immutable published version of a command-center
   bundle (the D9 manifest carried by a `universe-custom-agents` public
   definition), grouped under a stable bundle identity.
2. **Credited agent time** is recorded once per agent call, at the seat. The
   seat is already the platform's single definition of "one agent call" on every
   execution path. When a seat is released, it leaves one usage record: the
   inference time and capped tool time of that call, plus the pieces that took
   part in it. No second meter is added anywhere else. Waiting, pending approval,
   failed calls and sleeping tools earn nothing.
3. **Revenue is split per user.** Each paid account's net revenue for the month,
   times the founder-set share, is divided among the third-party pieces it ran,
   in proportion to each piece's share of that account's total credited time.
   Self-use, platform-owned pieces and unpublished work count toward the total
   but pay nobody. Under this rule no account, or ring of accounts, can get back
   more than it paid.
4. **The pool comes from collected revenue.** It is the founder's share
   percentage (undecided; we recommend starting at 20% within a 10–30% range)
   applied to Stripe revenue actually collected, net of Stripe fees, refunds and
   chargebacks. Each month closes on a fixed schedule. Revenue becomes payable
   only after it matures (it has to outlast the window in which a stolen card
   can be disputed). There is a minimum payout, and anything below it rolls over.
5. **The ledger is in US dollars and has no payout first.** Earnings are an
   append-only, integer, conservation-exact obligation in micro-USD. The amount
   of TINY a creator receives is set by the price actually achieved in that
   month's purchase. The ledger never shows a TINY price or a projected value.
6. **The payout rail is designed but off.** The design covers buying TINY on
   Base through a DEX aggregator or an intent-based route, with limits on price
   impact and minimum output, and partial fills rolling over. Payouts go out in
   one batched transaction from a Safe smart account, and **the founder signs
   every monthly batch**. Platform keys can propose a batch and pay gas, but
   cannot spend. Creators bind a wallet by signing a typed message (EIP-712 for
   ordinary wallets, EIP-1271/6492 for smart-contract wallets), and a changed
   address waits out a cooling-off period. There is an append-only audit log and
   a kill switch.
7. **Creator metrics are aggregated and private.** Creators see active users,
   paid active users, retention cohorts, credited run time, sessions, version
   adoption, the funnel from import to activation, churn and earnings. Any cell
   with fewer than k distinct accounts is suppressed (k is a founder parameter,
   default 10). No identities, universe ids, content or fine-grained timestamps
   are ever shown.
8. **Phasing.** Phase A covers attribution, the revenue ingest, the monthly
   close into a shadow ledger, and metrics, with no payout. Phase B is a full
   rehearsal of the payout rail on Base Sepolia with `test tiny`. Phase C is
   mainnet, which waits on counsel's answers and on the founder opening the
   real-currency phase.

## Capabilities

### New Capabilities

- `creator-revenue-share`: piece identity, credited agent time, per-user
  allocation, the revenue pool, the USD ledger, the payout rail and its custody,
  wallet binding, creator metrics, payer erasure, and the mainnet gate.

### Modified Capabilities

- `universe-seats`: a released seat leaves one usage record of credited agent
  time and participating pieces. Seat admission is unchanged.

## Impact

- **Storage (new shapes, built in Phase A):** the seat usage record, a revenue
  ledger fed from Stripe events, the monthly allocation and creator-balance
  ledger, and the metrics rollups. Payout lines and the audit log come in
  Phase B. All of these are platform-owned records, kept outside every
  agent-controlled environment (`universe-agent-harness` §4.16).
- **Depends on** D9 (bundle manifest, stable bundle identity, activation records)
  so that we know which pieces took part in a call. Phase A cannot attribute
  anything until D9 ships.
- **Account deletion:** a deleting payer's usage and allocation detail is swept.
  Closed creator totals are kept with no payer identity, which adds a retained
  item to `account-deletion`'s list. That delta lands with Phase A.
- **Privacy notice:** creators would see aggregates of usage, which is a new
  purpose. The founder approves the copy before Phase A goes live.
- **No MCP surface change.** Metrics and earnings are owner-door projections,
  and the agent reads them the same way it reads activities.
- **All accounts, one code path.** Measurement, metrics and creator eligibility
  are identical for free and paid accounts. The only difference is that a free
  account contributes no revenue, because it pays none.
- **Not the compute market.** This shares subscription revenue with authors of
  published designs. It does not lend compute, which stays undesigned
  (memory `compute-market-emerges-later`).
