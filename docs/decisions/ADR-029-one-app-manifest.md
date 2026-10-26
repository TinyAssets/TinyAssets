# ADR-029: One App Manifest

## Status

Accepted (founder, option 1, 2026-10-26).

## Date

2026-10-26

## Context

Two app shapes exist:

- **The N11 projection** (`tinyassets/app_object.py`): every command center
  read as an app of kind `command_center`. Its registry guard says another
  `app_manifest` is a second path.
- **The published slug app** (`apps/{slug}/app.json`, from
  `post-c6-multiplayer`): the app players open.

Apps and games need one place for requirements (nothing installed, an
install on phone or computer, or computer only), hosting tier, agent
interface and AI payment.

## Options

1. The slug app document carries the manifest, with a `multiplayer` block;
   the N11 projection reads it. Recommended by the multiplayer research.
2. The N11 projection is extended, and the slug app becomes a published view
   of a command center's app.

## Decision

Option 1. The published app file (`apps/{slug}/app.json`) carries the one
manifest. Its `multiplayer` block holds requirements, hosting tier, agent
interface and AI payment. The N11 projection reads that file and stops being
a second source.

## Consequences

- Any manifest field the N11 projection keeps of its own is deleted in the
  same PR; the projection survives only as a reader of `app.json`, and
  N11's guard names `app.json` as the one manifest.
- A game's type doesn't decide how hard it is to serve; its declared
  requirements do.
- Build order: `openspec/changes/device-first-build/`.
