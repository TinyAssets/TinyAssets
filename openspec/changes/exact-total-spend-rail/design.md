## Context

Sources: [Muse connection methods](../../../docs/design-notes/2026-10-04-muse-connection-methods.md) and [orchestration analysis](../../../docs/design-notes/2026-10-04-agent-orchestration-comparative-analysis.md). Their evidence labels/caveats remain authoritative for attribution; this is a proposed TinyAssets contract, not a verified claim of existing functionality.

## Goals / Non-Goals

Payments start with exact-total once-only approval and reconciled receipts; owners can replace that default with a budget-capped spend grant. inline-connect-and-approve owns approval sheet/Needs you/continuation and effect intents; connect-anything-ladder owns credential custody; existing budget/accounting paths own balances. Creator revenue and payouts remain creator-revenue-share work. No platform LLM or provider-specific compute/integration path.

## Decisions

Owner-declared tool/effect classification is trusted. Tool hints alone cannot grant authority; unknown effects follow the owner's editable default (starter default: ask through the approval sheet), never refusal merely for being unknown. Exact-total once-only approval is the editable starter default for payments, not an immutable platform rule. The owner may instead authorize a spend grant bounded by an owner-editable budget cap, destination/action scope and expiry; dispatch rechecks that grant and atomically reserves against the cap. Unknown payment totals need an enforceable maximum within that grant, or return to the owner's approval sheet. Cross-user isolation is the only immutable platform behavioral invariant. The once path binds merchant/payee, exact total in integer minor units, currency, quote revision and expiry. A changed quote invalidates that once decision; a standing spend grant is evaluated against its own owner-defined scope and cap.

Reuse effect_intents and the owner-control coordinator. Check and display current cap availability at preview, without reserving funds while the sheet is open. On approval, under the same owner budget authority atomically reserve the exact amount (or enforceable maximum for a capped grant) with the dispatch intent, considering concurrent reservations and recorded spend; recheck current authority/cap before dispatch. An insufficient cap leaves the request visibly blocked without issuing a payment credential/card or sending a charge. A previously affordable preview is not a reservation; a later cap increase or changed quote requires reevaluation under current owner policy. Use an existing connection whose data describes issuer/payment API and declared credential slots, not a vendor-specific integration. Broker alone sees issuer tokens/payment credentials and injects them into the bound transaction. Where available, issue a single-use credential constrained by merchant, amount, currency and time; it never enters the sandbox. If a provider cannot enforce the owner-selected payment scope or maximum, report the limitation and return to the approval sheet; never treat an unknown tool as a payment solely because it is unclassified.

Use a durable idempotency key shared with provider dispatch and reconcile provider receipts. Crash or timeout after possible payment keeps both intent and reservation unresolved until authoritative status, never blind retry or premature release. Final receipt moves reserved amount to spent once; proven non-execution releases it. Stop/revoke prevents unsent payment, but cannot undo a sent charge; refunds are separate recorded operations with their own authority. Denial wakes only still-active work to plan an alternative without spending.

## Migration Plan

Extend existing versioned records and preserve prior data/history. Negotiate unsupported versions visibly before dispatch. Roll out after prerequisites pass; on rollback disable new dispatch, retain records and revocation/recovery controls, and never reactivate stale authority or erase user data.

## Risks / Trade-offs

External availability and partial failures can leave work held: expose current state and receipts; do not invent success. New handles can become confused deputies: resolve authenticated bindings at every dispatch and fence stale generations.

## Acceptance criteria

Approve a fixed-total test payment, deny another, change a quote, race two cap reservations and interrupt after dispatch; verify one receipt/charge and no credential exposure. Use a provider test environment before a founder-authorized live spend; no real spending is authorized by this docs proposal.

Implementation verification includes affected tests/heavy files, Ruff, strict OpenSpec validation, floor review, deployed-SHA assertion, real-user rendered proof and spec sync. Sandbox/process/network enforcement must pass the Linux oracle; a skip is not acceptance.
