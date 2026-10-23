## Why

An owner may want their agent to use a personal computer, while Direction 8 requires every hosted surface to work with no owner host online.

## What Changes

- Define a personal computer as an optional external connector added by the owner to their box.
- Keep local access, consent and availability inside that connector; a missing or offline computer reports unavailable without blocking hosted work.
- Exclude the computer from platform routing, waking and owner-data host paths.

## Capabilities

### New Capabilities

- `optional-personal-computer-link`: Optional owner-added computer connector and cloud independence.

### Modified Capabilities

None. Existing companion-local-mcp work may supply a connector package; this change fixes N8's platform boundary.

## Impact

Spec-only N8 slice on `spec/n4-n8-view-and-pc-link`. Future implementation belongs in a box connector package and user-installed companion, after hosted box routing. No product code in this change.
