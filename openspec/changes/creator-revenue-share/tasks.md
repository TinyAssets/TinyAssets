# Tasks: creator-revenue-share

Design approval (1.1) gates Phase A. Counsel (1.2) and the founder opening the
real-currency phase gate Phase C. No task moves money before 3.3, and 3.3 uses
`test tiny` on Base Sepolia only.

## 1. Decide

- [ ] 1.1 Founder approves the design and answers F1–F14 (design §10). Record the answers in design §10.
- [ ] 1.2 Counsel answers design §9 Q1–Q10 (host action). Record the answers and any changes they force.

## 2. Phase A: attribution, ingest, shadow ledger, metrics (no money moves; needs D9's `bundle_id` and activation gate)

- [ ] 2.1 Seat usage record at release, plus the D9 loader tagging the participating piece versions. Tests: the credited-time formula, the nested seat, a retry that never holds a seat; mutation-check each rule.
- [ ] 2.2 Read-only, idempotent Stripe revenue ingest (paid, fee, refund, dispute) with nightly reconciliation.
- [ ] 2.3 Monthly close: per-account allocation, conservation checks, maturity, adjustments, the micro-USD shadow ledger, the payer-erasure delta to `account-deletion`. Run a gpt-6-astra refute on the money math.
- [ ] 2.4 Creator metrics and shadow earnings as owner-door projections with k suppression, version folding and weekly granularity. Tests try differencing attacks. The founder approves the privacy-notice copy.
- [ ] 2.5 Live proof on the two test accounts: B runs A's piece, a shadow month closes, A sees a suppressed aggregate and a dollar shadow line, and B sees the piece's share of its own agent time.

## 3. Phase B: rehearse the rail on Base Sepolia

- [ ] 3.1 Read-only study: TINY pools and depth on Base, and price impact at $100, $1k and $5k across 0x, 1inch, Odos, Velora, CoW and Aerodrome quotes. No transaction. Report to the founder before 3.3.
- [ ] 3.2 Wallet binding: EIP-712, EIP-1271 and ERC-6492 proofs, single-use nonces, cooling-off with email notice, sanctions screening.
- [ ] 3.3 Sepolia Safe (host action), proposer and gas keys in the vault, the founder approval screen, MultiSend of `test tiny`, chain-event reconciliation, the kill switch and the 84532-only allowlist. Live rehearsal of one full month.

## 4. Phase C: mainnet (only after 1.2 and an explicit real-currency phase opening)

- [ ] 4.1 Open the phase in its own change: update the 2026-04-29 note and the legal page, publish the creator terms, add 8453 to the allowlist, set up the mainnet Safe (host action), and run a small founder-watched first run. Then sync the deltas into `openspec/specs/` and archive this change.
