Implementation before C6 close-out is authorized by the lane brief. Merge remains blocked on C6.

## 1. Implementation prerequisites

- [ ] 1.1 Confirm C6 registry close-out, C7 single-check transport coverage and N10 owner-only authority; remove any obsolete shared path instead of guarding it.

## 2. Implementation

- [x] 2.1 Add the egress section to the existing broker policy store/protected owner write path and read-only projection; prove initialization, generation conflicts and `policy-writers` remain sound.
- [x] 2.2 Extend only `broker.egress.destination` with the three modes and existing proxy refusal plumbing; prove all transport paths, redirects, public-address pinning, custody and change/open ordering.
- [x] 2.3 Wire in-box Needs you requests and owner policy UI to existing consent; prove standing-entry approval through the sole writer, N10-committed credential scope/revocation, authenticated committer recording on creation and `extend`/`full`, and `test_box_committed_binding_does_not_satisfy_allowlist_or_ask` in `tests/test_owner_broker_egress.py`, denial, stale proposal refusal and raw proxy retry.

## 3. Acceptance

- [ ] 3.1 Run the design's targeted Linux tests and ruff, register `egress-gate` evidence, complete required floor review and a naive-owner app pass after deployment, then sync the delta. Do not mark the guard implemented from this spec PR.

Targeted Linux evidence is recorded in the implementation PR. No production access, independent floor review, live app acceptance, or as-built spec sync is claimed here.
