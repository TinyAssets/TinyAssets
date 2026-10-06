#!/usr/bin/env python3
"""Install the pre-rendered launcher icon + splash into the generated Android project.

Why pre-rendered: CI installs JS deps with ``npm ci --ignore-scripts`` (read-only
build, Codex review 2026-08-21) and ``@capacitor/assets`` needs sharp's native
libvips, which only arrives through an install script. So the density set is
rendered once on a dev machine (Pillow, from ``mobile/resources/icon.png`` +
``splash.png``) into ``mobile/resources/android/`` and this script copies it in
after ``npx cap add android`` — the same post-generate shape as
``add_app_scheme.py``.

Fails loudly (exit 1) when the generated project carries an icon/splash PNG we
have no replacement for, when a replacement's pixel size differs from the
template's file, or when the manifest no longer points its launcher icon at
``@mipmap/ic_launcher`` / ``@mipmap/ic_launcher_round`` — replacing files the
manifest does not reference would report success and ship the template icon
(Codex 2026-09-02). A silently wrong resource is exactly what Play's pre-launch
report would flag later.

Run from ``mobile/``: ``python3 scripts/add_app_icons.py``
"""
from __future__ import annotations

import re
import shutil
import struct
import sys
from pathlib import Path

MOBILE = Path(__file__).resolve().parents[1]
RES = MOBILE / "android" / "app" / "src" / "main" / "res"
MANIFEST = MOBILE / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
SRC = MOBILE / "resources" / "android"
BACKGROUND = "#14140F"  # launcher icon ground; independent of the app/splash background
# The manifest attributes that decide which resources the launcher actually shows.
MANIFEST_ICON_REFS = (
    ("android:icon", "@mipmap/ic_launcher"),
    ("android:roundIcon", "@mipmap/ic_launcher_round"),
)

ADAPTIVE_ICON = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background"/>
    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>
</adaptive-icon>
"""

BACKGROUND_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">{BACKGROUND}</color>
</resources>
"""


def png_size(path: Path) -> tuple[int, int]:
    """Width/height straight from the PNG IHDR chunk — no image library needed."""
    with path.open("rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise SystemExit(f"not a PNG: {path}")
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def is_target(rel: Path) -> bool:
    top = rel.parts[0]
    name = rel.name
    if top.startswith("mipmap-") and name.startswith("ic_launcher") and name.endswith(".png"):
        return True
    if top.startswith("drawable") and name == "splash.png":
        return True
    return False


def manifest_icon_problems(manifest_text: str) -> list[str]:
    """Every way the manifest could stop referencing the icons we install."""
    problems: list[str] = []
    for attr, want in MANIFEST_ICON_REFS:
        found = re.search(rf'{re.escape(attr)}="([^"]*)"', manifest_text)
        have = found.group(1) if found else None
        if have != want:
            problems.append(
                f"AndroidManifest.xml {attr} is {have!r}, expected {want!r} — "
                "the template changed; the pre-rendered icons would go unused"
            )
    return problems


def main() -> int:
    if not RES.is_dir():
        print(f"error: {RES} missing — run `npx cap add android` first", file=sys.stderr)
        return 1
    if not SRC.is_dir():
        print(f"error: {SRC} missing — render it with scripts/render_app_icons.py", file=sys.stderr)
        return 1
    if not MANIFEST.is_file():
        print(f"error: {MANIFEST} missing — template changed?", file=sys.stderr)
        return 1
    manifest_problems = manifest_icon_problems(MANIFEST.read_text(encoding="utf-8"))
    if manifest_problems:
        print("error: icon install aborted:", file=sys.stderr)
        for p in manifest_problems:
            print("  - " + p, file=sys.stderr)
        return 1

    targets = sorted(p for p in RES.rglob("*.png") if is_target(p.relative_to(RES)))
    if not targets:
        print("error: no icon/splash PNGs in the generated project — template changed?",
              file=sys.stderr)
        return 1

    problems: list[str] = []
    replaced = 0
    for dst in targets:
        rel = dst.relative_to(RES)
        src = SRC / rel
        if not src.is_file():
            problems.append(f"no pre-rendered replacement for {rel}")
            continue
        want, have = png_size(dst), png_size(src)
        if want != have:
            problems.append(
                f"{rel}: template is {want[0]}x{want[1]}, pre-rendered is {have[0]}x{have[1]}"
            )
            continue
        shutil.copyfile(src, dst)
        replaced += 1

    # Extra pre-rendered files with no counterpart in the template are a drift signal too.
    extras = sorted(
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.png")
        if not (RES / p.relative_to(SRC)).exists()
    )
    for e in extras:
        problems.append(f"pre-rendered {e} has no counterpart in the generated project")

    if problems:
        print("error: icon install aborted:", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        return 1

    anydpi = RES / "mipmap-anydpi-v26"
    anydpi.mkdir(parents=True, exist_ok=True)
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        (anydpi / name).write_text(ADAPTIVE_ICON, encoding="utf-8")
    values = RES / "values"
    values.mkdir(parents=True, exist_ok=True)
    (values / "ic_launcher_background.xml").write_text(BACKGROUND_XML, encoding="utf-8")

    print(f"installed {replaced} icon/splash PNGs + adaptive-icon XML (background {BACKGROUND})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
