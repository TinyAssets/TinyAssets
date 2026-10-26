## Why

An owner may want their agent to use a personal computer, while Direction 8 requires every hosted surface to work with no owner host online.

## What Changes

- Define a personal computer as an optional external connector added by the owner to their box, with one broker-custodied pairing and outbound relay.
- Keep local access, consent and availability inside that connector; a missing or offline computer reports unavailable without blocking hosted work.
- Reuse the existing `/api` door for the companion's outbound link; keep routing and waking free of device business logic and owner-data host paths.
- Fold `companion-local-mcp` into N8 and delete the legacy tray host; `desktop-app/` remains the thin shell and carries the user-installed companion.

## Capabilities

### New Capabilities

- `optional-personal-computer-link`: Optional owner-added computer connector and cloud independence.

### Modified Capabilities

None. The open `companion-local-mcp` change is folded into this change and removed from the active queue.

## Impact

Spec-only N8 slice on `spec/n4-n8-view-and-pc-link`. Future implementation belongs in a box connector package and user-installed companion, after hosted box routing. No product code in this change.
