## Context

Binding basis: `../owner-daemon-in-box/research.md`, design T1-T10, C7/C8; registry N5/N10; ADR-022 P5. C7 deliberately deleted legacy egress policy during cutover. This post-C6 change adds owner choice to that check, superseding only C7's unconditional public access rule; it never restores the deleted parallel gates.

## Goals / Non-Goals

Enforce owner internet choices for every box connection. No agent-side enforcement, provider-specific code, owner-file inspection, or new approval system.

## Decisions

1. **One check.** Extend `tinyassets/broker/egress.py:destination`. Its single decision combines bound owner admission, pinned public-address validation and owner egress policy before upstream open. Model, connector, public HTTP/CONNECT and git use it, including every new destination/redirect. Credential scope remains separate injection eligibility, never permission to bypass egress. Direct native egress stays impossible.
2. **One writer.** The existing broker-private `PolicyStore` owns the egress section `{mode, destinations}` and policy generation. Its protected owner-session write path alone commits changes and receipts (N10/T9); the agent gets a read-only projection. Initial provisioning explicitly sets `open`; missing/corrupt policy fails closed. C8 action policy stays in-box. This is the T6/T8 egress exception, not shared owner business logic.
3. **Exact scope.** `open` permits public destinations. `allowlist` permits exact normalized hostname-or-public-IP plus port entries; no wildcard, URL-path or provider-specific matching. Hostname matching never exempts resolved addresses from C7. `ask` requires owner approval for each new connection; approval covers the displayed destination and port, not arbitrary HTTP operations inside that connection. Raw CONNECT cannot provide URL-level consent. Private/metadata targets always refuse and never produce an approvable ask.
4. **Existing consent path.** A refused open in `ask` returns a broker-bound challenge to the box; the in-box request handler raises/deduplicates it on the existing Needs you rail. No socket is held awaiting a person. Protected owner approval issues one consumable retry authorization, bound to owner, policy generation, destination/port, challenge and current box volume generation, expiring after five minutes. The proxy alone consumes it atomically while rechecking admission and public addressing. Raw proxy clients receive refusal; the in-box relay forwards the challenge to the same request handler and a later retry uses the authorized connection. Deny/Clear/mute retain existing request semantics. Approval never edits policy; changing a standing rule is an explicit owner policy write.
5. **Changes take effect.** Policy writes serialize with opens; changed policy invalidates pending grants and closes existing owner egress connections before success is acknowledged. Failure reports pending, never effective. Restart, restore, suspension and erasure cannot replay a grant. Decision receipts contain policy revision, destination and outcome, not credentials or request bodies.

## Shared surface and proof

No new shared decision point. The necessary shared edits are `broker/egress.py` and the existing `broker/policy.py` schema/owner-only write and approval dispatch it consumes. Existing proxy adapters carry challenges; router and waker gain no business logic. Requests and app handling remain in-box. Credentials remain in broker custody.

Planned registry guard **`egress-gate`**: extend `tests/test_owner_broker_egress.py`, `tests/test_owner_public_egress.py` and `tests/test_box_public_egress.py` with all three modes across model/connector/public/CONNECT/git paths, redirects, DNS rebinding, direct-egress refusal and no credential leakage. `tests/test_owner_policy.py` plus `tests/test_policy_writers.py` prove owner-only writes, generation conflicts, single-use approval, expiry and policy-change/open races. Prove every opener reaches `destination`; do not add a second check to satisfy a test.

## Risks / Trade-offs

Per-connection asks are noisy and CONNECT consent is coarse; show the scope plainly and let the owner choose an allowlist. Automatic retries never mint approval. A suppressed request still refuses internet access.

## Implementation gate

C6 **registry close-out**, not merely design subsection C6: the owner-daemon cutover and deletion of shared owner paths must be complete, with C7 one-check coverage and N10 protected owner authority proven. Then initialize the new section as `open`, never import retired egress rules. A rollback that ignores restrictive policy must refuse those owners' egress. No implementation, deploy, spec sync into as-built specs, or independent review is claimed by this spec-only PR.
