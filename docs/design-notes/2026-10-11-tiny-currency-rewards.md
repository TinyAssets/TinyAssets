# Destiny (tiny): what exists, what the site says, and a credits-first shape

Date: 2026-10-11
Status: research note. No code and no site text changed.
This is not legal advice. It records what the repo and the site say, and where a later counsel review has to answer before any payout exists.

The owner's intent, stated for this note: `tiny` is the payout for creator rewards and for compute rewards (hosts lending compute, creators publishing shapes that people use).

## Where this stands

Nothing pays anyone in `tiny` today. The careful public line is on the fine-print page: no paid work market, and no currency moves. The legal page describes a Base Sepolia `test tiny` settlement rail and daemon-host rewards that the code does not run. The low-risk shape is an off-chain ledger of internal credits, denominated in cents of subscription revenue actually collected, with no token balance, no wallet, and no cash-out, until counsel reviews the questions already listed in `openspec/changes/creator-revenue-share/design.md` §9 and the founder opens the 2026-04-29 real-currency phase in the same change as the legal page.

## What the name is (evidence)

`docs/design-notes/2026-04-29-token-naming-and-test-currency.md` is an accepted host decision:

| Term | Meaning in that note |
|---|---|
| `Destiny (tiny)` | Real currency reference. Symbol `tiny`. |
| `test tiny` | The only currency current work may touch, on Base Sepolia. |
| Mainnet `Destiny (tiny)` | Reference-only until a real-currency phase is explicitly opened. Public copy must not say the product pays, stakes, custodies, or governs with it. |

The note cites Base contract `0x0BB570E30f0b3C5D909C08e3316Dade9C1Dc7fE0` as Destiny / TINY, from BaseScan and a Coinbase asset page read on 2026-04-29. **TODO:** those two pages were not re-fetched for this note. The address is what the site still publishes.

There is no Solidity (or other contract source) in this repository. A search for `*.sol` returned nothing.

## What the code actually does (evidence)

| Piece | Where | What it is |
|---|---|---|
| Integer unit | `tinyassets/payments/identifiers.py` `MicroToken`. 1 token = 1_000_000 micros. | An integer type. Not a chain balance. |
| 1% take | `tinyassets/treasury/distribution.py` `PLATFORM_TAKE_BP = 100`. Half of that take is a bounty-pool share, half stays treasury. Amounts are integers. | Pure functions. They do not send a transaction. |
| Settlement ledger | `tinyassets/payments/settlement_ledger.py`. SQLite rows: gross, fee, net, treasury, bounty, an audit log. Idempotent on `settlement_key`. | An off-chain book for gate-bonus and escrow disbursements. The module calls itself the foundation for a money loop, and says market-escrow migration is later. |
| On-chain seam | `tinyassets/payments/settlement_backend.py`. Default backend is `internal` (a local marker, no network). `TINYASSETS_SETTLEMENT_BACKEND=base_sepolia` selects `BaseSepoliaBackend`, whose default client is `MockOnChainClient` (a fake `0xMOCK…` hash, no web3, no network). The token address in that file is Circle testnet USDC on Base Sepolia (`0x036CbD53842c5426634e7929541eC2318f3dCF7e`, chain id 84532), not Destiny and not `test tiny`. | A mock USDC withdrawal. Not a tiny rail. |
| Flag | `openspec/specs/paid-market-economy/spec.md`. `TINYASSETS_PAID_MARKET` defaults off. Escrow lock, release, refund, fund, set-wallet, and withdraw return `not_available` and write nothing. `NodeBidProducer` does not register. Reads stay available. | The money path is inert. |
| Actor hole | Same spec. `_current_actor()` in `tinyassets/api/engine_helpers.py` can fall back to `UNIVERSE_SERVER_USER`. The hardened resolver in `tinyassets/api/permissions.py` is not the money-path authority. | A reason the flag stays off. |
| Escrow still in tree | `tinyassets/api/market.py` gates those actions on the flag. | Node escrow, not creator rewards and not an LLM hour. |
| Testnet wallet address | `prototype/full-platform-v0/treasury_config.toml` publishes `0x3023f144fdaEC0F3E5F75125D55B9BAe3AEEdEb1` on chain 84532, labeled `tinyassets-testnet-treasury-v0`. `SUCCESSION.md` §3 points at a host-local key for that wallet and says moving real money is launch-checklist-blocked. Mainnet address in the toml is empty. | A documented testnet wallet address. Not a token contract, and not wired to `settlement_backend.py`. **TODO:** whether that address holds anything was not checked on chain. |
| NAV / mint fund | `docs/exec-plans/active/2026-07-08-token-architecture.md` describes `tinyassets/paid_market/fund.py`: TINY minted against a pool of assets at NAV. | The package is not in the tree. A search for `paid_market/fund` and `record_fee_inflow` in Python returned nothing. The plan's own §5 says that design is plausibly a security and must not be public without counsel. |
| Creator revenue share | `openspec/changes/creator-revenue-share/`. Proposal 2026-10-01. Founder line 2026-10-04: pay daily in tiny. Tasks for the Sepolia rehearsal and the mainnet phase are unchecked. | Design only. No Python module records credited agent time, a creator balance, or a micro-USD obligation. `docs/concerns/2026-10-04-creator-daily-settlement-design.md` says the proposal's "daily" line and the design's monthly close disagree, and that daily payouts must not be described as implemented. |
| Subscription cash | `tinyassets/billing/stripe_adapter.py` can create a Stripe Checkout session. `tinyassets/usage_policy.py` is the tier table (seats and storage only). | A card rail for the platform's own subscription. The fine-print page says there is no upgrade control on the site yet. **TODO:** whether checkout is charging live customers was not checked. |
| Usage the product already counts | `tinyassets/universe_seats.py` counts concurrent agent calls and queues the overflow. `tinyassets/storage_accounting.py` measures cloud bytes. `universe_seats.release` exists. | Concurrency and storage. Not a creator-time ledger and not a GPU-hour ledger. |

`free-source-pooling` still refuses another owner's model. A community pool that runs one user's prompt on another user's machine is non-compliant with the 2026-10-11 hard requirement in `docs/design-notes/2026-10-11-single-app-dead-limb-inventory.md`. There is no meter of "compute lent to other people," because that workload is not allowed.

## What the site says (evidence)

Footer links: `/fine-print/` and `/legal/` (`WebSite/site-react/components/Footer.tsx`). The economy page redirects to fine print.

**Fine print** (`WebSite/site-react/app/fine-print/page.tsx`), the page that says what is true now:

- Free and premium are the same command center. Premium is described as more allowances. The page says the outside-world daily gate is metered but not switched on, compute is a runaway guard, and storage is a capacity cap.
- "No paid work market. Nothing on this site is an offer to sell anything, and no currency moves." The legal page is where disclosures live.
- "The platform never bills you for a model; your subscription is your own."

**Legal page** (`WebSite/site-react/app/legal/page.tsx`), fed by `lib/legal-info.json` and `lib/token-info.json`. The page header says terms and token disclosures are Draft v0, and counsel review of the page remains pending (`legal-info.json` `review_status`). Operating entity is "TBD pending Wyoming DAO LLC formation" and "treat as unincorporated association at v0."

Sentences on the rendered legal page that describe a rail:

- "daemon-host rewards remain on the Base Sepolia `test tiny` rail until real integration opens."
- Reference contracts are "reference-only" and "TinyAssets takes no action on them today and settles solely on the Base Sepolia `test tiny` rail."
- "No mainnet `tiny` payouts, staking, DAO votes, or treasury flows in current TinyAssets surfaces."
- "`tiny` is also intended to qualify under the mature-blockchain carve-out" of the Digital Asset Market Clarity Act (H.R. 3633), which the same page says has not passed the Senate.
- "Not a security, not an investment contract, not equity, not a debt instrument." Not yield. Not backed by a managed pool. The phrase "Portfolio Backed Currency" is called deprecated.
- "This site does not constitute an offer or solicitation to sell securities."
- KYC: tier-2 daemon hosts "are subject to" identity checks at a lifetime paid-market threshold of USD 1,000 via Sumsub, Persona, or Onfido. "Wallet connections are screened against the OFAC sanctions list in real time." The same section says wallet features are not live and that this text "pre-positions the obligation."
- Geographic block list includes OFAC-comprehensively-sanctioned countries and the PRC. The enforcement sentence names "Wallet-connect screening (Chainalysis or TRM at MCP gateway)."
- Tax: real-token payouts are disabled at v0. Users are responsible for their own tax. The JSON `tax_reporting.us_threshold` says that once payouts are live, hosts at ≥ USD 600/year equivalent get a year-end CSV and the platform does not file 1099-MISC at v0. The rendered tax paragraph does not repeat the CSV sentence. **Evidence:** the CSV line is in the JSON, not in the paragraph read at `legal/page.tsx` under "Tax".
- Footer of the page: "Not legal advice… Substantive provisions are pending review by a US securities attorney with crypto practice."

`token-info.json` phases: "test tiny settlement" is `planned`, "real Destiny integration" is `planned`. The tagline says TinyAssets "currently uses test tiny on Base Sepolia." A PulseChain entry has a `buy` URL. The legal page renders explorer links, not that buy URL. **Evidence:** the buy field is in the JSON; the page map in `legal/page.tsx` renders `d.explorer` only.

`legal-info.json` also holds `token.is_security: false` and a `rewards_model` string ("daemons earn test tiny for completed work that clears outcome gates") and a DAO `vote_scope` item "Which outcome-gate ladders qualify for tiny minting." **Evidence:** those three fields were not found in the rendered JSX. They are still in the file the page imports.

A search of the marketing pages (`page.tsx`, `start`, `build`, `developers`) found no `Destiny` or reward-token pitch. The homepage describes a cloud agent on the user's own Claude or ChatGPT subscription.

## Where the public text and the code disagree (evidence)

| Public sentence | What the code shows |
|---|---|
| Fine print: no currency moves. | Matches the flag-off escrow path and the absence of a creator payout module. |
| Legal page: settles solely on a Base Sepolia `test tiny` rail; daemon-host rewards remain on that rail. | No `test tiny` contract in the repo. The only Base Sepolia backend is a mock USDC transfer, and it is not the default. `token-info.json` marks test-tiny settlement as planned. |
| Legal page: real-time OFAC wallet screening via Chainalysis or TRM; KYC at USD 1,000 via Sumsub, Persona, or Onfido. | Those vendor names appear in the legal JSON, the legal page, an old site note, and the creator-revenue-share counsel list. No Python or site module calls them. The same page says wallets are not live. |
| Legal page: not backed by a managed pool; "Portfolio Backed Currency" is deprecated. | The 2026-07-08 exec plan still describes TINY as a claim on a pool of assets, minted at NAV. That code is not in the tree. The two documents disagree. |
| 2026-04-29 note: do not say the product pays in mainnet Destiny. | The legal page does say mainnet payouts are not current. It does say a test rail is the thing TinyAssets settles on. |

## Risk flags (inference, except where a file is named)

Not a determination. `creator-revenue-share` design §9 already tells the founder to get counsel before mainnet, and lists the questions below. This note adds the mismatch between the legal page and the code, which §9 does not inventory line by line.

- **Securities.** Paying creators, from subscription revenue, in a token the site names as its currency can be read as buyers or holders expecting profit from the platform's efforts. Design §9 question 1 says that, and notes that scheduled platform buying is steady buy pressure. The legal page's "not a security" and the JSON `is_security: false` are the site's own classification. The page also says a securities attorney has not reviewed it. The 2026-07-08 plan says the NAV-fund version is plausibly a security. Inference: do not build that fund, and do not treat the page's "not a security" line as clearance.
- **Money transmission.** Design §9 question 2: buying a token and sending it to third parties as compensation may be money transmission (FinCEN or a state regime such as the NY BitLicense) or a crypto-asset service under EU MiCA. The legal JSON already lists New York BitLicense as "scope review" pending. An internal credit that cannot be withdrawn, sold, or transferred to a wallet is a different object from a token send. Inference: the credit should be non-transferable and non-redeemable until that question is answered.
- **Tax reporting.** Design §9 question 3: W-9 / W-8BEN, the US information-reporting threshold, withholding on non-US creators, EU DAC7 / DAC8, and fair-market value on the payout date. The JSON's "we do not file 1099-MISC at v0" is paired with "payouts are disabled." Inference: that sentence becomes a problem if payouts start and the page is not rewritten in the same change. Confirm the dollar threshold with counsel; the design note already says the historical $600 figure may have changed for 2026.
- **Sanctions and KYC.** The legal page states real-time screening and a KYC threshold as facts, and also says wallets are not live. Inference: a representation that screening happens, while no code screens, is the exposure. Opening a wallet or a payout without building the screen the page already promises is worse. The fine print's "no currency moves" is the statement that matches the software.
- **Consumer-protection and terms.** The legal page is labeled draft, the operating entity is unformed, and it still says that using the site is agreement to those terms, with a liability cap of the lesser of fees paid in 12 months or USD 100, plus a class-action waiver. The page itself says it does not exclude liability that cannot be excluded. Separately, "settles solely on the test tiny rail" and "rewards remain on" that rail describe a payout mechanism the software does not run. Inference: that is a false-statement problem even if `tiny` is never a security. The fine print is the accurate public line. This note does not edit either page.
- **Store rules.** Design §9 questions 5 and 6: Google Play and Apple restrict crypto offered for completing tasks, and the apps are store clients of the hosted service. Inference: earnings and token balances do not belong in those shells.
- **Stripe.** Design §9 question 4: card revenue that funds token purchases for third parties may engage Stripe's restricted-business terms. Inference: a shadow ledger that does not buy tokens does not take that step.

## How rewards would attach, if they are built later (inference)

**Creator rewards** already have a shape that does not need a token. From the creator-revenue-share proposal, not from code:

1. A published command-center piece is identified. Attribution waits on the bundle manifest the proposal calls D9. That manifest was not re-verified in this pass. **TODO.**
2. When a seat is released, one usage record stores that call's credited time and which third-party pieces took part. `universe_seats.release` does not write that record today.
3. The pool is a founder-set share of Stripe revenue actually collected, after fees, refunds, and chargebacks. The proposal recommends 20% inside a 10–30% range and says the number is not decided. Self-use and platform-owned pieces count in the denominator and pay nobody, so an account cannot receive more than it paid.
4. The ledger is integer micro-USD. It does not store a TINY price. Phase A in the proposal is this shadow ledger and creator metrics, with no payout.

That phase is the integration point. Seats are the meter. Storage is not a creator meter. A second meter of "shapes used" would duplicate the seat record the proposal already chose.

The founder's 2026-10-04 "pay daily" line is not a reason to send a daily token transfer. The open concern says to pick daily accrual versus a settlement threshold before any implementation, and not to weaken the founder-signed batch. Accrual can be daily inside the micro-USD ledger. Settlement, if it ever exists, stays a later phase.

**Compute rewards** do not have a compliant meter.

- Lending a machine to other users' prompts puts those prompts on that machine. The 2026-10-11 hard requirement forbids that. A reward for it would pay for a non-compliant design.
- A pool of tasks the user marks public still sends plaintext to a third-party host. The inventory note does not carve that out.
- Capacity used by the same account that owns the machine is bring-your-own-model, not a payout. There is nothing to pay the owner for running their own work.
- A platform-authored health probe contains no user content and is not a product someone pays for.

So compute-pool metering is not an input to `tiny`. If a later design has a workload that never carries user content and the owner still wants to pay for it, that is a new spec (storage, authority, and money). It is not a flag on the escrow path.

## Recommended minimal design

Do this, and do not do the rest. All of it is inference from the evidence above, and it stays behind counsel for anything that leaves the building as money.

1. **Internal credits only.** An append-only ledger of integer micro-USD obligations, filled only from subscription cash the platform has actually collected. Non-transferable. Not redeemable for cash, for `tiny`, or for `test tiny`. No wallet field. No price of TINY. No "earn crypto" string. This is Phase A of `creator-revenue-share`, and it is not built.
2. **No public token and no cash-out.** Do not deploy a `test tiny` contract, do not point `BaseSepoliaBackend` at Destiny, do not flip `TINYASSETS_PAID_MARKET`, and do not revive the NAV mint in the 2026-07-08 plan. Testnet tokens that people can sell or that the site calls "rewards" are still a public claim. The 2026-04-29 boundary stays closed until counsel answers design §9 and the founder opens the phase in a change that updates that note and the legal page together. The proposal's own spec already says a mainnet transaction is forbidden before that change.
3. **Copy, in a later change, not this one.** Make the legal page match the fine print: no currency moves; test-tiny settlement is not live; the Base, PulseChain, and BSC addresses are reference-only and TinyAssets does not settle on them; KYC and OFAC screening are not implemented. Delete or stop rendering `is_security: false` and the "daemons earn test tiny" sentence until counsel writes the replacement. This note does not edit the site.
4. **Creator credits use the seat, when D9 exists.** One usage record per released seat. Unpublished and self-owned pieces pay nothing. Show creators aggregate counts only after the privacy question in design §9.8 is answered. The legal page currently says the apps run no analytics.
5. **Compute rewards stay off.** No host is paid for other users' inference. Own-account GPU use is not a reward program.
6. **Leave the old money code dark.** The 1% MicroToken math and the flag-off escrow ledger are the map of the old node market. They are the wrong rail for creator credits. Building credits does not require turning them on.

## What this note did not do

- No chain read of the Base, PulseChain, BSC, or Sepolia addresses.
- No check that Stripe is charging live customers.
- No counsel review. Design §9 is the question list to send, plus the public-sentence mismatch in the table above.
