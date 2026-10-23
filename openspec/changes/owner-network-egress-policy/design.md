## Context

Binding basis: `../owner-daemon-in-box/research.md`, design T1-T10, C7/C8; registry N5/N10; ADR-022 P5. C7 deliberately deleted legacy egress policy during cutover. The [founder decision below](#founder-decision-2026-10-23) explicitly retains N5 after the 2026-10-20 open-public-web decision; it never restores deleted parallel gates.

## Founder decision (2026-10-23)

Open remains the default. Owners may restrict their box with allowlist or ask-before-internet. Destinations with an owner-bound credential (model sources and connectors) satisfy both modes without provider-specific code. Approving an ask writes a standing destination entry through the existing protected owner policy writer; no one-use grant store. This is the decision record for N5 surviving C7.

## Goals / Non-Goals

Enforce owner internet choices for every box connection. No agent-side enforcement, provider-specific code, owner-file inspection, or new approval system.

## Decisions

1. **One check.** Extend `tinyassets/broker/egress.py:destination`. Its single decision combines bound owner admission, pinned public-address validation and owner egress policy before upstream open. Model, connector, public HTTP/CONNECT and git use it, including every new destination/redirect. An active owner-bound credential for the exact destination satisfies the owner restriction; it never bypasses admission, public-address checks or service-scoped injection. Resolve this from generic credential bindings, never provider names. Re-evaluate every redirect hop; an unbound hop gets no inherited permission or credential. Direct native egress stays impossible.
2. **One writer.** The existing broker-private `PolicyStore` owns the egress section `{mode, destinations}` and policy generation. Its protected owner-session write path alone commits changes and receipts (N10/T9); the agent gets a read-only projection. Initial provisioning explicitly sets `open`; missing/corrupt policy fails closed. C8 action policy stays in-box. This is the T6/T8 egress exception, not shared owner business logic.
3. **Exact scope.** `open` permits public destinations. `allowlist` permits exact normalized hostname-or-public-IP plus port entries; no wildcard, URL-path or provider-specific matching. Hostname matching never exempts resolved addresses from C7. `allowlist` and `ask` both permit standing entries and destinations with active owner-bound credentials. `ask` requests approval only for other public destinations; approval adds the displayed destination and port as a standing entry until the owner removes it, covering future connections, not just one HTTP operation. Raw CONNECT cannot provide URL-level consent. Private/metadata targets always refuse and never produce an approvable ask.
4. **Existing consent path.** A refused `ask` open returns the normalized destination/port to the box; its request handler raises/deduplicates an existing Needs you policy proposal. No socket waits for a person. The card says "Allow future connections to HOST:PORT until you remove this rule." Confirmation goes through N10's protected owner-session route to `PolicyStore.write` with the expected policy generation. Only that writer appends the standing entry and receipts the write. A stale proposal must be refreshed and confirmed again. The client retries through the ordinary destination check after commit. Raw clients use the same in-box relay/request handler. Clear/Deny/mute never write permission. No challenge authorization, expiry, consumable token or new grant store.
5. **Changes take effect.** Policy writes serialize with opens; changed policy makes stale proposals conflict and closes existing owner egress connections before success is acknowledged. Failure reports pending, never effective. Restore cannot rewind broker policy or credential bindings. Revoked bindings cease satisfying restrictions; connection closure uses the existing revocation path. Decision receipts contain policy revision, destination and outcome, not credentials or request bodies.

## Shared surface and proof

No new shared decision point. The necessary shared edits are `broker/egress.py` and the existing `broker/policy.py` schema/owner-only write and approval dispatch it consumes. Existing proxy adapters carry blocked destination details; router and waker gain no business logic. Requests and app handling remain in-box. Credentials remain in broker custody.

Planned registry guard **`egress-gate`**: extend `tests/test_owner_broker_egress.py`, `tests/test_owner_public_egress.py` and `tests/test_box_public_egress.py` with all three modes across model/connector/public/CONNECT/git paths, redirects, DNS rebinding, direct-egress refusal and no credential leakage. `tests/test_owner_policy.py` plus `tests/test_policy_writers.py` prove owner-only writes, generation conflicts, standing-entry approval, credential-binding revocation and policy-change/open races. Prove every opener reaches `destination`; do not add a second check to satisfy a test.

## Risks / Trade-offs

Standing permission and CONNECT consent are broad; show destination/port and persistence plainly. Automatic retries never mint approval. A suppressed request still refuses internet access.

## Implementation gate

C6 **registry close-out**, not merely design subsection C6: the owner-daemon cutover and deletion of shared owner paths must be complete, with C7 one-check coverage and N10 protected owner authority proven. Then initialize the new section as `open`, never import retired egress rules. A rollback that ignores restrictive policy must refuse those owners' egress. No implementation, deploy, spec sync into as-built specs, or independent review is claimed by this spec-only PR.
