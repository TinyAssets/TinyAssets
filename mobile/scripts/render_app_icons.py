#!/usr/bin/env python3
"""Render the app's icon/splash art (dev machine only — needs Pillow).

Two stages, both idempotent:

1. ``--from-logo PATH`` (optional): render the *sources* from a square
   logo mark — ``resources/icon.png`` (1024²), ``resources/splash.png`` (2732²) and
   the Play listing graphics under ``docs/ops/play-assets/`` (icon-512, feature
   graphic). The feature graphic carries the only type, and its font must be an
   explicit file rather than a host lookup, or the same command renders different
   pixels on Windows, Linux and macOS (Codex 2026-09-02). Pass ``--font`` (and
   optionally ``--font-bold``) to redraw its type; without them, the reviewed
   wordmark is preserved while its logo panel is refreshed.
2. Always: render the Android density set from the sources into
   ``resources/android/`` — launcher icons and adaptive foregrounds from
   ``icon.png``, every splash size from ``splash.png`` (cover-fit + centre-crop, so
   what is painted in ``splash.png`` is what ships) — the exact files
   ``add_app_icons.py`` copies into the generated project after ``npx cap add
   android``. Sizes mirror the Capacitor Android template; the installer refuses
   any mismatch, so a template change shows up as a red build, not a blank icon.

The committed PNGs are canonical; re-render only to change the art. Rendered
with Pillow 10.x (LANCZOS); a different major version may resample slightly
differently, which is a reason to review the diff, not to re-render casually.

Usage (from ``mobile/``):
    python scripts/render_app_icons.py
    python scripts/render_app_icons.py --from-logo ../assets/icon.png
    python scripts/render_app_icons.py --from-logo ../assets/icon.png \\
        --font /path/to/Inter-Regular.ttf --font-bold /path/to/Inter-Bold.ttf

``WebSite/brand/render_marks.py`` runs the second form for you as part of exporting
the mark to every surface. Without ``--font`` the feature graphic's existing type
is retained rather than redrawn with whatever font the host happens to have, so
the brand pipeline stays runnable and the wordmark stays reproducible.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

MOBILE = Path(__file__).resolve().parents[1]
REPO = MOBILE.parent
RES = MOBILE / "resources"
OUT = RES / "android"
PLAY = REPO / "docs" / "ops" / "play-assets"
BG = (0x14, 0x14, 0x0F)  # launcher icon and store artwork ground
SPLASH_BG = (0x0F, 0x10, 0x20)  # app --bg and capacitor shell backgrounds

LAUNCHER = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
FOREGROUND = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}
SPLASH_PORT = {
    "mdpi": (320, 480), "hdpi": (480, 800), "xhdpi": (720, 1280),
    "xxhdpi": (960, 1600), "xxxhdpi": (1280, 1920),
}


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    """An explicit font file — no host-dependent fallback (fail loudly instead)."""
    if not path.is_file():
        raise SystemExit(f"font file not found: {path}")
    try:
        return ImageFont.truetype(str(path), size)
    except OSError as exc:
        raise SystemExit(f"cannot load font {path}: {exc}") from None


def _save(im: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, optimize=True)
    print(f"{path.relative_to(REPO).as_posix()} {im.size[0]}x{im.size[1]}")


def render_sources(
    logo_path: Path, font_path: Path | None = None, bold_path: Path | None = None
) -> None:
    # Load every dependency BEFORE writing anything: the first draft validated
    # the font only when it reached the feature graphic, so a missing font left
    # icon.png and splash.png already overwritten and the committed source set
    # half-updated (Codex 2026-09-02).
    title_font = _font(bold_path, 84) if bold_path else None
    body_font = _font(font_path, 30) if font_path else None
    logo = Image.open(logo_path).convert("RGBA")
    if logo.size[0] != logo.size[1]:
        raise SystemExit(f"logo must be square, got {logo.size}")

    icon = Image.new("RGB", (1024, 1024), BG)
    big = logo.resize((1024, 1024), Image.LANCZOS)
    icon.paste(big, (0, 0), big)
    _save(icon, RES / "icon.png")

    splash = Image.new("RGB", (2732, 2732), SPLASH_BG)
    side = 820
    mark = logo.resize((side, side), Image.LANCZOS)
    splash.paste(mark, ((2732 - side) // 2, (2732 - side) // 2), mark)
    _save(splash, RES / "splash.png")

    i512 = Image.new("RGB", (512, 512), BG)
    m512 = logo.resize((512, 512), Image.LANCZOS)
    i512.paste(m512, (0, 0), m512)
    _save(i512, PLAY / "icon-512.png")

    if title_font is None or body_font is None:
        # Preserve the reviewed wordmark while replacing the mark-bearing left
        # panel. Logo changes must reach the store graphic even on a host without
        # the exact fonts; guessing those fonts would silently alter the type.
        feature = PLAY / "feature-graphic-1024x500.png"
        if not feature.is_file():
            raise SystemExit(
                f"{feature} missing — pass --font and --font-bold to create it"
            )
        fg = Image.open(feature).convert("RGB")
        if fg.size != (1024, 500):
            raise SystemExit(f"{feature} must be 1024x500, got {fg.size}")
        ImageDraw.Draw(fg).rectangle((0, 0, 469, 499), fill=BG)
        m = logo.resize((360, 360), Image.LANCZOS)
        fg.paste(m, (70, 70), m)
        _save(fg, feature)
        return
    fg = Image.new("RGB", (1024, 500), BG)
    m = logo.resize((360, 360), Image.LANCZOS)
    fg.paste(m, (70, 70), m)
    d = ImageDraw.Draw(fg)
    x = 470
    d.text((x, 150), "TinyAssets", font=title_font, fill=(0xF2, 0xF0, 0xEC))
    d.text((x + 4, 262), "Your own AI universe.", font=body_font, fill=(0x5E, 0xD6, 0xA6))
    d.text(
        (x + 4, 304), "Runs real work on your own LLM.", font=body_font,
        fill=(0xC8, 0xC6, 0xC2),
    )
    _save(fg, PLAY / "feature-graphic-1024x500.png")


def _legacy_icon(icon: Image.Image, size: int, round_mask: bool) -> Image.Image:
    im = icon.resize((size, size), Image.LANCZOS)
    if not round_mask:
        return im
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _foreground(icon: Image.Image, size: int) -> Image.Image:
    # Adaptive icon: 108dp canvas, launchers mask to the centre ~66dp. Keep the
    # mark inside that safe zone; the background layer is the solid colour resource.
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mark = int(size * 0.66)
    m = icon.resize((mark, mark), Image.LANCZOS)
    out.paste(m, ((size - mark) // 2, (size - mark) // 2), m)
    return out


def _splash(source: Image.Image, w: int, h: int) -> Image.Image:
    # Cover-fit the square splash SOURCE, then centre-crop. The mark stays
    # centred and the surround is the source's own background, so a change to
    # resources/splash.png is a change to every shipped splash — it is not a
    # dead file (Codex 2026-09-02).
    scale = max(w / source.width, h / source.height)
    sw = max(w, round(source.width * scale))
    sh = max(h, round(source.height * scale))
    scaled = source.resize((sw, sh), Image.LANCZOS)
    left, top = (sw - w) // 2, (sh - h) // 2
    return scaled.crop((left, top, left + w, top + h)).convert("RGB")


def render_density_set() -> None:
    icon_src, splash_src = RES / "icon.png", RES / "splash.png"
    for src in (icon_src, splash_src):
        if not src.is_file():
            raise SystemExit(f"{src} missing — run with --from-logo first")
    icon = Image.open(icon_src).convert("RGBA")
    splash = Image.open(splash_src).convert("RGB")
    if splash.width != splash.height:
        raise SystemExit(f"{splash_src} must be square, got {splash.size}")
    for d, s in LAUNCHER.items():
        _save(_legacy_icon(icon, s, False), OUT / f"mipmap-{d}" / "ic_launcher.png")
        _save(_legacy_icon(icon, s, True), OUT / f"mipmap-{d}" / "ic_launcher_round.png")
    for d, s in FOREGROUND.items():
        _save(_foreground(icon, s), OUT / f"mipmap-{d}" / "ic_launcher_foreground.png")
    _save(_splash(splash, 480, 320), OUT / "drawable" / "splash.png")
    for d, (w, h) in SPLASH_PORT.items():
        _save(_splash(splash, w, h), OUT / f"drawable-port-{d}" / "splash.png")
        _save(_splash(splash, h, w), OUT / f"drawable-land-{d}" / "splash.png")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--from-logo", type=Path,
        help="square logo PNG to render icon.png/splash.png/Play graphics from",
    )
    ap.add_argument(
        "--font", type=Path,
        help="TTF/OTF for the feature-graphic wordmark; omit to preserve its type",
    )
    ap.add_argument(
        "--font-bold", type=Path,
        help="bold face for the title; defaults to --font",
    )
    args = ap.parse_args(argv)
    if args.from_logo:
        bold = args.font_bold or args.font
        render_sources(args.from_logo, args.font, bold)
    render_density_set()
    return 0


if __name__ == "__main__":
    sys.exit(main())
