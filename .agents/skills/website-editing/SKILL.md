---
name: website-editing
description: "Conventions for editing the TinyAssets public site (WebSite/site-react, Next.js static export on @tiny/design-system). Use for website copy, components, routes, styling, the mark and brand assets, live-data surfaces, preview, tests, or deploy. Covers the preview loop, the copy rules, the public-read boundary, the build/test/sweep pipeline, and the site deploy."
---

# Website editing

`WebSite/site-react/` is the only source for `tinyassets.io`. It is a Next.js
14 App Router static export that consumes `@tiny/design-system`
(`WebSite/design-system/`, tokens + base styles + components). It deploys
through `deploy-site-react.yml` on a push to main that changes site source
(`WebSite/site-react/**`, `WebSite/design-system/**`, `WebSite/shared/**`).
A manual run with `confirm: deploy` is the rollback. The Svelte rollback tree was retired
on 2026-09-02; do not recreate a second tree or a parity step.

These are project-level website rules for every provider; `AGENTS.md` only
points here.

## Before you edit anything

1. **Read `WebSite/PREVIEW.md`** (the preview loop) and, if you might ship,
   `WebSite/DEPLOY.md`.
2. **Know what the site is for.** One sentence the whole site serves: *your own
   AI universe: a cloud agent that runs on your subscription, builds any
   automation to any platform from primitives, learns you continuously, and
   runs 24/7 under your control.* Every page has one job; the map is in
   `openspec/specs/public-website-surface/spec.md`.
3. **Tokens live in the design system.** Colour, type, spacing, and the
   vocabulary classes (`.sheet`, `.rule`, `.ledger`, `.receipt`, `.ev`,
   `.eyebrow`) come from `WebSite/design-system/src/styles/`. Edit
   `tokens.css` (canonical), run `npm run build` there, and the site picks it
   up. `DESIGN.md` there is the rules layer.
4. **The mark has one source.** `tinyassets/desktop/icon_gen.py` owns the
   geometry and palette; `python WebSite/brand/render_marks.py` exports it to
   every surface (site icons, `assets/`, desktop, Android, tray, Play
   listing) and `python WebSite/brand/render_og.py` renders the OG card.
   Never hand-edit an exported PNG or ICO.

## The iteration loop

```
cd WebSite/site-react && npm run dev     # http://localhost:3000, live /mcp proxied
```

Edit, the tab updates (HMR). If a tab ever needs F5, investigate; do not
normalise the workaround. Copy and tokens are reviewed in the browser, not in
the diff.

## Copy rules

- **Sell outcomes, not mechanisms.** Never open with "AI-powered workflow
  engine". Lead with what a universe finishes: a merged PR, a ledger, a
  watched feed. The proof on the home page is a real receipt (PR #2728).
- **Naming.** *TinyAssets* is the platform, site, and brand. *Tiny* is the
  universe the person talks to; it speaks first person. A chatbot *relays* to
  the universe; it never "embodies" it.
- **Honest availability.** No platform model; Claude is a setup-token deposit,
  not an OAuth button; ChatGPT/Codex is one tap. Android is a pre-release APK;
  desktop builds are unsigned and from source. Say so in words.
- **Plans in one place.** `/fine-print#plans` is the only page that describes
  free versus premium ($20 a month, higher daily limits on outside-world
  actions, compute and storage). No numbers until enforcement is live, no
  upgrade control until the app has one. `/start` may carry one sentence
  linking there.
- **Do not advertise** the paid market, crypto or tokens, hardware, host-run
  fleets, per-channel integration lists, the retired `Workflow` name, or
  "powered by <model>". No engagement metrics anywhere.
- **Hero** = ritual label + H1 + one lead paragraph + one primary action.
  Secondary links are quiet text.
- **Forms are never fake.** If there is no backend, use `mailto:` with real
  fields.
- **Affordance contract.** If it looks clickable it is clickable; prefer
  `<a>`/`<button>` over clickable `<div>`s.
- **Refresh labels are fixed:** `Refresh MCP` and `Refresh GitHub`.
- **No phone numbers.**

## The signed-in read boundary (tested)

Every `/mcp` request needs a connector bearer. `lib/live.ts` therefore makes
no MCP request from the public browser and returns an explicit sign-in-required
state. Public pages render only the labelled checked-in snapshot. A live read
belongs in an authenticated connector or account session, never a credential
embedded in static JavaScript. `WebSite/site-react/scripts/public-boundary.test.mjs`
and its canonical-contract sibling enforce this; extend them when you add an
authenticated live surface.

## Routes

Six public routes plus legal: `/`, `/start`, `/build`, `/commons`,
`/developers`, `/fine-print`, `/legal`, and `/account` (robots-disallowed).
Every retired route is a soft-landing alias through `app/_components/Moved.tsx`
(two-second redirect with a visible link). Nav items live in `lib/site.ts`
(`NAV`); canonical URLs in `SITE`. Adding a nav item updates desktop and the
phone drawer together.

## Build, test, sweep, ship

From the repo root (paths written in full so they resolve the same for you and
for the doc checker):

```
cd WebSite/design-system && npm run build   # after any token/component change
cd ../site-react && npm test                # contract + boundary + preview validators
npm run build                               # out/<route>/index.html for every route
```

Then, still in `WebSite/site-react`:

- `python WebSite/site-react/scripts/sweep.py --shots ../../out-shots` — every
  route and alias at 390 px and 1280 px; requires errs 0, warns 0, no overflow.
- `node WebSite/site-react/scripts/snapshot-public.mjs` — refresh
  `lib/mcp-snapshot.json` when it is stale.

Before declaring done: the sweep is clean, the screenshots were looked at,
and protected live surfaces render an explicit signed-in state while snapshots
retain their date and provenance. Reject raw placeholders such as `{}`, `[]`,
`undefined`, or epoch numbers.

Ship through the normal pull-request path. A merge that changes the site
source paths above runs `deploy-site-react.yml`. Then the canary and
`deployed_sha.py` checks in `WebSite/DEPLOY.md`. A manual run with
`confirm: deploy` republishes or rolls back.

## Auto-iterate on recurring website failures

| Recurrence | Ratchet |
|---|---|
| 1st | Fix in place. Note it in the relevant doc. |
| 2nd | Add the rule to **this** SKILL.md and PREVIEW.md / DEPLOY.md. |
| 3rd | Build a runnable check under the site's `scripts/` directory (node test or the sweep). |
| 4th | Wire it as a hook; runnable from any provider. |
| Next | Pre-commit / CI gate. |

Ratchets already in place: cross-provider drift (`cross-provider-drift`
invariant); the public-read boundary (node tests); the rendered sweep
(`WebSite/site-react/scripts/sweep.py`); exact workflow pinning for the hosted-preview trust
boundary (the pinned preview-worker security test).

## Files involved

| File | What it is |
|---|---|
| `WebSite/site-react/` | The site source |
| `WebSite/site-react/lib/site.ts` | Canonical URLs and the nav |
| `WebSite/site-react/lib/live.ts` | Browser authentication boundary for live reads |
| `WebSite/site-react/scripts/` | Tests, snapshot baker, sweep, preview validators |
| `WebSite/design-system/` | `@tiny/design-system`: tokens, base, components, `DESIGN.md` |
| `WebSite/brand/` | The mark's SVGs and exporters |
| `WebSite/shared/mcp/public-read-contract.js` | Public read contract shared by site and baker |
| `WebSite/PREVIEW.md`, `WebSite/DEPLOY.md` | Preview loop; deploy and verify |
| `.github/workflows/deploy-site-react.yml` | Production deployment on site-source pushes to main, plus manual rollback |
| `openspec/specs/public-website-surface/spec.md` | As-built spec of the public site |
