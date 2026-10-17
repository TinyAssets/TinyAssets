---
severity: P2
title: Redeposit race sends the new secret to the old endpoint
filed: '2026-09-29'
summary: >
  CredentialBlindBroker.dispatch reads the connection's policy and then resolves
  the credential through a credential_ref that is deterministic per (universe,
  destination), so a remove-and-redeposit to a different host between the two
  reads sends the NEW secret to the OLD declared host. Scheme-independent and
  pre-existing; bounded to one owner's own two deposits under one destination.
---

# Redeposit race sends the new secret to the old endpoint

**Filed:** 2026-09-29
**Verified:** 2026-09-29 by `gpt-6-astra` (read-only refute round on PR #4115,
branch `claude/capability-url-auth`, head `d8dc1986`), which reproduced it.
**Severity:** P2

## Source (verbatim)

> FINDING 1: Concurrent redeposit sends the new secret to the old endpoint.
> VERDICT: DISAGREE_EVIDENCE
> CITATION: tinyassets/storage/outbound_connections.py:1041; tinyassets/storage/outbound_connections.py:1072
> WHY IT MATTERS: The broker reads authority before resolving the mutable vault
> reference. Reproduced an inherited race where removing and redepositing the
> same destination causes the replacement credential to reach the previous host.
> CONCRETE ATTACK OR REPAIR: Pause dispatch before credential resolution; remove
> the connection; redeposit `https://new.example.com/new/NewSecret87654321`;
> resume. The captured request was
> `https://hooks.example.com/hooks/inbound/NewSecret87654321`. Bind vault lookup to
> an immutable connection incarnation and revalidate authority before dialing.

## Why it is filed rather than fixed in PR #4115

**Pre-existing and scheme-independent.** `CredentialBlindBroker.dispatch` reads
`_active_resource_for_grant(grant_id)` — the endpoint allowlist, the scheme, the
`credential_ref` — and then resolves the credential through that ref. The ref is
deterministic per `(universe, destination)`, so a remove-and-redeposit between
those two steps yields the NEW secret under the OLD policy. That is true for
`bearer` (new token, old host, in a header) exactly as it is for `url_secret`
(new segment, old host, in the path). astra's own verdict calls it "an inherited
race".

PR #4115 added the capability-URL scheme; it did not add this seam, and the
repair astra names — binding the vault lookup to the connection's `incarnation`
and revalidating authority immediately before dialing — is a change to the
broker's authority-read ordering that every scheme passes through. Folding it
into a scheme PR would put an unrelated concurrency rework behind a feature
review.

## What the repair needs

1. `_active_resource_snapshot_for_grant` already returns an authority stamp
   (it exists for the redirect chain). Extend it to the ordinary path: resolve
   the credential under the SAME stamp the policy was read at.
2. Refuse when the stamp moved between the policy read and the credential
   resolution, the way `revalidate_authority` refuses mid-redirect.
3. Test: a monkeypatched resolver that removes and redeposits the destination
   while the broker is between the two reads. Mutation-check it — the test must
   fail with the current ordering.

`remove_http` already moves the incarnation on redeposit, so the stamp exists;
this is about reading it in one place instead of two.

## Blast radius

Bounded to ONE owner's own two deposits under one destination name. The grant,
the universe binding and the host allowlist are all still the same owner's, so
this is not cross-user reach — it is the owner's newer credential reaching the
owner's older declared host. It needs a removal and a redeposit to a DIFFERENT
host under the SAME destination label, concurrent with an in-flight call.
