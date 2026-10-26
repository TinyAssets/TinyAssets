## Why

The modern discovery response fails the official 2026-07-28 DiscoverResult schema because cache hints are missing. A strict client can reject discovery before listing events.

## What Changes

- Return zero-TTL private caching hints and standard server identity metadata.
- Regress the official discovery schema requirements and retain legacy transport behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `live-mcp-connector-surface`: modern discovery returns the complete protocol envelope.

## Impact

Only the discovery response and its tests/spec change. No credentials, subscriptions, authorization, delivery or tool dispatch changes.
