---
name: tinyassets-design
description: Use this skill to design on-brand TinyAssets interfaces and assets, for production or for throwaway prototypes and mocks. Carries the warm-editorial identity: palette, type, the mark, the vocabulary, and the copy rules.
user-invocable: true
---

Read `../design-system/DESIGN.md` first: it is the rules layer, and
`../design-system/tokens/tiny.tokens.json` is the machine-readable mirror of
the canonical tokens in `../design-system/src/styles/tokens.css`.

If you are building production UI, import the real library
(`@tiny/design-system`) rather than copying values. If you are making a mock or
a one-off artifact, copy the token `:root` out of `tokens.css` into a standalone
HTML file so the look is identical.

## What TinyAssets is (get this right before designing anything)

- **The product in one line:** your own AI universe. A cloud agent with storage,
  connections, automations and a brain of its own, running on the Claude or
  ChatGPT subscription you already pay for. It builds any automation to any
  platform from a few primitives, learns you as it goes, and keeps working when
  you close the tab.
- **Naming:** *TinyAssets* is the platform and brand; *Tiny* is the universe a
  person talks to, in the first person. A chatbot connected over MCP relays to
  the universe; it never speaks as it.
- **Voice:** plain, exact, unhurried. Say what a universe finished, with the
  receipt. Never "AI-powered", never "unleash", never sparkle. Load-bearing
  verbs: found, connect, build, run, remix, refuse.
- **Never claim:** a platform-supplied model, a paid work market, tokens or
  crypto, host-run fleets, a list of integrations, or engagement metrics.

## Visual DNA (ink editorial)

- **Ground:** ink `#14140f`, raised surfaces `#1c1c16` and up. Never pure
  black, never a light default.
- **Cream:** `#f2efe6` for text; three quieter steps below it.
- **One accent:** ember `#e0703f` for the single action on a surface, one
  emphasised word, and the dot in the mark. A muted green `#3a7d47` is reserved
  for genuine liveness. Nothing else is coloured.
- **Type:** Fraunces (variable optical size) for display and first-person
  prose, Source Sans 3 for body, IBM Plex Mono for every id, number, timestamp
  and receipt line.
- **Motif:** rules and receipts. Sections are separated by hairlines, data sits
  in ruled tables (`.ledger`), and a run reports itself as a `.receipt` between
  a heavy top rule and a foot rule. Radii are small, shadows nearly absent, no
  glows or gradients.

## The mark

A circular badge: Mount Baker from Everett, a wolf howling toward an ember moon,
and a river galaxy overhead. The mountain is traced from a photograph, not drawn
from memory — its flat summit plateau and the Everett-facing asymmetry are the
details most stylisations get wrong. The whole scene lives as three `draw_mark`
renders (full, compact, micro) in `mark.py` and
nowhere else.

**Three optical versions:**
- `mark.svg` (full) — the complete Wolf Moon Seal for large brand and store artwork.
- `mark-compact.svg` (compact) — optically widened for app icons and header use.
- `mark-tile.svg` (compact on tile) — the compact version squared off with rounded corners.

Favicons and small-icon scales (16–32 px) use the `micro` path, further reduced to moon,
Baker, and the howling wolf. All three scale from one source and share one palette.

- `python render_marks.py` (from the repo root: `python WebSite/brand/render_marks.py`)
  exports the mark to the surfaces listed in `WebSite/brand/README.md`.
- `python render_og.py` renders the site's Open Graph card with the real fonts.

Never hand-edit an exported PNG or ICO or SVG, and never redraw the mark by eye.

## Common tasks

**A marketing page or hero:** import `@tiny/design-system/styles.css` (or paste
the token `:root`). Ritual label, then an H1 in Fraunces cream on the ink
ground, then one lead paragraph, then one ember button. Put the proof underneath as a
`.receipt`, not as a testimonial.

**An app surface:** hairline borders, `.sheet` for cards, `.ledger` for any
table of records, mono for every value that came from a machine. One primary
action per screen; secondary actions are ink-filled or quiet outlines.

**A diagram:** cream strokes on the ink ground, ember only for the element the reader
must follow. Label nodes in mono.

## Files

- `../design-system/DESIGN.md` — the full rules layer.
- `../design-system/src/styles/tokens.css` — canonical tokens.
- `../design-system/src/components/` — Button, StatusPill, RitualLabel, Tick,
  Term, Ladder, each with a `.prompt.md` describing correct use.
- `mark.svg` (full), `mark-compact.svg` (compact), `mark-tile.svg` (compact+tile),
  `render_marks.py`, `render_og.py` — the mark and exporters.
- `../site-react/` — the live site, the best reference for how the language is
  actually used.
