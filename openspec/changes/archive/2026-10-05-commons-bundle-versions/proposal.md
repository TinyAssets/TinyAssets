## Why
Republishing a command center creates duplicate commons listings. Immutable versions need a stable author-bound catalogue identity.

## What Changes
- Mint a bundle per author, source home, source screen and publication kind; append versions atomically with definitions.
- Show only the current definition per bundle, retaining exact reads and traversable version history.
- Backfill only from platform publication evidence, never name similarity. Verify cross-account discovery.

## Capabilities
### New Capabilities
- `commons-bundle-versions`: author-bound publication identity and current-version discovery.
### Modified Capabilities
None. Recipient update consent remains unchanged.

## Impact
Custom-agent storage, publish snapshots, catalogue projections, package listings and their tests. Additive tables and read fields; no installed-copy rewrites.
