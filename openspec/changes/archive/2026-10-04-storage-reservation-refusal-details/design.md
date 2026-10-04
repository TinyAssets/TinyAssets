## Context

The existing ledger reports measured bytes plus pending writes as used_bytes. pending has reserved and committed states. Owners currently receive this total as though it were retained disk usage. The preserved #4428 branch at 1585cc78 contains a tested diagnostic split; admission behavior will remain exactly current main (#4418/#4427).

## Goals / Non-Goals

Goals: report the three existing components to the owner, retain used_bytes and all current metadata, and suggest retrying active reservations.
Non-goals: schema/migration, altered retention, quota, launch headroom, zero-growth policy, new action, or any counts for a non-owner.

## Decisions

Use one grouped query of the existing pending table within the current usage read, recording reserved and committed components on Usage. Retain the sum of all pending states in total usage so reporting cannot weaken admission even if unexpected rows exist. Copy the original owner message and additive fields from #4428. Keep visible_record's owner boundary and _OTHER_ACCOUNT_FULL unchanged. Distinguish accounted bytes from physical bytes: measurements and pending records can temporarily overlap.

Adding no fields and only rewriting prose would lose machine-readable diagnostics in the original PR. Adding storage columns would duplicate state already in the ledger. Use additive derived fields instead.

## Risks / Trade-offs

- Accounting components can overlap physical data: describe them as accounted components and retain reconciliation semantics.
- A caller may construct Usage directly: search constructors, maintain explicit fields and update meaningful tests if required.
- Non-owner disclosure: exact generic-response assertions for foreign, empty and unknown viewers; no new lookup or owner inference.
- New reporting must not reduce admission totals: preserve the existing sum and validate ordinary reserved/committed transitions.

## Migration Plan

No persistent migration. Regenerate the packaged mirror, verify the focused accounting/privacy tests and independent review, then use normal PR gates. Reverting the code removes only the additional diagnostics.

## Pre-build review adaptations

Independent Claude shape review (2026-10-04) accepted the authority/privacy shape with these adopted constraints: this is a hand port, never a cherry-pick or whole-file checkout from 1585cc78. Allowed code hunks are Usage fields, _usage_in grouped read, refusal_record owner message/fields and only test_owner_refusal_distinguishes_measurements_and_pending_states. Preserve main's activity registry, headroom clamping, credit/bound formula and all recovery behavior; do not import the source's two fitted-headroom tests. used_bytes remains authoritative; components are explanatory. Accountless accounting-unavailable errors keep their existing non-account-specific response. Reserved entries can also expire after abandoned calls.
