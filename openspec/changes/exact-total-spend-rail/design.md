## Context

Sources: [Muse connection methods](../../../docs/design-notes/2026-10-04-muse-connection-methods.md) and [orchestration analysis](../../../docs/design-notes/2026-10-04-agent-orchestration-comparative-analysis.md). Their evidence labels/caveats remain authoritative for attribution; this is a proposed TinyAssets contract, not a verified claim of existing functionality.

## Goals / Non-Goals

Connection actions that spend money need an exact-total approval and reconciled payment receipt, not a reusable broad connector grant. inline-connect-and-approve owns approval sheet/Needs you/continuation and effect intents; connect-anything-ladder owns credential custody; existing budget/accounting paths own balances. Creator revenue and payouts remain creator-revenue-share work. No platform LLM or provider-specific compute/integration path.

## Decisions

Spend is an explicit action class. The payment rail requires a protected Allow once / Deny decision for merchant/payee identity, exact total in integer currency minor units, currency, item/quote revision and expiry. Task/site/always grants cannot authorize it; these wider choices are disabled for spend with a plain explanation. The founder-requested exact-total rail is a payment contract, not a new general approval policy for reads/writes. A changed merchant, total, currency, added fee or expired quote invalidates the preview and needs fresh approval. Unknown totals cannot execute.

Reuse effect_intents and the owner-control coordinator. Check and display current cap availability at preview, without reserving funds while the sheet is open. On approval, under the same owner budget authority atomically reserve the exact amount with the dispatch intent, considering concurrent reservations and recorded spend; recheck current authority/cap before dispatch. An insufficient cap leaves the request visibly blocked without issuing a payment credential/card or sending a charge. A previously affordable preview is not a reservation; a later cap increase or changed quote requires a fresh preview/decision. Use an existing connection whose data describes issuer/payment API and declared credential slots, not a vendor-specific integration. Broker alone sees issuer tokens/payment credentials and injects them into the bound transaction. Where available, issue a single-use credential constrained by merchant, amount, currency and time; it never enters the sandbox. Providers unable to enforce the required payment scope are visibly unsupported for this rail.

Use a durable idempotency key shared with provider dispatch and reconcile provider receipts. Crash or timeout after possible payment keeps both intent and reservation unresolved until authoritative status, never blind retry or premature release. Final receipt moves reserved amount to spent once; proven non-execution releases it. Stop/revoke prevents unsent payment, but cannot undo a sent charge; refunds are separate recorded operations with their own authority. Denial wakes only still-active work to plan an alternative without spending.

## Migration Plan

Extend existing versioned records and preserve prior data/history. Negotiate unsupported versions visibly before dispatch. Roll out after prerequisites pass; on rollback disable new dispatch, retain records and revocation/recovery controls, and never reactivate stale authority or erase user data.

## Risks / Trade-offs

External availability and partial failures can leave work held: expose current state and receipts; do not invent success. New handles can become confused deputies: resolve authenticated bindings at every dispatch and fence stale generations.

## Acceptance criteria

Approve a fixed-total test payment, deny another, change a quote, race two cap reservations and interrupt after dispatch; verify one receipt/charge and no credential exposure. Use a provider test environment before a founder-authorized live spend; no real spending is authorized by this docs proposal.

Implementation verification includes affected tests/heavy files, Ruff, strict OpenSpec validation, floor review, deployed-SHA assertion, real-user rendered proof and spec sync. Sandbox/process/network enforcement must pass the Linux oracle; a skip is not acceptance.
