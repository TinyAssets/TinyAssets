# Destiny (tiny): wallet payout, platform spend, and what exists today

Date: 2026-10-11
Status: research note. No code and no site text changed.
This is not legal advice. Facts below are labeled evidence. Recommendations and risk readings are labeled inference. Counsel has to answer the questions in the last section before any token moves.

The owner's intent, clarified 2026-10-11: `tiny` is payment for creator work and for compute work. It is a transferable on-chain token. The user holds it in their own wallet (self-custody) and can move it, or they spend it on the platform. The platform should not custody user funds.

## Where this stands

Nothing pays anyone in `tiny` today. **Evidence:** the fine-print page says no currency moves, and no module sends Destiny or `test tiny`. The legal page describes a Base Sepolia `test tiny` rail that the code does not run.

**Inference, the low-risk order.** Accrue what the platform owes as an append-only payable, in cents of subscription cash actually collected. That payable is not a token and not a balance the platform holds for the user. Wallet payout is the settlement that matters. In-app spend does not give a heavy earner anywhere to put the balance. The instrument that does, without issuing a new token, is an existing dollar stablecoin bought with revenue the platform has already collected and sent to a wallet the user controls. Issuing `tiny` stays off that path. The owner cannot afford a lawyer; this note is not a substitute for one, and the staging below is chosen to avoid a new legal object rather than to declare the payout legal.

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

## Chain status (evidence)

| Chain | What the repo and the site say | What the running code does |
|---|---|---|
| Base mainnet, chain id 8453 | `token-info.json`: Destiny `0x0BB570E30f0b3C5D909C08e3316Dade9C1Dc7fE0`, `workflow_status` `reference-only`, plus a second address `0xa446E417c9379863bD279CE715De59a80879bAfE`. The 2026-04-29 note cites BaseScan and a Coinbase asset page for the name Destiny / TINY. **TODO:** those pages were not re-fetched. `treasury_config.toml` mainnet address is empty. | No payout, no signer, no mainnet client. |
| PulseChain | Address `0x92a242f94db176082Df4D386B366f4217ab7fAFd`, reference-only. The JSON has a `buy` URL. The legal page renders the explorer link, not the buy URL. | Nothing. |
| BSC | Address `0x839108AaecB749e8F33cc68bb6D6323F61206322`, marked legacy. | Nothing. |
| Base Sepolia, chain id 84532 | Policy name for current work is `test tiny`. `token-info.json` phase "test tiny settlement" is `planned`. No Sepolia token address is in `token-info.json`. `settlement_backend.py` names Circle testnet USDC `0x036CbD53842c5426634e7929541eC2318f3dCF7e` and talks to a mock client. `treasury_config.toml` publishes wallet `0x3023f144fdaEC0F3E5F75125D55B9BAe3AEEdEb1`. The creator-revenue-share spec allowlist starts as `{84532}` and forbids a mainnet transaction until a recorded phase opening. | The mock USDC path is off unless `TINYASSETS_SETTLEMENT_BACKEND=base_sepolia`. It does not send `tiny`. The Sepolia wallet is not wired to that backend. **TODO:** balances were not read on chain. |

**Inference.** Sending the published Base contract is mainnet money, even if a test happened first on Sepolia. A Sepolia token at a different address can rehearse a transfer. It is not the asset the site and the 2026-04-29 note point at on Base. There is no deployed `test tiny` contract in this repo to rehearse against.

## The product shape the clarification asks for (inference, built on the evidence above)

Two ways a person receives or uses `tiny`, and neither one leaves their tokens in the platform's hands.

1. **Wallet payout.** The platform transfers tokens to an address the user proved they control. After that transfer the user holds a normal transferable token. They can move it with their own wallet. The platform has no key for that address.
2. **Platform spend.** Either the unpaid amount is retired because the user takes a platform charge (seats, storage, a subscription credit), or, once they already hold tokens, they sign a transfer from their wallet to a platform address at the moment they buy something. Tokens that arrive that way belong to the platform. They are not a deposit the user can withdraw.

The creator-revenue-share design already sketches the payout leg, and it is not built. **Evidence**, design §7 and §8: a Safe on Base whose owner is the founder's hardware wallet; no spending-limit module; a proposer key that can propose and cannot spend; a gas key with a small ETH float; the founder signs each batch; the creator proves the address with an EIP-712 message (EIP-1271 / ERC-6492 for a smart wallet); a changed address waits out a cooling-off period; a row is paid only when the chain `Transfer` event is seen. The allowlist starts at Base Sepolia. That design holds platform funds for the length of a batch. It does not hold a user's wallet.

**Inference.** A balance in the app that the user can withdraw later, or tokens sitting in the Safe labeled as a user's balance, is custody. The payable in the ledger is an amount the platform owes. It becomes the user's property when the chain transfer lands, or it disappears when the payable is retired in kind. There is no third state in which the platform is keeping their coins.

## One ledger for payout and for spend (inference)

Yes. One append-only book should be the source of truth. The chain is how a wallet row closes. It is not a second set of books.

A row is an integer micro-USD obligation, the same unit the creator-revenue-share proposal already chose, so the book does not store a TINY price. Settlement is one of:

| Close | What happens | What the platform holds |
|---|---|---|
| `settled_on_chain` | Founder-signed transfer to the proved address. Amount of tokens is the amount the buy actually filled, recorded from the transfer. | Nothing of the user's after the event. |
| `settled_in_kind` | The obligation is applied to a platform charge (more seats, more storage, a subscription credit). No token is bought. | Nothing. |
| Incoming spend | The user already holds tokens and signs a transfer in. A new row records the transfer hash and what it paid for. | The received tokens are platform property, not a user balance. |

`tinyassets/payments/settlement_ledger.py` is a different book: escrow gross, fee, and net, behind the paid-market flag, with a 1% take. **Evidence:** it is the old node-market ledger. **Inference:** creator and compute rows should not be written there. One new ledger, not three (escrow, plus credits, plus a chain balance).

Daily accrual can be a row in this ledger. A daily on-chain send is a separate choice. `docs/concerns/2026-10-04-creator-daily-settlement-design.md` says the proposal's daily line and the design's monthly batch disagree, and that the founder-signed batch stays. **Inference:** accrue daily, settle on the batch the founder can still sign, and do not leave per-user tokens in the Safe between batches.

## Securities and token classification

**Evidence.** The legal page says Destiny is intended to function as a digital tool and/or digital commodity under an April 2026 SEC "Project Crypto" release, and that it is intended to qualify for a mature-blockchain carve-out in the Digital Asset Market Clarity Act (H.R. 3633). The same page says that Act has not passed the Senate, says "not a security," and says a securities attorney has not reviewed the page. `legal-info.json` has `is_security: false`. The 2026-07-08 exec plan says a NAV-mint fund "is very plausibly a security" and must stay internal until counsel signs off. That fund code is absent. Design §9.1 asks whether paying creators in this token, funded by revenue the platform uses to buy it, is an expectation-of-profit narrative.

**Inference.** A transferable token, paid for other people's use of the product, which holders can move and which the platform buys with subscription revenue, is the shape counsel has to classify before the first send. The page's "not a security" line is not that classification. The NAV-fund design conflicts with the page's "not a managed pool" line and should not be the issuance model. Counsel's question is the transferable payment token itself: what it is when the user can hold it and sell it, and what the buy-pressure of platform purchases does to that answer. A Sepolia rehearsal under the same brand is part of the same question if the public site calls it `tiny`.

## Money transmission and custody

**Evidence.** Design §9.2 asks whether buying a token and sending it to third parties is money transmission (FinCEN or a state regime such as the NY BitLicense) or a crypto-asset service under EU MiCA. The legal JSON lists New York BitLicense as pending scope review. No transmitter license, no custody module, and no user-key store exist in the tree. The Safe design keeps spending power on the founder's hardware key.

**Inference.** Self-custody removes the platform's power over tokens after delivery. It does not, by itself, answer whether the act of buying and delivering them is money transmission. Counsel has to say that before mainnet sends. The in-kind close (`settled_in_kind`) delivers no token and so does not take that step. A user-signed spend delivers tokens the user already holds; the platform receives its own property and cannot send it back out as a "withdrawal." Avoid a deposit address, a withdraw button, and a per-user token balance in the Safe.

## Tax reporting

**Evidence.** Design §9.3 lists W-9 / W-8BEN, the US information-reporting threshold (historically $600, and the design says to confirm a 2026 change), withholding on non-US creators, EU DAC7 and DAC8, and fair-market value on the payout date. The legal JSON says real-token payouts are disabled at v0 and that, once they are live, hosts at ≥ USD 600/year equivalent get a CSV and the platform does not file 1099-MISC at v0. The rendered tax paragraph says users are responsible for their own tax and does not repeat the CSV line.

**Inference.** A transfer into a wallet the user controls is the likely moment of receipt, valued at the price the buy actually paid. The platform still may have reporting duties for that payout. Collect tax identity before the first mainnet send, and rewrite the "we do not file" sentence in the same change. Counsel and a tax advisor set the form, the threshold, and withholding. Testnet tokens are a question for them if the brand is the same one that trades, or that the site points at, on mainnet.

## KYC and sanctions

**Evidence.** The legal page says tier-2 hosts are subject to KYC at USD 1,000 lifetime via Sumsub, Persona, or Onfido, and that wallet connections are screened against OFAC in real time via Chainalysis or TRM. The same page says wallets are not live. No code calls those vendors. Design §8 says addresses are checked before every payout run. That check is not built.

**Inference.** Screen the destination address before each send, and hold the payable when the screen fails. After a self-custody transfer the platform cannot pull the tokens back, so the screen has to happen first. Build the screen before describing it as live. Whether KYC is required at $1,000, at the first payout, or at some other line is a counsel question. The page's current sentence is ahead of the software.

## Consumer disclosures

**Evidence.** Terms are Draft v0. The operating entity is unformed. The page says use is agreement, caps liability at the lesser of 12 months of fees or USD 100, and includes a class-action waiver, while also saying it does not exclude liability that cannot be excluded. It says the test rail is how TinyAssets settles. The fine print says no currency moves. Store questions are design §9.5 and §9.6 (Play and Apple limits on crypto for completing tasks).

**Inference.** Before any send, and in a later copy change, the public pages have to say: settlement is not live yet; a future payout goes to a wallet the user controls and the platform cannot recover it; the token can be moved and can lose value; it is not insured; testnet and mainnet are different contracts; the platform does not hold user funds; compute pay exists only for work that does not put someone else's prompts on the host's machine. "Not a security" stays out until counsel writes it. Earnings and token balances stay out of the store shells until §9.5 and §9.6 are answered. This note does not edit the site.

## How the two rewards meet that ledger (inference)

**Creator work.** The proposal's meter still fits. One usage record when a seat is released, naming the published pieces in that call. A founder-set share of collected Stripe revenue, after fees, refunds, and chargebacks, is split by that time. Self-use and platform-owned pieces pay nothing. Attribution waits on the bundle manifest the proposal calls D9. **TODO:** that manifest was not re-verified here. `universe_seats.release` does not write the usage record today. Those rows are the payables. They close later by wallet transfer or in-kind retirement. Seats are the meter. Storage is not a creator meter.

**Compute work.** The token shape does not change the 2026-10-11 prompt rule. A host whose machine runs another user's prompt is not a payable. A public-task pool still sends plaintext to a third-party host. A machine serving its own account is bring-your-own-model, and there is no payout for running one's own work. A future workload that never carries user content would be a new spec before any compute row is written. Until then the ledger has no compute-reward source.

## Sink and demand (owner, 2026-10-11)

Wallet payout is the point of the program. In-app spending is a weak sink. Someone who hosts a lot of compute, or whose published pieces are used a lot, already has a subscription, some storage, and a seat cap. Those are small bills. The rest of what they earned would sit in the app with nothing to buy. **Inference** from that observation and from what the product actually sells.

| Sink the owner named | What exists (evidence) | Does it absorb what was issued? (inference) |
|---|---|---|
| Own subscription | `stripe_adapter.py` can start Checkout. The fine-print page describes premium as the same product with higher allowances, names $20 a month, and says the site has no upgrade control yet. **TODO:** live charges were not checked. | A capped personal bill. Someone who earns more than their own subscription keeps the surplus. The people paid the most are the ones whose work others use, so they are the ones most likely to be over that bill. |
| Cloud storage | The tier table is 2 GiB free and 20 GiB paid (`usage_policy.py`). There is no second product for buying more after that. | One step, then it stops. A host who does not store much never spends it. |
| Concurrent runs | 3 seats free, 8 paid. Over the cap, work waits. It is not a meter the user buys down. | Same shape as storage: one step up, then nothing left to purchase. |
| Buying other creators' shapes | No purchase flow. The creator-revenue-share proposal takes a cut of subscription revenue the platform already collected. It is not a shop where one user pays another in `tiny`. | If it were built as a shop, a purchase moves a balance from buyer to seller. The total owed to creators does not fall. Net publishers still accumulate. |
| Tipping | No tip action in the app or the payment modules. | A tip would move a balance between users. It does not retire supply. |
| Compute purchases | The platform has no model of its own (ADR-006). Another user's machine must not see this user's prompts. No compute shop exists. | There is no sink here today. A market in which hosts are the sellers and also the people with nothing to buy does not clear inside the app. |

**Inference.** In-platform sinks do not balance issuance. Subscription, storage, and seats are the only real things to buy, they are small and capped, and they are already priced in dollars through Stripe. Shapes and tips, if added, recirculate. Compute cannot be sold under the prompt rule. A closed `tiny` balance therefore piles up with the heaviest earners, which is the failure the owner described. A wallet payout in a coin that already spends in the outside economy is what gives that surplus somewhere to go. A wallet payout in a new `tiny` that has no outside market recreates the same pile, one hop later, in the user's wallet.

## Staged payouts, without a new token and without a lawyer on retainer

This is not legal advice. The owner cannot afford counsel. That constraint is a reason to avoid inventing a token, a DAO, or a custom issuance structure. It is not a finding that a payout is permitted. The stages below use money the platform already collected and an asset someone else already issues. They still leave tax reporting, sanctions screening, and the question of whether sending a stablecoin is money transmission. Those do not disappear because the coin is familiar. Staying small, paying only from collected revenue, and not advertising a new coin is the practical bound while counsel is unaffordable. The first time the program is large enough to pay for a lawyer is the time to get one, and it is the earliest time to consider issuing `tiny`.

| | Pay in an existing stablecoin (USDC on Base is the one the settlement code already names, as testnet USDC) | Issue `tiny` |
|---|---|---|
| What the user receives | Dollars, in their own wallet, spendable anywhere USDC is accepted. | A new transferable token. Spendable inside TinyAssets, and outside only if someone else wants it. |
| Where the money comes from | A share of Stripe revenue actually collected, after fees, refunds, and chargebacks. The platform buys USDC with that cash and pushes it. No mint. | Either a mint, or the platform buys `tiny` on the market with the same cash. A mint creates supply with no outside buyer. A buy is the steady buy-pressure design §9.1 asked counsel about. |
| Sink problem | The outside economy is the sink. A heavy host does not need to buy more GiB. | Unsolved. In-app sinks are the list above. No outside demand exists in this product. |
| New legal object | No new asset. The remaining questions are about sending an existing payment token and about information reporting. | A new transferable token paid for other people's use of the product, which is the classification question. The legal page's "not a security" line does not answer it. |
| Custody | Same rule either way: push to an address the user proved, do not keep a balance for them. A short batch of USDC in a founder-signed wallet is platform cash in transit, and it should be emptied by the send. | Same, plus the temptation to leave `tiny` "in the app" because there is nowhere else for it to go. That would be the custody the owner wants to avoid, and the pile the sink problem creates. |
| What has to be true first | Collected revenue, a ledger, a destination address, a record of the send. | All of that, plus a reason someone who is not being paid would hold the token, plus a lawyer. |
| Honest cost | Crypto sends can still be regulated activity. Card-to-stablecoin for third parties can conflict with a processor's rules (design §9.4). Users without a wallet are unpaid until they have one. Gas and a manual send are real. | The brand matches the site. The economics do not. Issuing it before there is outside demand pays people in something they cannot use, which is the weak in-app program with extra steps. |

**Inference.** Stablecoin from real revenue is the wallet payout that matches the economics. Issuing `tiny` does not, until something outside this app creates demand for it. That demand is not subscriptions, storage, seats, tips, or shape purchases.

A dollar payout to a bank account is even smaller as a new structure, and it spends in the same outside economy. It is not a crypto wallet. Offer it beside USDC for people who do not want a wallet. Do not build a third instrument.

## Lowest-risk way to pay contributors from revenue share

**Inference, and not legal advice.** "Lowest risk" here means the smallest new structure that still gets money out of collected revenue into the contributor's own hands. It does not mean the send has been cleared.

1. **Pay only cash that arrived.** The pool is a founder-set share of Stripe charges that have cleared refunds and chargebacks. The proposal's undecided range was 10–30%, with 20% as a sketch. Start at the low end of what the founder can actually send. If nothing was collected, nobody is paid. Do not mint the difference.
2. **One ledger, in cents.** The row is "this account is owed this many cents for this period." It closes when USDC (or a bank payment of the same cents) lands in their hands. It does not close into a `tiny` balance.
3. **Creators first.** The meter is seat time of a published piece, once that piece can be named. Self-use pays nothing. This is the creator-revenue-share proposal's Phase A math, settled in USDC instead of in `tiny`.
4. **Compute hosts are not in this payout.** A machine that saw another user's prompt is not a payable, whatever the coin. The sink story is a second reason: those hosts are who would accumulate an in-app balance. Solving the sink with USDC does not make that workload acceptable.
5. **Push, don't hold.** The user proves an address. The founder sends the batch and does not leave their share sitting in a platform wallet. No withdraw button, because there is no balance left behind.
6. **Records a person can keep without a law firm.** Who was paid, how many cents, which period, which transaction. Ask for a tax form before the first real send rather than after the total gets large. Check the destination against the public sanctions list before the send, and skip the send if it matches. The legal page's claim that Chainalysis already does this is still false; a manual check is not that product, and it is the check that fits a program too small to buy one.
7. **Do not issue `tiny`, do not flip `TINYASSETS_PAID_MARKET`, and do not turn the legal page's test rail on.** Those create the custom structure this stage exists to avoid.
8. **Revisit `tiny` only when both are true:** holders who are not payees want it for a reason that is not "we paid them in it," and the program can pay for counsel. Until then the published Base contract stays reference-only.

## Recommended minimal path

**Inference. Not legal advice.** The owner cannot afford counsel. Steps 1 and 2 move no money. Step 3 is a USDC send of collected revenue, not an issuance of `tiny`. It is the lowest-structure wallet payout this note can describe, and it is not a clearance.

1. **One payable ledger, in cents.** Append-only rows from cash actually collected. No `tiny` price, no in-app balance, no withdraw button. This is Phase A of `creator-revenue-share`. It is not built.
2. **Creator rows only, and only once a published piece can be named.** One record per released seat. Compute rows stay absent. Leave `TINYASSETS_PAID_MARKET` off. Do not write these rows into `settlement_ledger.py`. Do not revive the 2026-07-08 NAV mint.
3. **Pay the cents out in USDC to a wallet the user proved, or in dollars to a bank.** Founder-signed, batch emptied, sanctions list checked first, tax form collected before the first send. In-kind retirement is optional and does not fix the sink problem. Do not mint `tiny` and do not buy the published Base contract.
4. **Issue `tiny` only later,** when someone other than a payee has a reason to hold it and the program can pay for counsel. Until then the 2026-04-29 boundary stays closed.
5. **Copy, in a later change.** The legal page matches the fine print until a payout is real: no currency moves, the published contracts are reference-only, and the KYC and OFAC products named on the page are not running.

## Where counsel is needed

Not legal advice. The owner cannot afford a lawyer now, so the path above avoids a new token instead of waiting on a custom legal structure. That is a staging choice. It is not a conclusion that a USDC payout, a bank payout, or a later `tiny` issuance is permitted. Do not treat this note as permission to send money. When the program can pay for advice, the questions are:

- Whether sending USDC, bought with card revenue, to a contributor's wallet is money transmission or conflicts with the card processor's rules (design §9.2 and §9.4). This is the question the staged plan does not answer.
- Whether a later `tiny` is a security, a commodity, a digital tool, or something else, including the effect of the platform buying it with subscription revenue (design §9.1). The page's "not a security" line is not an answer.
- Whether a short-lived batch in a founder-signed Safe is custody of platform funds only, and which product behaviors (a withdrawable app balance, a deposit address, per-user tokens left in the Safe) would be custody of user funds.
- Tax forms, the reporting threshold, withholding, and fair-market value on the transfer date (design §9.3). Whether a same-brand testnet token creates a reporting or disclosure duty.
- The KYC threshold and the sanctions screen that must exist before a send. The page's $1,000 / Sumsub / Chainalysis sentences are not that program.
- Stripe's terms if card revenue is converted to buy the token (design §9.4).
- Apple and Google Play rules for showing or earning the token inside the apps (design §9.5 and §9.6).
- The consumer disclosure text, the unformed operating entity, and the draft terms, before the legal page describes a live rail.
- The open product rule on compute: no payable for a host that would see another user's prompts. That one is a product requirement from 2026-10-11, not a counsel question.

## What this note did not do

- No chain read of the Base, PulseChain, BSC, or Sepolia addresses.
- No check that Stripe is charging live customers.
- No counsel review.
- No edit to the legal page or any other site text.
