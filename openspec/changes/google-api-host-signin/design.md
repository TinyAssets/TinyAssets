## Context

The directory already owns issuer endpoints, exact API hosts, named default scopes and host-to-use defaults. Shared Google API requests without scopes cannot select a use; other Google API subdomains are not covered.

## Goals / Non-Goals

Resolve API hosts through trusted provider data with requested scopes; preserve generic discovery. No provider-specific Python branches, client registration changes, token custody changes or implicit broad scope grants.

## Decisions

Extend existing `hosts` with leading `*.` patterns matching DNS subdomains at a dot boundary, never the apex or a suffix lookalike. Every connection host must be covered by the same row. Exact rows remain exact. Requesters cannot add directory hosts or endpoints. Keep `default_scopes` per API and `host_uses` for exact dedicated endpoints; shared/wildcard hosts require explicit scopes or `oauth.use`. Explicit scopes remain restricted to the row's declared scope sets. Missing credentials retain existing fallback.

Google data declares `googleapis.com`, `*.googleapis.com`, `www.googleapis.com`, Gmail and Calendar hosts. Agent guidance gives the concrete Calendar API host and full read-only scope. Existing schema is extended instead of adding a second competing host list.

## Risks / Trade-offs

Broad matching could redirect credentials to an unrelated host: validate patterns, require dot boundaries and all-host coverage, and test another provider plus lookalikes. Wildcard coverage is trusted administrator authority; it never widens a connection's own endpoint grants.

## Migration Plan

No stored data migration. Deploy directory/resolver together; revert the commit to roll back. Draft PR delivery records deployment and real-user proof as outstanding.
