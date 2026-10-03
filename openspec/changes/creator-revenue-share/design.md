# Design: creator revenue share

Status: **draft for founder decision.** Nothing in this design moves money,
creates a wallet or signs a transaction. Every number marked *founder parameter*
is a recommendation, not a decision. Legal questions (§9) are questions, not
conclusions.

## 0. Ground truth this design starts from (origin/main ad484628, 2026-10-01)

- **Revenue.** The paid plan costs $20/month and is sold only on the web,
  through Stripe Checkout (`tinyassets/billing/stripe_adapter.py`). Durable
  billing state is the tier and nothing else
  (`tinyassets/storage/subscription_state.py`: "the ONLY durable state the
  billing path needs"). There is **no revenue ledger**: Stripe holds invoices,
  refunds and disputes. The Android shell sells nothing, because Play policy
  requires Play Billing for in-app digital subscriptions (`app.html` around
  `renderPlan`, `docs/ops/google-play-launch.md` §8). Today, then, **all revenue
  is Stripe web revenue**, and no Play Billing revenue exists.
- **Account.** A universe's owning account comes from `universe_owner.owner_of`,
  and its tier from `tier_of`. Seats and storage already share this one resolver.
- **Agent calls.** `universe-seats`: every agent call takes one seat where the
  model call happens. That covers the chat turn (`converse`), the agent node
  executor (`graph_compiler`) and the automation/wake worker. A blocking nested
  call re-enters its parent's seat, and an activity holds one seat while it
  runs and releases it in `waiting_on_you`. The seat row (`account_seats`) is
  **live only**: `release` deletes it, so nothing durable records how long
  agent work ran.
- **Turn journal.** `agent_turns` / `agent_turn_rounds` hold chat-turn rounds
  and `cost_microusd`, but no per-round duration. They cover chat turns, not
  graph agent nodes.
- **Published pieces.** A `universe-custom-agents` public definition is an
  immutable component composition, with a fingerprint, an author id, and remix
  lineage that carries declared credit shares in `[0,1]`. D9
  (`universe-agent-harness` §4.15) makes a versioned bundle manifest ride on
  that carrier, with an owner activation record as the only gate that lets
  imported content load. There is **no stable bundle identity across versions**
  yet, because every evolution is a new definition.
- **Attribution math.** `tinyassets/attribution/calc.py` already provides
  conservation-exact largest-remainder splitting with a refusal for unattributed
  payouts (`evaluation-outcomes-and-attribution`). It is not part of #4209's
  deletion.
- **Token.** `Destiny (tiny)` is an ERC-20 on Base,
  `0x0BB570E30f0b3C5D909C08e3316Dade9C1Dc7fE0`
  (`WebSite/site-react/lib/token-info.json`). It is reference-only: the legal
  page says TinyAssets does not pay it today, and the 2026-04-29 decision limits
  the roadmap to `test tiny` on Base Sepolia until the real-currency phase is
  explicitly opened.

## 1. Constraints this design must keep

| Source | Constraint | How it is kept |
|---|---|---|
| AGENTS / PLAN | Money gets a proposal and design before code; "wrong money" is a floor item | This change, conservation-exact integer ledger, founder signs every batch |
| PLAN private by default | Nothing about a user reaches another user without the owner's choice | Creators see k-suppressed aggregates only; never identities or content |
| Memory `all-accounts-one-code-path` | No tier predicate in behaviour paths | Measurement, metrics and creator eligibility are identical for every account. Tier enters only as "revenue collected", which is zero for free accounts |
| Memory `the-floor-is-cross-user-only` | Platform guards exist to protect *other* users | The anti-gaming rules protect other creators and the platform's money, not the owner from themself |
| Memory `usage-limits-are-storage-and-seats` | No new rate meters | The usage record measures; it never limits or refuses |
| Hard Rule 4 / "no autonomous treasury spend" | Treasury spend needs the founder | Platform keys can propose and pay gas, never spend (§7) |
| Secrets vault-first | No plaintext keys | Proposer key, gas key and aggregator API keys live in the vault; the founder's signer is a hardware wallet the platform never touches |
| 2026-04-29 boundary | Mainnet TINY only after an explicit phase opening | Phase C gate (§11), flag default off, chain allowlist starts at Base Sepolia only |

## 2. Attribution

### 2.1 What a piece is

A **piece** is one immutable published version: a `universe-custom-agents`
public definition whose content is a D9 bundle manifest (an agent's or a
roster's harness: instructions, skills, extensions, settings, layouts).
- **Piece version:** the definition id. It is immutable, fingerprinted, and
  carries its `author_id`.
- **Bundle identity:** D9's manifest must carry a stable `bundle_id`, minted by
  the author's first publish and reused by that author's later versions, with a
  monotone `version`. Only the original author may publish under a
  `bundle_id`. A remix by someone else gets a new `bundle_id`, with lineage
  credit to its parents. **This is a requirement on D9**, which has no stable
  identity today.
- **Payout** attributes to the piece version, credited to its author. Metrics
  roll up by `bundle_id`, so a creator sees one piece across versions.
- **Not pieces:** a user's own unpublished harness; the platform's published
  starter template (`universe-agent-harness` §4.14, owned by a platform account);
  pieces whose author is the paying account.

### 2.2 Which pieces took part in an agent call

D9's activation record is already the single gate through which imported
content reaches a call: "Only activated content enters extension loading, the
skill index or trusted prompt assembly." The loader at that gate tags the
current seat with each activated piece version whose content **entered the
call**:
- the bound agent's own definition, since its `AGENTS.md` always enters;
- a skill from another piece, only when its `SKILL.md` body was actually loaded
  into context (being listed in the skill index does not count);
- an extension from another piece, only when it was actually invoked;
- a layout-only piece (UI), which never enters an agent call and so earns no
  run time (founder question F8).

A blocking nested call re-enters its parent's seat. Its pieces join the same
seat's set. An activity has its own seat, so delegated work is attributed to
the specialist's pieces.

### 2.3 Credited agent time: "useful agent work", defined

Founder: run time should mean useful agent work. Raw wall time held is not
that: a tool can `sleep`, and an approval can sit pending. **Credited time
for one seat-held call** is:

```
credited = I + min(T, r * I)
  I = inference seconds: the wall time of each model round that returned a
      valid response (the executor already brackets the provider call)
  T = tool seconds: the wall time of tool executions inside the call
  r = tool-time ratio cap (founder parameter, default 1.0)
```

- **Earns nothing:** time waiting for a seat (no seat is held); `waiting_on_you`
  (the seat is released); rounds that errored, were refused or returned nothing
  (I = 0); calls refused by authority before any model round.
- **Why cap tools by a ratio rather than a constant:** a piece that loops
  `sleep 3600` would otherwise earn an hour per round. Under the ratio, every
  credited tool second needs a matching inference second, and inference runs on
  the **user's own compute** (PLAN: the platform has no LLM). A piece that
  inflates inference burns the user's own tokens and quota. The user sees this
  per piece (§2.5) and removes the piece. So the market itself is the check, and
  the platform does not have to judge what counts as quality.
- **Scheduled automations and idle research count.** The owner enabled them,
  and they ran on the owner's compute.
- **Measured once.** Seat release writes the usage record: account, universe,
  seat class, kind, run or turn id, I, T, credited time, outcome, piece-version
  set, and the UTC month. That is the only meter. Chat turns, graph agent nodes,
  automations, wakes and activities all pass through it, because all of them
  take seats (`universe-seats`). Adding a timer anywhere else would create two
  definitions of one fact.

### 2.4 Allocation: per user, by share of that user's combined run time

For each paid account `u` and closed month `m`:

```
P(u,m)    = share% * net_revenue(u,m)              (the account's payable pool)
C(u,m)    = total credited time of u in m          (ALL its agent work)
c(u,p,m)  = credited time of u's calls on piece p  (split equally across the
             pieces in each call's set)
alloc(p)  = P(u,m) * c(u,p,m) / C(u,m)              for each eligible third-party p
remainder = P(u,m) - sum(alloc)                     stays with the platform
```

- **Equal split within a call** is simple and auditable. A call that used the
  agent's own piece plus two skills from other pieces credits a third of its time
  to each.
- **The denominator is all of the account's work**, including its own and
  unpublished work. A user who spends 90% of their agent time on their own
  harness pays out 10% of their pool. This is the founder's "percent of command
  center total combined run time."
- **The remainder stays with the platform.** It is not redistributed among
  third-party pieces. Redistributing it would let a piece used for 1% of an
  account's time capture that account's whole pool.
- **Lineage pass-through (founder question F5).** Recommendation: honour the
  remixer's own declared, verified component credit shares
  (`universe-custom-agents` lineage), splitting with the existing
  `attribution/calc.py` largest-remainder primitive at fee 0. The remixer chose
  those shares, which is in the spirit of the commons. Unresolved external
  origin earns no credit, as already specified.
- **Integer math.** Amounts are in micro-USD, and the largest remainder runs per
  account, so each account's allocations sum exactly to P(u,m) minus the
  remainder. No residue is created or lost.

**Worked example.** One paid account, $20 collected, Stripe fee $0.88, net
$19.12, share 20%, so P = $3.824. Its month: 10 h total. 6 h on its own harness,
3 h on creator A's agent, and 1 h of calls that used A's agent plus B's skill.
A gets 3 h + 0.5 h and B gets 0.5 h. A receives $3.824 × 3.5/10 = $1.3384, B
receives $0.1912, and the platform keeps $2.2944.

### 2.5 Anti-gaming

| Attack | Defence | Residual |
|---|---|---|
| **Self-use**: an author runs their own piece | Pieces authored by the paying account are ineligible (identity is the definition's `author_id` against the payer from `universe_owner`) | An author with a second paid account is a sock puppet (next row) |
| **Sock-puppet paid accounts** | The allocation is per user: an account can return at most `share% × its own net revenue`, which is always less than what it paid. A sock puppet loses `(1 − share%) × net + fees` every month | None while share% < 100%. This is the main reason to allocate per user rather than pro-rata over one global pool |
| **Stolen cards** used to buy sock-puppet accounts and cash out irreversible tokens | **Maturity:** a month's revenue is payable only after it outlasts the dispute window (founder parameter, recommended M+2 close, about 60 days, with later disputes netted as adjustments). Stripe Radar's fraud outcome on the payment excludes it. A new creator's first payout is held for founder review. Payment-fingerprint links between payer and creator are flagged for review (§5.4) | Card disputes can arrive later than 60 days; those become negative adjustments against future earnings (§5.3), and the platform absorbs any loss that cannot be recovered |
| **Idle loops** inflating a piece's share of a legitimate user's pool | Credited time counts only inference plus ratio-capped tool time. The inference runs on the user's own compute, and the user sees each piece's share of their agent time and cost in their own app (a complete owner-door read) | A piece can still shift share among pieces on one account by using more of the user's tokens. That is visible and self-defeating |
| **Collusion rings** of real payers using each other's pieces | Same bound as sock puppets: each payer gets back less than it paid | None |
| **Pieces stuffed into every call** (a tiny skill that loads everywhere) | Only content that actually entered the call counts (§2.2). Users activate imports explicitly | A popular, genuinely useful skill earns. That is intended |
| **Version churn** to reset or inflate metrics | Payout attributes to versions; metrics roll up by `bundle_id` | — |
| **Account takeover redirecting payouts** | Changing an address requires a fresh signed proof, a cooling-off period, and a notice to the account (§8) | — |

## 3. The pool

- **Share percentage: founder parameter F1, undecided.** Recommended default
  **20%**, range **10–30%**. The reasoning:
  - The subscription buys platform capacity (storage and seats). Users bring
    their own compute. So a published piece is a complement to the product, not
    the product itself. App stores (developers keep 70–85%) and Spotify (rights
    holders get about two thirds) pay most of the revenue out because there the
    content *is* the product. That benchmark does not transfer here.
  - The platform's own costs come out of the same $20: hosting and storage,
    Stripe (about 4.4%), payout gas and aggregator fees, compliance work, and
    price impact on a thin token.
  - **Start low.** Raising the percentage later is a gift, and cutting it is a
    broken promise.
  - At 20%, a creator earns at most about $3.82 a month per paid user who runs
    only their pieces.
- **Net revenue per account per month:** Stripe `invoice.paid` amounts collected
  in month m, minus Stripe's balance-transaction fee, minus refunds and lost
  disputes in m. Coupons and trials collect less or nothing and contribute
  accordingly. Taxes Stripe collects (Stripe Tax) are excluded.
- **Monthly close.** Calendar month, UTC. The close for m runs once the month
  ends plus a grace period for late-settling invoices (founder parameter,
  recommended 7 days). The close is deterministic and re-runnable from the
  usage records and revenue ledger, and it writes an immutable allocation set
  with an input hash.
- **Maturity (founder parameter F3).** Allocations from month m become
  **payable** at the close of m+2 (recommended), unless a dispute has landed in
  the meantime. Disputes that arrive later become adjustments (§5.3).
- **Minimum payout (founder parameter F4).** Recommended **$10** equivalent per
  creator per payout run. Gas on Base is fractions of a cent per transfer. The
  threshold exists to bound per-payee compliance overhead (screening, tax forms,
  §9), not gas. Anything below it **rolls over** with no expiry. What happens to
  a balance that is never claimed, or that belongs to a deleted creator account,
  is a legal question (unclaimed property, §9 Q9).

## 4. Phase A first: a shadow ledger in USD

**Recommendation: denominate the ledger in micro-USD, not "TINY-equivalent"
units.** The obligation comes from dollars collected, so a dollar ledger is
exact, has no oracle, and carries no price risk. A "TINY-equivalent" ledger
needs a price to accrue at. It turns every month into an implied promise about
TINY's value, and it is the token-promotion framing that §9 Q1 asks counsel
about. The TINY a creator receives is whatever their dollar amount buys at the
month's realized purchase price (§6). The app never shows a TINY price, a
projection, or "earn crypto" wording (founder question F9).

In Phase A, creators see "earned this month (shadow, not payable yet)" in
dollars. The shadow ledger proves the attribution and close against real usage
before any money is involved.

## 5. Ledgers

### 5.1 Records (new storage shapes, platform-owned, outside agent reach)

| Record | Key | Holds | Phase |
|---|---|---|---|
| `seat_usage` | seat id | account, universe, class, kind, run/turn, I, T, credited, outcome, piece versions, month | A |
| `revenue_events` | Stripe event id (idempotent) | account, kind (paid/refund/dispute/fee), micro-USD, Stripe ids, month | A |
| `allocations` | (month, account, piece version) | micro-USD, input hash, close id | A |
| `creator_balance_entries` | append-only | creator, month, kind (accrual/adjustment/payout), micro-USD, status (shadow/maturing/payable/paid) | A |
| `payout_wallets` | creator | address, chain id, proof, bound_at, cooling_until, screening result | B |
| `payout_runs` / `payout_lines` | run id / (run, creator) | ledger hash, proposal, signer, tx hashes, realized price, TINY amounts, reconciliation | B |

Revenue ingest is **read-only from Stripe**: webhook events plus a nightly
reconciliation against Stripe's list API, keyed by event id. It extends the
existing billing webhook. It does not charge, refund or create anything.

### 5.2 Conservation

Every close checks the following, and refuses loudly on any failure:
- for each account, `sum(alloc) + remainder = P(u,m)`;
- `sum(P) = share% × sum(net)`, with integer floor rounding recorded;
- creator balances equal the sum of their entries.

A failed close writes nothing, so there is never a partial month.

### 5.3 Adjustments after a close

A refund or dispute that lands after month m has closed creates negative
adjustments. They are spread over the pieces that account's m allocation paid,
in the same proportions, and charged against those creators' unpaid balances
first, then against future accruals. A creator balance may go negative and nets
against future earnings. **Nothing is ever clawed back on-chain**, and a
negative balance is never turned into a debt demand. The platform absorbs what
cannot be netted.

### 5.4 Fraud review signals (flag, never auto-punish)

- Stripe card fingerprint shared between a payer and the piece's creator's own
  billing.
- An account whose credited time goes almost entirely to one young piece.
- Clusters of new paid accounts activating the same piece.

Flags hold the affected lines for founder review at the payout approval. They
never change the ledger silently.

## 6. Payout rail (Phase B on Base Sepolia, Phase C on Base mainnet)

### 6.1 First measure the market (read-only, Phase B task)

TINY's on-chain liquidity on Base is **unknown to this design**. Before any
number below means anything, measure (read-only, no transaction):
- which pools hold TINY (Uniswap v2/v3/v4, Aerodrome classic or Slipstream, and
  others), their TVL, and the price impact of a $100, $1k and $5k USDC buy;
- quotes from each aggregator for those sizes.

Quoting is free and moves nothing. If the depth makes a whole month's payout
impossible to buy within the impact cap, the share percentage, the cadence or
the asset itself becomes a founder decision. TINY would not simply be bought
anyway.

### 6.2 Routes compared (verify each claim at Phase B; APIs change)

| Route | Kind | Fit for a thin token bought from a multisig |
|---|---|---|
| **0x Swap API v2** | Aggregator (Permit2 / AllowanceHolder), Base supported, API key | Wide source coverage. The returned calldata goes stale within minutes, so it suits execution right after signing, guarded by on-chain `minBuyAmount` |
| **1inch** (Classic and Fusion) | Aggregator, plus intent orders filled by resolvers | Fusion's signed intent carries a limit price and expiry, which suits multisig latency and resists MEV. Resolvers may not fill a long-tail pair |
| **Odos** | Smart order routing, multi-input | Often strong on long-tail Base pairs. Quote-then-assemble calldata goes stale like 0x's |
| **Velora (ParaSwap)** Market and Delta | Aggregator plus intent | Same split as 1inch: Market for immediate calldata, Delta for signed intents |
| **CoW Protocol** | Batch-auction intents, native Safe app, TWAP orders | Built for multisigs: a pre-signed order with a limit and `validTo`, with MEV protection by design. Base support and TINY fill depth must be verified |
| **Aerodrome direct** | Base's largest native DEX (classic and Slipstream pools), not an aggregator | If TINY's depth sits only here, every aggregator routes here anyway. Direct router calls are the simplest audited path, with no third-party API |

**Recommendation:**
- **Primary:** a limit-order or intent route that the founder signs once, with a
  limit price, an expiry and (where supported) TWAP parts: CoW, 1inch Fusion or
  Velora Delta, whichever actually fills TINY.
- **Fallback:** aggregator calldata fetched at execution, best of 0x, Odos and
  1inch net of gas, with on-chain minimum output.
- **Never:** a market order without a floor.

### 6.3 Price-impact, slippage and MEV limits (founder parameters F6)

- **Price impact** of at most 1% per tranche (recommended), measured against the
  pre-trade mid.
- **Slippage tolerance** of at most 0.5% beyond the quote.
- **Minimum output** enforced on-chain.
- **Split the monthly buy into tranches** across the payout window, ideally one
  signed TWAP order. Randomize timing within the window, so a buyer of a thin
  token is not front-run on a known schedule.
- **Partial fills.** If the caps stop the buy short, every creator in the run
  gets TINY pro rata to their dollar amount, and the unbought dollars stay owed
  and roll to the next run. The ledger stays in dollars, so nothing is lost.
- **MEV.** Base's sequencer has no public mempool, which reduces sandwich risk
  but does not remove it. The design relies on the minimum-output floor and on
  intent routes, not on the sequencer.

### 6.4 One batched buy, then one batched distribution

- **One buy per month, not one per creator.** Per-creator buys multiply fees and
  hit the same thin pool many times. One tranched buy amortizes cost and spreads
  the impact.
- **Distribution:** a Safe MultiSend batch of ERC-20 transfers, approved in the
  same signing session. There is no custom contract to write or audit. Batches
  are chunked to about 200 transfers to stay well within the block gas limit. An
  ERC-20 transfer costs on the order of 50k gas, so on Base the batch costs
  cents. A dedicated batch-transfer contract (Disperse-style) is reconsidered
  only if MultiSend becomes the bottleneck.
- **Who pays for gas, aggregator fees and impact (founder question F7).**
  Recommendation: the platform pays gas and aggregator fees, which is trivial.
  Price impact is inherent in the realized price, so creators share it pro rata.
- **Reconciliation:** each line is marked `paid` only from its on-chain
  `Transfer` event. A failed or expired batch leaves its lines payable. A
  replacement uses the same Safe nonce, so two versions of one batch can never
  both execute.

## 7. Custody, approval and the kill switch

**The treasury holds no hot key with spending power.**

- **Treasury.** A Safe smart account on Base (on Base Sepolia for Phase B). Its
  owner is the founder's hardware wallet. Recommended: 2-of-3, using the
  founder's hardware key, a founder backup key stored separately, and a second
  trusted signer if one exists (founder question F10). Every spend, swap or
  transfer needs the founder's signature. **No allowance or spending-limit
  module is installed**, so the Safe never permits autonomous spend.
- **Proposer key.** Held in the vault (`set -a; source scripts/load_secrets.sh`).
  It is a Safe Transaction Service *delegate*: it can propose a transaction and
  nothing else.
- **Gas key.** An EOA holding at most a small ETH float (founder parameter,
  e.g. 0.05 ETH), with its key in the vault. It only submits transactions the
  owners have already signed. A compromised platform loses at most the float.
- **Funding.** Each month the founder moves exactly the batch's USDC into the
  Safe (a host action). The treasury is not pre-funded with a surplus, which
  bounds what is exposed.
  - Moving fiat to USDC on Base is itself an operational step: Stripe pays out
    to the company bank account, then an exchange or stablecoin account converts
    it. Stripe's own stablecoin products are listed as a question (§9 Q4).
- **The monthly approval screen** (owner door, founder only) shows:
  - the close id and ledger hash;
  - per-creator dollar amounts, flagged lines and new-creator holds;
  - USDC in, the minimum TINY out, the quoted price impact, and the route;
  - the exact Safe transactions.

  The founder signs in their own wallet. The platform cannot sign for them.
- **Kill switch.** `TINYASSETS_CREATOR_PAYOUTS` defaults to off, and while it
  is off no proposal is made. A chain allowlist starts as `{84532}` (Base
  Sepolia), so mainnet stays unreachable until Phase C adds 8453. Beyond that:
  - the founder can revoke the delegate;
  - the founder can always decline to sign;
  - accrual and metrics keep running whether or not payouts are on.
- **Audit log.** Append-only `payout_runs` and `payout_lines`: inputs hash,
  proposal hash, signers, tx hashes, realized price, reconciliation. They are
  retained as financial records.

## 8. Creator opt-in and wallet binding

- **Opting in** is explicit: the creator accepts the creator terms (§9). Accrual
  happens regardless, and payout needs opt-in plus a verified wallet.
- **Ownership proof.** The creator signs an EIP-712 typed message:
  `{purpose: "TinyAssets creator payouts", account: <opaque account digest>,
  address, chainId, nonce, issuedAt, expiresAt}`. An ordinary wallet is checked
  by signature recovery. A smart-contract wallet (common on Base, e.g.
  Coinbase Smart Wallet) is checked with EIP-1271, and an undeployed one with
  ERC-6492. Each nonce is used once.
- **Address change** requires a new proof, a **cooling-off period** (founder
  parameter, recommended 7 days) and a notice to the account's sign-in email.
  Lines due in the meantime wait for the new address and are never sent to an
  unverified one.
- **Screening.** Addresses are checked against sanctions lists before every
  run, and jurisdiction is gated per the legal page's existing (pre-positioned)
  wallet-connect rules. A failed screen holds the creator's lines for the
  founder. Whether creator KYC applies is §9 Q2.

## 9. Open questions for counsel and the founder (NOT decided here)

**Get counsel before Phase C.** None of the following is a legal conclusion.

1. **Paying out in the platform's associated token.** Paying creators, from
   subscription revenue, in a token the site names as its currency could bear
   on whether TINY is treated as a security, given the legal page's existing
   "digital tool/commodity" positioning and its "not yield-bearing, not backed
   by a treasury" statements. The concern sharpens because scheduled
   platform buying, funded by revenue, is steady buy pressure. Questions for
   counsel:
   - whether that reads as an expectation-of-profit narrative for holders;
   - whether founder or insider holdings of TINY create conflict or
     manipulation exposure (blackout windows, disclosure);
   - whether the legal page's "Not currently paid by TinyAssets" and the
     2026-04-29 boundary must be rewritten before any mainnet payout.
2. **Money transmission and licensing.** Whether buying a token and sending it
   to third parties as compensation is money transmission (FinCEN or state
   regimes such as the NY BitLicense), or a crypto-asset service under EU MiCA.
3. **Creator tax reporting.** This means W-9 / W-8BEN collection,
   the US information-reporting threshold for payments to contractors
   (historically $600; reportedly changed for 2026, so confirm), and whether
   payments to non-US creators are royalty-like payments that need
   withholding. Add to that EU DAC7 platform reporting and DAC8 crypto reporting,
   and valuation at fair market value on the payout date.
4. **Stripe.** Whether Stripe's terms or restricted-business list are engaged
   when card revenue funds token purchases distributed to third parties, and
   what to disclose. Alternatively, whether Stripe's stablecoin or Connect
   payouts would be a simpler rail (this would be a founder product change
   away from TINY, not decided here).
5. **Google Play.** Today the Android app sells nothing, and all revenue is
   Stripe web revenue, so Play Billing revenue does not exist to exclude. Open
   questions:
   - whether Play's blockchain-based content and financial-services policies
     restrict showing creator earnings or TINY payouts inside the Play app;
   - whether earnings UI must be hidden in the native shell (as checkout already
     is);
   - what happens if Play Billing is ever added, because Play-collected revenue
     might then have to be excluded from the pool.
6. **Apple** (the legal page names an iOS app). Whether the App Store's
   cryptocurrency rules (which, as we understand them, restrict offering crypto
   for completing tasks) affect showing or earning TINY in the iOS app.
7. **Jurisdictions.** Where payouts may go at all; the OFAC list and the China
   exclusion already on the legal page; age (18+ for wallet connection, already
   pre-positioned).
8. **Privacy.** Whether showing creators aggregated, k-suppressed usage is a
   new processing purpose needing notice or consent under GDPR/CCPA, and
   whether users get an opt-out (founder question F11). The legal page
   currently says the apps run no analytics.
9. **Unclaimed and forfeited balances.** Unclaimed-property (escheatment) rules
   for balances never claimed, and for balances of deleted creator accounts.
10. **Accounting.** Whether creator shares are cost of revenue, and the
    treatment of the TINY held between the buy and the distribution.

## 10. Founder parameters and decisions (recommendations, not decisions)

| # | Decision | Recommendation |
|---|---|---|
| F1 | Revenue-share % | 20% (range 10–30%), start low |
| F2 | Close grace | 7 days after month end |
| F3 | Maturity | Payable at close of m+2; later disputes as adjustments |
| F4 | Minimum payout | $10 equivalent, roll over with no expiry |
| F5 | Lineage pass-through | Honour declared verified component credit shares |
| F6 | Price impact / slippage | ≤1% per tranche impact, ≤0.5% slippage, on-chain minimum output, TWAP tranches, randomized timing |
| F7 | Cost bearer | Platform pays gas and aggregator fees; impact is shared via realized price |
| F8 | Layout-only (UI) pieces | Earn nothing in v1 because they have no run time; revisit with a session-based measure if wanted |
| F9 | TINY display | Dollars in the ledger; no TINY price, no projection, no "earn crypto" copy |
| F10 | Safe signers | 2-of-3 with the founder's hardware key required in practice; no spending modules |
| F11 | Metrics opt-out | Aggregates on by default with k=10; an account setting to exclude yourself from creator metrics (payout attribution unaffected) |
| F12 | Founder's own published pieces | Treated like any creator's, with a public disclosure line, unless counsel advises excluding insiders |
| F13 | Tool-time ratio r | 1.0 |
| F14 | Address cooling-off | 7 days |

## 11. Phasing

- **Phase A: attribution, revenue ingest, shadow ledger, metrics. No money
  moves.** Safe to build once the founder approves this design and D9 ships.
  It covers:
  - the seat usage record and the piece tags at the D9 loader;
  - the read-only Stripe ingest;
  - the monthly close into dollar balances marked `shadow`;
  - the creator metrics and shadow earnings views;
  - the payer-erasure path;
  - the privacy-notice update (the founder approves the copy).

  Proven live with the two test accounts: B runs A's piece, A sees a suppressed
  aggregate and a shadow earnings line.
- **Phase B: rehearse the rail on Base Sepolia with `test tiny`.** Already
  allowed by the 2026-04-29 boundary. It covers:
  - the read-only mainnet liquidity and quote study (§6.1);
  - wallet binding with proofs, cooling-off and screening;
  - a Safe on Base Sepolia (host action: create it and fund gas from a faucet);
  - proposer and gas keys in the vault;
  - the founder approval screen;
  - MultiSend distribution of `test tiny`;
  - reconciliation from chain events.

  Aggregators largely do not serve testnets, so the swap leg is rehearsed
  against a testnet pool, and route selection runs read-only against mainnet
  quotes.
- **Phase C: mainnet.** Requires:
  - counsel's answers to §9 recorded;
  - the founder explicitly opening the real-currency phase (the 2026-04-29 note
    and the legal page are updated in the same change);
  - creator terms published;
  - the chain allowlist gaining 8453;
  - a mainnet Safe, a host action.

  The first mainnet run is small and founder-watched.

## 12. Alternatives considered

- **One global pro-rata pool** (all paid revenue × share%, split by global run
  time). Rejected. It lets a sock-puppet farm or an idle loop capture other
  users' money, and it pays heavy users' favourite pieces out of light users'
  fees.
- **A new run-time counter beside the seat.** Rejected: two definitions of one
  fact, and paths that bypass it. The seat is already where every agent call is
  counted.
- **Accruing in TINY units.** Rejected for Phase A. It needs a price oracle and
  implies a value promise (§4).
- **Per-creator on-chain buys or streaming payments.** Rejected. They multiply
  fees and impact on a thin pool, and they need autonomous spend.
- **A hot wallet with spend limits.** Rejected. "No autonomous treasury spend"
  rules out any key the platform holds being able to spend.
- **Paying from the old paid-market substrate.** It is being deleted (#4209), so
  it is not resurrected.

## 13. Review record

- gpt-6-astra refute focused on gaming, custody and privacy: see the PR comment
  linked from the PR description. Folded in below once it returns.
