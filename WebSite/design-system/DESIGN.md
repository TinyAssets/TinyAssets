# TinyAssets — Design System ("Ink editorial")

> Framework-agnostic design brief for AI design tools and coding agents.
> This file is the rules layer. The compiled React library (`dist/`) and the
> DTCG tokens (`tokens/tiny.tokens.json`, generated from
> `src/styles/tokens.css`) are the machine contracts.

## 1. Theme & feeling

TinyAssets reads like **a well-set document about serious work, printed the
other way round**: an ink ground, cream text, one warm accent, ruled lines, and
run receipts. It is not a dashboard and it is not an AI product page. No
sparkle, no glowing chat bubbles, no gradients. The dark ground also matches
the web app, so the site and the product stop looking like two products.

The core distinction is **claim versus evidence, made typographic**: prose is
serif or sans; every live value, id, and timestamp is mono, always.

The subject of the site is one app for the user's own agents, a model they
bring, and setups they can share. Copy does not open with "AI-powered
workflow engine" or "global goals engine".

## 2. Color (semantic roles → CSS variables)

Use the semantic variables, not raw hex. Full list in `tokens/tiny.tokens.json`.

| Role | Variable | Use |
|---|---|---|
| Page ground | `--bg-0` | body background (`#14140f`) |
| Raised surface | `--bg-1` … `--bg-3` | sheets lifted off the ground |
| Text | `--fg-1` (cream) → `--fg-4` (faint) | type hierarchy |
| Action / emphasis | `--ember-600` / `--accent` (`#e0703f`, ember) | **the one** accent: the primary action, the dot on the mark, one emphasised word |
| Liveness | `--live-600` / `--signal-live` | **reserved** for genuine live evidence |
| Idle | `--signal-idle` | asleep, a first-class state |
| Error | `--signal-error` | failures that name their cause |
| Rules | `--border-1` (hairline), `--border-2`, `--border-strong` | the ruled-table motif |
| Paper block | `--panel`, `--on-panel` | the inversion: a rare printed sheet laid on the ink |

**Hard rules:** green is load-bearing, only for genuinely live state. There is
exactly one accent; the violet variables resolve to cream so a "secondary"
object is quiet, not a second colour. No glows. Token NAMES are stable across
identities and only their values change, so `paper-*` is the surface ramp
(darkest first) despite the name.

## 3. Typography

Three families, each with a job:

- **Display / voice** `--font-display`, `--font-voice` (Fraunces, variable
  optical size) — headlines and the universe's first-person prose. Weight 500
  for headings, `"opsz" 144, "SOFT" 30` at hero size.
- **Body** `--font-sans` (Source Sans 3) — paragraphs, labels, nav.
- **Evidence** `--font-mono` (IBM Plex Mono) — every live number, id,
  timestamp, address, receipt line. No exceptions.

Scale `--fs-xs` (11px) … `--fs-6xl` (92px). Eyebrows use mono, uppercase,
wide tracking (`--ls-caps`) — the `RitualLabel` / `.eyebrow` vocabulary.

## 4. Components

- **Button** (`btn`) — `primary` (ember; the single key action on a
  surface) / `secondary` (ink fill) / `ghost` (hairline outline) / `link`;
  sizes `sm|md|lg`. Renders `<a>` when `href` is set. No hover glow.
- **StatusPill** (`pill`) — `kind` live/idle/paid/self/error on paper;
  `pulse` only when truly live.
- **RitualLabel** (`ritual-label` / `.eyebrow`) — mono kicker.
- **Tick** — a provenance device: mono glyph + source label, optionally a link.
- **Term** — inline first-use definition with an ink tooltip.
- **Ladder** — an outcome ladder; a rung lights only with evidence.

## 5. Vocabulary classes (global, in `styles.css`)

`.voice` (first-person prose), `.ev` (inline evidence), `.eyebrow`,
`.dot.live/.idle/.error`, and the motif:

- `.sheet` — a sheet of paper on the desk (card).
- `.rule` — a labelled horizontal rule, like a ledger section head.
- `.ledger` — a ruled table: hairlines between rows, a strong rule under the
  head and at the foot.
- `.receipt` — a run receipt: `dl` of `dt`/`dd` rows in mono between a heavy
  top rule and a foot rule; `dd.ok` / `dd.err` colour the result line.
- `.readout` — the inversion: a printed sheet on the ink.

## 6. Layout, spacing, shape

4px spacing base (`--s-1`=4 … `--s-24`=96). Radii are small (`--radius-sm` 4,
`--radius-card` 6): paper has corners, not pills. Page content lives in
`.container` (max-width 1140px, fluid side padding). Sections breathe with
`--s-16` rhythm. Measure: 60–64ch for prose.

## 7. Elevation & motion

On ink, depth is a lighter surface rather than a shadow: raise a thing by
moving it up the `--bg-*` ramp. Shadows exist for genuinely floating objects
only. Motion is calm: `--dur-base` 200ms, `--ease-standard`.

## 8. Do / don't

- DO reserve green for real liveness; DO set every id/timestamp in mono; DO keep
  one ember action per surface; DO use rules and receipts, not cards with
  shadows, to structure information.
- DON'T use pure black or pure white; DON'T add a second accent; DON'T add glows or
  gradients; DON'T mix claim and evidence type registers; DON'T print
  engagement metrics.

## 9. The mark

A circular badge: **Mount Baker from the south**, a **wolf howling** on the
snowfield, a pale **moon** and a spiral **galaxy** overhead. The mountain's
profile is traced from a photograph of the real skyline, so it carries Baker's
signature: a flat summit plateau rather than a peak, the jagged Lincoln and
Colfax group at about half height on the left, and a long south-east flank.

The scene lives as the `EMBLEM` layer list in `tinyassets/desktop/icon_gen.py`.
Two renderers read it -- one emits SVG, the other rasterises with Pillow -- and
`WebSite/brand/render_marks.py` exports every raster AND generates the site's
inline React component, so the web mark cannot drift from the app icons.

## 10. Agent build guide

Import `@tiny/design-system/styles.css` once at the root (tokens + reset +
vocabulary), then compose with the components and the semantic variables:

```tsx
import "@tiny/design-system/styles.css";
import { Button, StatusPill, RitualLabel } from "@tiny/design-system";

<section className="container">
  <RitualLabel>Start</RitualLabel>
  <h1>A universe of your own.</h1>
  <p>It runs on the subscription you already pay for.</p>
  <Button href="https://tinyassets.io/app">Open the app</Button>
  <StatusPill kind="live" pulse>reachable</StatusPill>
</section>
```
