# TinyAssets brand

The mark, its exporters, and the design skill.

| File | What it is |
|---|---|
| `mark.svg` | The full Wolf Moon Seal (transparent surround). Large brand artwork. |
| `mark-compact.svg` | The optically reduced disc. Website header and dense rows. |
| `mark-tile.svg` | The compact mark on its rounded tile. App icons and dark grounds. |
| `render_marks.py` | Exports the mark to every surface and records their hashes. Run from the repo root. |
| `generated-assets.json` | Generated receipt binding every committed mark to the source and exporter. |
| `render_og.py` | Renders the site's Open Graph card with the real web fonts. |
| `SKILL.md` | The design skill: identity, palette, type, motif, copy rules. |

**One source of truth, three optical drawings.** The geometry and palette live
in `WebSite/brand/mark.py` (`draw_mark`, `mark_svg`). `full` carries
the complete Wolf Moon Seal for large brand/marketing artwork, `compact`
widens the important shapes for app and header icons, and `micro` reduces the
scene to moon, Baker, and the howling wolf head for 16--32 px favicon frames.
They share one palette and one Everett-facing mountain profile; none is a
hand-edited export. The SVGs and every raster in this repo are generated from
that source:

```bash
python WebSite/brand/render_marks.py   # site icons, assets/, desktop, tray, Android, Play
python WebSite/brand/render_og.py      # WebSite/site-react/public/og-image.png
```

Both need Pillow; `render_og.py` also needs Python Playwright. Never hand-edit
an exported PNG, SVG, ICO, ICNS, manifest icon, or generated component — change
the geometry and re-run. `npm run check:brand` in `WebSite/site-react/` verifies
the receipt and runs in every site test/deploy gate. The served app (`tinyassets/onboarding/app.html`)
is not in the receipt: only its badge is generated, so brand parity checks that
badge against `mark-tile.svg`, and app feature edits never need a re-export. The same generated content
fingerprint versions favicon and manifest URLs, so browser caches cannot retain
the previous mark after a logo release.

The design language itself (tokens, components, the rules layer) lives in
`WebSite/design-system/`, with `DESIGN.md` as the brief.
