# L6 implementation evidence

Branch: `feat/outside-agent-grants`. The L6 dispatch supersedes the proposal's
historical docs-only delivery instructions, not its authority contract.
Implementation and acceptance are incomplete; enforcement is not enabled.

## Task 1.1: prerequisite investigation and executable probe

At base `b945fb3b3a`, `WorkOSAuthProvider` validates RS256, issuer, resource
audience, expiration and named subject but discards client/session claims.
`verified_claims()` now exposes that same validation to a diagnostic probe;
`resolve_token()` uses it with unchanged identity/capability behavior.

`scripts/outside_client_claims_probe.py --issuer <configured-https-origin>`
accepts JSON on stdin with `initial`, `refreshed`, `reauthorized` access-token
samples. Feed these from a protected ephemeral pipe, never command arguments or
a repository file. Every sample must verify for the canonical MCP resource and
the same owner and exact `client_id`; contradictory `azp` refuses. The receipt
contains fixed statuses and booleans, never claims, ids or credentials.
Missing identity, duplicate samples, invalid signatures and owner/client mixing
cannot become positive evidence. Neither `aud`, `iat`, a name nor caller
metadata substitutes for client identity or fresh authorization.

Exit 0 is a **candidate observation**, not a proven provider guarantee: the
probe cannot authenticate the operator's exchange labels. It always reports
`cutover_ready=false` and registered metadata as unobserved. Exit 1 requires
protected family binding; exit 2 means unavailable/invalid evidence. Tests use
locally signed fixtures and are not live AuthKit receipts.

## Provider evidence and outstanding proof

WorkOS primary documentation read 2026-10-05:

- [Connect tokens](https://workos.com/docs/reference/workos-connect/token)
  document `client_id` and an `app_consent_...` `sid`, but do not establish the
  needed `auth_time` invariants. Do not assume a consent id rotates on login.
- [Introspection](https://workos.com/docs/reference/workos-connect/introspection)
  returns client identity with application credentials. Resource-server access
  to other clients' introspection is unproven; do not collect their secrets or
  deploy an assumed API-key lookup.
- [Applications](https://workos.com/docs/reference/workos-connect/applications)
  documents client ids, redirects and dynamic-registration metadata. Tenant
  access and CIMD coverage are unverified. Provider `is_first_party` is not our
  exact issuer/client allowlist.

No AuthKit sample/credential environment variables were present. The prescribed
`scripts/load_secrets.sh` failed: `1Password CLI 'op' not installed`. Public
discovery at `https://tinyassets.io/.well-known/oauth-protected-resource`
returned HTTP 403 from this execution environment. The public canary refused
locally because `TINYASSETS_WIKI_CANARY_TOKEN` was absent. None is a live pass or
evidence about Muse callback acceptance.

To finish 1.1, collect sanitized receipts from authenticated initial, refresh
and fresh-interactive exchanges for independent clients, plus registered
metadata. Verify family stability on refresh and rotation on re-consent.

## Protected recovery contract still to implement and test

Reuse `onboarding/owner_sessions.py`'s server PKCE, browser flow cookie,
single-use state and same-origin owner check. Bind recovery to the protected
owner session, exact issuer/client, expected fence generation, deadline and a
new interactive exchange. Atomically compare that generation, consume recovery
once and add the **new** family without deleting old bindings/fences. A second
revoke invalidates pending recovery. Reject other owners/clients and replay.

A local opaque handle alone cannot distinguish refreshed third-party tokens at
`/mcp`: the provider must expose a trusted family discriminator or token-bound
authenticated lookup. A first-party login proves owner interaction, not the
outside credential's new family. Without either binding, cutover stays blocked;
accepting a submitted token, newer `iat` or caller handle could revive old
authority. The protected recovery implementation and race tests remain undone.

## Adjacent lanes and decisions

- `inline-connect-and-approve`: reuse protected owner sessions and HTTP approval
  machinery. Broad tasks remain open; Connected apps widening/recovery is absent.
- `addressed-agent-control-provenance`: tasks 3 onward are unimplemented at this
  base. Extend its carriers; unavailable downstream authority must refuse.
- `agent-access-controls`: extend existing access readback/channel revoke later,
  keeping client inventory private to the authorizing owner.
- `platform-state-outside-command-centers`: consent sidecar migration exists;
  enumeration/backup follow-up is pending. Client authority belongs at the
  specified platform root; store loss must not create an empty replacement.
- Admission script reports BLOCKED for 18 boxes. The merged proposal records
  the owner's explicit 4/9/5 section exception. Preserve the tasks and order;
  do not weaken the script or rewrite acceptance to get green.
- No public MCP schema/handle, prompt head or static prompt budget changed.
  Talk-back/outbox, A2A and outbound attach remain later changes.

## Task status

| Items | Status |
|---|---|
| 1.1 | Partial: reconciliation, probe and fixture tests; live identity/metadata/family proof and tested protected recovery outstanding |
| 1.2–1.4 | Not started; dependent on 1.1 |
| 2.1–2.9 | Not started; no grants or outside kill switch shipped |
| 3.1 | No cutover or rollback rehearsal |
| 3.2 | Slice checks/review only; deployment/full authority tests outstanding |
| 3.3–3.4 | No rendered two-client, first-party or Muse acceptance |
| 3.5 | No canonical spec sync/archive; proposed behavior is not as-built |

All boxes remain unchecked. Finish 1.1 evidence before proceeding to 1.2.

## Slice verification

- Linux oracle (Python 3.11.16, uid 1001, bubblewrap probe passed): **100 passed**
  across `test_outside_client_claims_probe.py`, `test_workos_provider.py`,
  `test_inline_owner_sessions.py` and `test_converse_turn_cost.py`. One PyJWT
  warning is from the intentionally invalid HMAC-signature fixture.
- Ruff on all changed Python files including the mirror: passed.
- `packaging/claude-plugin/build_plugin.py`: 612 files staged, `probe-ok`.
- `openspec validate outside-agent-grants --strict` and diff whitespace: passed.
- No affected file appears in `.github/heavy-test-files.txt`.
- Public canary: unavailable (missing canary bearer); no deployed-SHA assertion
  or rendered acceptance is claimed for this draft.
