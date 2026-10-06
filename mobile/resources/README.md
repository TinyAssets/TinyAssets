# App icon + splash

- `icon.png` — 1024×1024 source (the TinyAssets badge on the ink ground,
  `#14140f`). Every launcher icon and adaptive foreground is rendered from this.
- `splash.png` — 2732×2732 source (the badge centred on `#0f1020`, matching the
  dark app, bundled offline page and native window). Every splash size is a cover-fit centre-crop of this, so editing
  it changes what ships.
- iOS uses those two sources directly: `scripts/add_ios_assets.py` replaces the
  generated Xcode asset catalog's 1024×1024 icon and all three 2732×2732 splash
  entries. It fails on missing files, wrong dimensions, or catalog drift so a
  release cannot silently ship Capacitor's placeholder artwork.
- `android/` — the **pre-rendered density set** the app actually ships: legacy
  launcher icons (48–192 px, square + round), adaptive-icon foregrounds
  (108–432 px, mark at 66% so no launcher mask clips it) and the splash at every
  `drawable-{port,land}-*dpi` size. Mirrors the Capacitor Android template's
  paths so `scripts/add_app_icons.py` can copy by path after `npx cap add android`;
  the installer also checks the manifest still points at `@mipmap/ic_launcher`.

Why pre-rendered instead of `npx @capacitor/assets generate`: CI installs with
`npm ci --ignore-scripts` (read-only build), and `@capacitor/assets` needs sharp's
native libvips, which only arrives through an install script. Rendering once here
keeps CI free of an image toolchain and makes the shipped pixels reviewable.

## Regenerate

```bash
cd mobile
python scripts/render_app_icons.py                      # density set from icon.png + splash.png
python scripts/render_app_icons.py --from-logo ../assets/icon.png   # also icon.png, splash.png, icon-512
python scripts/render_app_icons.py --from-logo ../assets/icon.png \
    --font <Regular.ttf> --font-bold <Bold.ttf>         # ...and the feature graphic
# or, for every surface at once: python ../WebSite/brand/render_marks.py
```

The feature graphic is the only output with type on it, and its font must be an
explicit file: rendered from whatever the host happens to have installed, the same
command produces different pixels on different machines. Without `--font`, the
renderer preserves the reviewed wordmark and deterministically replaces its logo
panel, so a mark change still reaches the Play listing. Rendered with Pillow 10.x;
the committed PNGs are generated outputs — change the canonical geometry, re-run,
and review the pixel diff.

`scripts/add_app_icons.py` fails the build if any file in `android/` is missing or
the wrong size for the template it is copied into, so a Capacitor upgrade that
changes the template surfaces here rather than as a blank icon on a phone.
