## Context

Modern discovery has the event capability but omits required caching fields. The authoritative 2026-07-28 schema requires cacheScope, capabilities, resultType, supportedVersions and ttlMs.

## Goals / Non-Goals

Make the response conform without changing authentication or compatibility. Do not claim this proves the cause of a particular client's failed scan.

## Decisions

Use ttlMs=0 and cacheScope=private, matching existing modern list responses. Move serverInfo into result._meta.io.modelcontextprotocol/serverInfo as recommended by the protocol. Keep supportedVersions and capabilities unchanged.

## Risks / Trade-offs

No caching across authorization contexts. Clients relying on a nonstandard top-level serverInfo must consume the standard metadata field; legacy initialize is unchanged.

## Verification

Validate the real local response against the downloaded official schema and exercise real FastMCP modern and legacy transport tests. No live subscription or provider call.
