"""The TinyAssets mark, and the one place its geometry lives.

The mark is a circular badge: **Mount Baker seen from Everett**, a **wolf
howling** toward an ember moon, and a **galaxy** overhead.
The mountain's profile is traced from a photograph rather than drawn from
memory, so it carries Baker's signature: a flat summit plateau instead of a
peak, the jagged Lincoln and Colfax group at about half that height on the
west (left, from the south), and a long south-east flank.

Every rendering comes from the layer lists below: the Windows tray
``.ico`` (``generate_icon``), the brand exports under ``WebSite/brand/`` and
``assets/``, the desktop and Android app icons, and the SVG the site inlines
(``WebSite/brand/render_marks.py`` imports ``draw_mark`` and ``mark_svg``).
One description, two renderers -- ``mark_svg`` emits it as SVG and
``draw_mark`` rasterises the same list with Pillow -- so a change here reaches
every surface and nothing can drift.  There are three optical drawings rather
than one illustration blindly shrunk everywhere: ``full`` for large brand and
store artwork, ``compact`` for app/header icons, and ``micro`` for 16--32 px.

A layer is a dict:

    {"kind": "circle", "cx", "cy", "r", "fill", "opacity"?, "clip"?}
    {"kind": "path",   "d",  "fill", "opacity"?, "clip"?, "transform"?}
    {"kind": "stroke", "d",  "stroke", "width", "opacity"?, "clip"?}

``clip`` confines a layer to the badge outline (the disc, or the rounded
square when ``tile`` is set). ``transform`` is ``(tx, ty, scale)``, which is
how the wolf is drawn in comfortable coordinates and then placed.

Exports
-------
draw_mark(size, tile=False)   PIL image of the badge at ``size`` px (RGBA).
mark_svg(tile=False)          the same badge as an SVG document string.
create_icon_image(size)       the app-icon tile, for the tray and launcher.
generate_icon(output_path)    the multi-size tray ``.ico``.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

# ---- palette (matches WebSite/design-system/src/styles/tokens.css) ----------
INK = (0x14, 0x14, 0x0F)      # --bg-0, the ground the site sits on
SKY = (0x1B, 0x1B, 0x24)      # the night sky: lifted off the ground so the
                              # badge keeps an edge on the site's own ink
CREAM = (0xF2, 0xEF, 0xE6)    # --fg-1: snow, stars, the badge rim
ACCENT = (0xE0, 0x70, 0x3F)   # --ember-600: the moon
DUST = (0xC9, 0xB6, 0xFF)     # the cool cast of the galaxy disc

INK_HEX = "#14140f"
SKY_HEX = "#1b1b24"
CREAM_HEX = "#f2efe6"
ACCENT_HEX = "#e0703f"
DUST_HEX = "#c9b6ff"

# ---- geometry, in a 64 x 64 box -------------------------------------------
VIEWBOX = 64.0
DISC_CX = DISC_CY = 32.0
DISC_R = 31.0
RIM_R = 30.4
RIM_W = 1.2
TILE_RADIUS = 13.0            # rounded corner when the badge is an app tile
MOON_CX, MOON_CY, MOON_R = 48.0, 14.5, 5.1

_SUPERSAMPLE = 4

# The wolf, drawn around a 30 x 26 box, then placed on the snowfield.
_WOLF_AT = (22.0, 39.8, 0.58)

# ---- Mount Baker from the south -------------------------------------------
# Traced from photographs taken from Everett and Seattle, both of which look at
# the mountain from the south. Left to right the skyline runs:
#
#   * Lincoln Peak, a sharp twin spire (9,080 ft), lowest and furthest west;
#   * a gap, then Colfax Peak, broader and higher (9,440 ft) -- together the
#     Black Buttes, dark rock left over from the older volcano on Baker's
#     west shoulder, standing about half the height of the summit;
#   * a col, then the fairly straight rise of the Roman Wall;
#   * the SUMMIT AS A BROAD, NEARLY LEVEL PLATEAU -- the ice dome over Carmelo
#     Crater. This is the detail every stylisation gets wrong: from the south
#     Baker has a flat top, not a peak;
#   * a step down to a rounded shoulder, then a long, gentle descent down the
#     south-east flank, roughly twice the length of the left side.
#
# Sherman Peak (10,140 ft) sits on the west rim of Sherman Crater, south of the
# summit, and shows as a rock patch at the plateau's left shoulder.
_SNOW = (
    # Traced from the Everett photograph by modelling the sky gradient and
    # finding, per column, the first row that departs from it. Landmarks left
    # to right: Lincoln Peak, a saddle, Colfax Peak (together the Black Buttes,
    # about half the summit height), the col, the long straight rise of the
    # Roman Wall, THE SUMMIT PLATEAU -- flat, not a point -- a step down, the
    # rounded east shoulder, and the long south-east flank.
    "M-6 64 L-6 43.4 L-2 38.5 L0 36.1 L1.5 34.6 L3 33.3 "
    "L4.5 36.2 L6 38.9 L7.5 40.0 L9 38.2 L10 37.0 L11.5 40.0 L13 40.5 L14 38.8 "
    "L15 34.6 L16.5 32.6 L18 30.8 L19.5 32.2 L21 32.4 L23 33.0 L25 35.3 L26.2 37.0 "
    "L28 34.5 L30 31.8 L32 29.3 L34 26.9 L36 23.6 L37.5 21.2 "
    "L39 20.6 L41 20.2 L43 20.5 L45 21.1 L47 21.5 "
    "L48.5 23.2 L50 26.3 L51.5 25.2 L53 26.4 L55 28.9 L57 30.7 L59 33.0 "
    "L61 34.9 L63 37.1 L65 41.9 L67 43.5 L70 43.5 L70 64 Z"
)
# The selected Wolf Moon Seal uses a lower, darker ridge.  It gives the wolf a
# clean snow-coloured field behind its silhouette instead of merging the animal
# into a mid-tone foreground at icon sizes.
_INK_RIDGE = "M-4 64 L-4 56 Q9 52.5 22 54 Q35 56.2 47 53.6 Q57 51.4 68 50 L68 64 Z"

# Optical reductions keep Baker's Everett-facing asymmetry: the serrated Black
# Buttes remain at left, the broad summit shelf stays off-centre, and the right
# flank is longer.  They intentionally omit the full drawing's smaller cuts.
_COMPACT_MOUNTAIN = (
    "M-5 64 L-5 45 L3 39 L7 42 L11 36 L15 41 L19 35 L23 38 "
    "L31 28 L36 22 L40 20.5 L45 20.8 L49 22.5 L53 27 L57 29 "
    "L61 34 L69 40 L69 64 Z"
)
_MICRO_MOUNTAIN = (
    "M-5 64 L-5 47 L7 39 L13 43 L25 31 L34 23 L40 21.5 "
    "L46 22 L51 27 L56 30 L61 36 L69 41 L69 64 Z"
)

# ---- the galaxy -----------------------------------------------------------
# Direction A's galaxy is a diagonal river rather than a second circular
# object competing with the moon.  Three strokes and a deliberately sparse
# particle set remain graphic at 64 px while feeling expansive in large art.
_GALAXY_SWEEPS = [
    ("M-4 30 C12 23 22 12 58 5", 1.8, DUST, 0.66),
    ("M-3 26 C15 19 28 9 61 4", 0.55, CREAM, 0.62),
    ("M3 33 C18 26 31 18 52 15", 0.65, DUST, 0.58),
]
_GALAXY_PARTICLES = [
    (4, 27, 0.7), (8, 24, 0.38), (12, 23, 0.55), (15, 19.5, 0.4),
    (19, 18, 0.72), (23, 14.5, 0.42), (27, 14, 0.62), (31, 10.5, 0.42),
    (35, 10, 0.72), (40, 7.5, 0.4), (44, 7.1, 0.55), (53, 5.5, 0.52),
]

# The approved concept's wolf, reduced to a deterministic 29 x 26 silhouette.
# The full path keeps the bushy tail and natural paw/leg rhythm; the compact
# path removes the smallest notches but keeps the unmistakable open-mouthed
# howl.  Both are derived from the selected Direction A pose, not a stock mark.
_WOLF_FULL_PATH = (
    "M27.42 0.20 L26.90 0.06 L24.65 2.33 L22.85 2.86 L21.39 4.31 "
    "L19.38 5.10 L19.41 5.49 L20.39 6.02 L18.56 7.76 L18.90 7.79 "
    "L16.77 9.36 L14.76 10.09 L9.34 10.95 L6.82 12.19 L5.57 13.50 "
    "L3.62 17.23 L1.86 19.39 L2.43 19.25 L0 21.91 L2.07 21.97 "
    "L1.92 22.19 L3.77 21.24 L3.80 21.52 L5.17 19.89 L5.23 20.20 "
    "L6.09 18.38 L6.18 20.09 L4.81 22.02 L4.44 25.52 L4.87 25.97 "
    "L6.82 25.97 L6.76 25.27 L5.87 24.74 L6.12 23.20 L10.07 19.16 "
    "L8.82 22.55 L10.35 25.69 L12.51 25.78 L12.26 25.02 L10.95 24.38 "
    "L10.53 22.81 L12.90 18.94 L12.81 16.89 L15.49 17.01 L15.03 17.34 "
    "L16.74 16.98 L16.52 17.34 L17.77 16.84 L19.23 25.50 L19.90 25.89 "
    "L21.61 25.86 L21.61 25.16 L20.51 24.60 L20.18 23.37 L20.94 17.26 "
    "L21.33 23.20 L22.09 25.36 L22.73 25.75 L24.56 25.69 L24.41 24.99 "
    "L23.37 24.60 L22.94 23.73 L23.01 19.11 L23.67 15.72 L24.74 14.34 "
    "L24.86 14.71 L25.80 12.08 L26.11 12.64 L26.90 8.88 L27.17 9.11 "
    "L26.84 7.34 L27.17 5.88 L28.97 3.33 L28.82 2.52 L27.33 3.22 "
    "L27.90 1.54 Z"
)
_WOLF_GLYPH = (
    "M27.42 0.20 L19.38 5.10 L20.39 6.02 L16.77 9.36 L6.82 12.19 "
    "L0 21.91 L3.80 21.52 L6.09 18.38 L4.44 25.52 L6.82 25.97 "
    "L6.12 23.20 L10.07 19.16 L8.82 22.55 L10.35 25.69 L12.51 25.78 "
    "L10.53 22.81 L12.90 18.94 L12.81 16.89 L17.77 16.84 L19.23 25.50 "
    "L21.61 25.86 L20.18 23.37 L20.94 17.26 L22.09 25.36 L24.56 25.69 "
    "L22.94 23.73 L23.67 15.72 L26.11 12.64 L27.17 5.88 L28.82 2.52 "
    "L27.33 3.22 Z"
)
_WOLF_HEAD = (
    "M3 28 C4 22 7 18 11 15 L14 8 L17 11 L21 5 L23 8 "
    "L28 4 L29 6 L24 11 C21 14 20 17 20 21 L24 28 Z"
)

_FULL_MOUNTAIN_CUTS = [
    "M1 43 L7 38 L10 41 L6 46 Z",
    "M15 40 L19 32 L22 34 L20 41 L17 44 Z",
    "M30 37 L37 24 L40 23 L36 34 L33 40 Z",
    "M40 36 L45 26 L48 27 L44 36 L42 39 Z",
    "M50 40 L54 31 L58 35 L55 41 Z",
]


def _spark(cx: float, cy: float, radius: float) -> str:
    """A four-point star that holds a crisp silhouette in SVG and Pillow."""
    waist = radius * 0.22
    return (
        f"M{cx:g} {cy - radius:g} L{cx + waist:g} {cy - waist:g} "
        f"L{cx + radius:g} {cy:g} L{cx + waist:g} {cy + waist:g} "
        f"L{cx:g} {cy + radius:g} L{cx - waist:g} {cy + waist:g} "
        f"L{cx - radius:g} {cy:g} L{cx - waist:g} {cy - waist:g} Z"
    )


def _moon_layers(*, compact: bool = False) -> list[dict]:
    """The ember wolf moon, with only marks that survive the chosen scale."""
    layers: list[dict] = [
        {"kind": "circle", "clip": True, "cx": MOON_CX, "cy": MOON_CY,
         "r": MOON_R + (0.9 if compact else 1.15), "fill": CREAM},
        {"kind": "circle", "clip": True, "cx": MOON_CX, "cy": MOON_CY,
         "r": MOON_R, "fill": ACCENT},
    ]
    if not compact:
        layers += [
            {"kind": "circle", "clip": True, "cx": MOON_CX - 1.6,
             "cy": MOON_CY - 1.2, "r": 1.15, "fill": SKY, "opacity": 0.20},
            {"kind": "circle", "clip": True, "cx": MOON_CX + 1.3,
             "cy": MOON_CY + 1.4, "r": 0.85, "fill": SKY, "opacity": 0.17},
        ]
    return layers


def _full_emblem() -> list[dict]:
    """The selected Wolf Moon Seal for large website and store artwork."""
    layers: list[dict] = [{"kind": "sky", "fill": SKY}]
    for path, width, colour, opacity in _GALAXY_SWEEPS:
        layers.append({"kind": "stroke", "clip": True, "stroke": colour,
                       "width": width, "opacity": opacity, "d": path})
    for index, (cx, cy, radius) in enumerate(_GALAXY_PARTICLES):
        layers.append({"kind": "circle", "clip": True, "cx": cx, "cy": cy,
                       "r": radius, "fill": CREAM if index % 3 == 0 else DUST})
    for cx, cy, radius in ((10, 11, 1.25), (27, 6.5, 0.9), (36, 15, 0.8)):
        layers.append({"kind": "path", "clip": True, "d": _spark(cx, cy, radius),
                       "fill": CREAM})

    layers += _moon_layers()
    layers.append({"kind": "path", "clip": True, "d": _SNOW, "fill": CREAM})
    for cut in _FULL_MOUNTAIN_CUTS:
        layers.append({"kind": "path", "clip": True, "d": cut, "fill": SKY})
    layers.append({"kind": "path", "clip": True, "d": _INK_RIDGE, "fill": SKY})
    layers.append({"kind": "path", "clip": True, "d": _WOLF_FULL_PATH,
                   "fill": INK, "transform": _WOLF_AT})
    layers.append({"kind": "rim"})
    return layers


def _compact_emblem() -> list[dict]:
    """The app/header drawing: fewer layers, larger subjects, wider gaps."""
    layers: list[dict] = [
        {"kind": "sky", "fill": SKY},
        {"kind": "stroke", "clip": True, "stroke": DUST, "width": 1.7,
         "opacity": 0.72, "d": "M-3 27 C14 19 26 9 43 6"},
        {"kind": "path", "clip": True, "d": _spark(13, 13, 1.1), "fill": CREAM},
        {"kind": "circle", "clip": True, "cx": 25, "cy": 9, "r": 0.65,
         "fill": CREAM},
    ]
    layers += _moon_layers(compact=True)
    layers += [
        {"kind": "path", "clip": True, "d": _COMPACT_MOUNTAIN, "fill": CREAM},
        {"kind": "path", "clip": True,
         "d": "M30 37 L37 24 L41 23 L36 38 Z", "fill": SKY},
        {"kind": "path", "clip": True,
         "d": "M46 39 L51 29 L55 32 L51 41 Z", "fill": SKY},
        {"kind": "path", "clip": True, "d": _INK_RIDGE, "fill": SKY},
        {"kind": "path", "clip": True, "d": _WOLF_GLYPH, "fill": INK,
         "transform": (21.0, 37.5, 0.66)},
        {"kind": "rim"},
    ]
    return layers


def _micro_emblem() -> list[dict]:
    """The 16--32 px favicon drawing: three bold subjects, no miniatures."""
    return [
        {"kind": "sky", "fill": SKY},
        {"kind": "circle", "clip": True, "cx": 49, "cy": 14, "r": 6.2,
         "fill": ACCENT},
        {"kind": "path", "clip": True, "d": _MICRO_MOUNTAIN, "fill": CREAM},
        {"kind": "path", "clip": True, "d": _WOLF_HEAD, "fill": INK,
         "transform": (25.0, 33.0, 0.78)},
        {"kind": "rim"},
    ]


EMBLEM_FULL = _full_emblem()
EMBLEM_COMPACT = _compact_emblem()
EMBLEM_MICRO = _micro_emblem()
# Backwards-compatible public name for callers that only need the large mark.
EMBLEM = EMBLEM_FULL


def _layers(detail: str) -> list[dict]:
    try:
        return {
            "full": EMBLEM_FULL,
            "compact": EMBLEM_COMPACT,
            "micro": EMBLEM_MICRO,
        }[detail]
    except KeyError:
        raise ValueError("detail must be 'full', 'compact', or 'micro'") from None

# --------------------------------------------------------------------------
# path handling: one flattener, shared by both renderers
# --------------------------------------------------------------------------

_TOKEN = re.compile(r"([MLHVQCZ])([^MLHVQCZ]*)", re.I)
_NUM = re.compile(r"[-+]?[0-9]*\.?[0-9]+")


def _flatten(path: str, steps: int = 14) -> list[list[tuple[float, float]]]:
    """M/L/H/V/Q/C/Z into closed polygons.

    Arcs are deliberately NOT supported. An earlier version sampled ``A`` as a
    semicircle, which silently mangled the wolf when its rounded masses were
    written as arc paths; they are native circle layers now. A path containing
    an arc will raise rather than render something subtly wrong.
    """
    if re.search(r"[Aa]", path):
        raise ValueError(
            "arcs are not supported in EMBLEM paths -- use a circle layer, or "
            "express the curve with Q/C"
        )
    contours: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    x = y = 0.0
    for command, raw in _TOKEN.findall(path):
        nums = [float(n) for n in _NUM.findall(raw)]
        upper = command.upper()
        relative = command.islower()
        if upper == "M":
            if current:
                contours.append(current)
            x, y = (x + nums[0], y + nums[1]) if relative else (nums[0], nums[1])
            current = [(x, y)]
            for i in range(2, len(nums) - 1, 2):
                x, y = (x + nums[i], y + nums[i + 1]) if relative else (nums[i], nums[i + 1])
                current.append((x, y))
        elif upper == "L":
            for i in range(0, len(nums) - 1, 2):
                x, y = (x + nums[i], y + nums[i + 1]) if relative else (nums[i], nums[i + 1])
                current.append((x, y))
        elif upper == "H":
            for n in nums:
                x = x + n if relative else n
                current.append((x, y))
        elif upper == "V":
            for n in nums:
                y = y + n if relative else n
                current.append((x, y))
        elif upper == "Q":
            for i in range(0, len(nums) - 3, 4):
                cx, cy, nx, ny = nums[i : i + 4]
                if relative:
                    cx, cy, nx, ny = x + cx, y + cy, x + nx, y + ny
                for step in range(1, steps + 1):
                    t = step / steps
                    u = 1 - t
                    current.append((u * u * x + 2 * u * t * cx + t * t * nx,
                                    u * u * y + 2 * u * t * cy + t * t * ny))
                x, y = nx, ny
        elif upper == "C":
            for i in range(0, len(nums) - 5, 6):
                c1x, c1y, c2x, c2y, nx, ny = nums[i : i + 6]
                if relative:
                    c1x, c1y = x + c1x, y + c1y
                    c2x, c2y = x + c2x, y + c2y
                    nx, ny = x + nx, y + ny
                for step in range(1, steps + 1):
                    t = step / steps
                    u = 1 - t
                    current.append((
                        u ** 3 * x + 3 * u * u * t * c1x + 3 * u * t * t * c2x + t ** 3 * nx,
                        u ** 3 * y + 3 * u * u * t * c1y + 3 * u * t * t * c2y + t ** 3 * ny,
                    ))
                x, y = nx, ny
        elif upper == "Z":
            if current:
                contours.append(current)
                current = []
    if current:
        contours.append(current)
    return [c for c in contours if len(c) >= 3]


def _place(points, transform):
    if not transform:
        return points
    tx, ty, scale = transform
    return [(tx + px * scale, ty + py * scale) for px, py in points]


# --------------------------------------------------------------------------
# renderers
# --------------------------------------------------------------------------


def _clip_mask(px: int, tile: bool) -> Image.Image:
    k = px / VIEWBOX
    mask = Image.new("L", (px, px), 0)
    d = ImageDraw.Draw(mask)
    if tile:
        d.rounded_rectangle([0, 0, px - 1, px - 1], radius=TILE_RADIUS * k, fill=255)
    else:
        d.ellipse([(DISC_CX - DISC_R) * k, (DISC_CY - DISC_R) * k,
                   (DISC_CX + DISC_R) * k, (DISC_CY + DISC_R) * k], fill=255)
    return mask


def draw_mark(
    size: int = 64,
    tile: bool = False,
    detail: str | None = None,
) -> Image.Image:
    """Render an optically chosen badge at ``size`` px.

    Bare marks default to the full seal.  Tiles default to the compact icon;
    callers generating a multi-resolution favicon can request ``micro`` for
    the smallest frames.
    """
    if size < 8:
        raise ValueError("mark size must be at least 8 px")
    if detail is None:
        detail = "compact" if tile else "full"
    size = int(size)
    px = size * _SUPERSAMPLE
    k = px / VIEWBOX
    clip = _clip_mask(px, tile)
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))

    for layer in _layers(detail):
        kind = layer["kind"]
        if kind == "rim":
            rim = Image.new("RGBA", (px, px), (0, 0, 0, 0))
            if not tile:
                ImageDraw.Draw(rim).ellipse(
                    [(DISC_CX - RIM_R) * k, (DISC_CY - RIM_R) * k,
                     (DISC_CX + RIM_R) * k, (DISC_CY + RIM_R) * k],
                    outline=CREAM + (230,), width=max(1, round(RIM_W * k)))
                img.alpha_composite(rim)
            continue

        layer_img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer_img)
        alpha = round(255 * layer.get("opacity", 1.0))

        if kind == "sky":
            # The night sky fills the badge outline, whether disc or tile.
            solid = Image.new("RGBA", (px, px), layer["fill"] + (255,))
            solid.putalpha(clip)
            img.alpha_composite(solid)
            continue
        elif kind == "circle":
            cx, cy, r = layer["cx"], layer["cy"], layer["r"]
            if layer.get("transform"):
                tx, ty, scale = layer["transform"]
                cx, cy, r = tx + cx * scale, ty + cy * scale, r * scale
            draw.ellipse([(cx - r) * k, (cy - r) * k, (cx + r) * k, (cy + r) * k],
                         fill=layer["fill"] + (alpha,))
        elif kind == "path":
            fill = layer["fill"] + (alpha,)
            shape = Image.new("1", (px, px), 0)
            shape_draw = ImageDraw.Draw(shape)
            for contour in _flatten(layer["d"]):
                pts = [(cx * k, cy * k) for cx, cy in _place(contour, layer.get("transform"))]
                one = Image.new("1", (px, px), 0)
                ImageDraw.Draw(one).polygon(pts, fill=1)
                shape = ImageChops.logical_xor(shape, one)
            del shape_draw
            solid = Image.new("RGBA", (px, px), fill)
            layer_img = Image.composite(solid, layer_img, shape)
        elif kind == "stroke":
            for contour in _flatten(layer["d"]):
                pts = [(cx * k, cy * k) for cx, cy in _place(contour, layer.get("transform"))]
                # SVG scales stroke width with the element's transform, so
                # Pillow must too or the galaxy's arms come out heavier here
                # than on the web (Codex review, 2026-09-02).
                stroke_scale = layer["transform"][2] if layer.get("transform") else 1.0
                draw.line(pts, fill=layer["stroke"] + (alpha,),
                          width=max(1, round(layer["width"] * stroke_scale * k)),
                          joint="curve")

        if layer.get("clip", False):
            existing = layer_img.getchannel("A")
            layer_img.putalpha(ImageChops.multiply(existing, clip))
        img.alpha_composite(layer_img)

    return img.resize((size, size), Image.LANCZOS)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def mark_svg(tile: bool = False, detail: str | None = None) -> str:
    """The same optical badge as SVG (viewBox 0 0 64 64)."""
    if detail is None:
        detail = "compact" if tile else "full"
    clip_shape = (
        f'<rect width="{VIEWBOX:g}" height="{VIEWBOX:g}" rx="{TILE_RADIUS:g}"/>'
        if tile
        else f'<circle cx="{DISC_CX:g}" cy="{DISC_CY:g}" r="{DISC_R:g}"/>'
    )
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {VIEWBOX:g} {VIEWBOX:g}" '
        'width="64" height="64" role="img" '
        'aria-label="TinyAssets: Mount Baker under a wolf moon">',
        f'<defs><clipPath id="ta-badge">{clip_shape}</clipPath></defs>',
    ]
    for layer in _layers(detail):
        kind = layer["kind"]
        if kind == "rim":
            if not tile:
                out.append(
                    f'<circle cx="{DISC_CX:g}" cy="{DISC_CY:g}" r="{RIM_R:g}" fill="none" '
                    f'stroke="{CREAM_HEX}" stroke-width="{RIM_W:g}" opacity=".9"/>'
                )
            continue
        opacity = layer.get("opacity", 1.0)
        op = f' opacity="{opacity:g}"' if opacity != 1.0 else ""
        transform = layer.get("transform")

        if kind == "sky":
            out.append(
                f'<rect width="{VIEWBOX:g}" height="{VIEWBOX:g}" rx="{TILE_RADIUS:g}" '
                f'fill="{_hex(layer["fill"])}"/>'
                if tile
                else f'<circle cx="{DISC_CX:g}" cy="{DISC_CY:g}" r="{DISC_R:g}" '
                     f'fill="{_hex(layer["fill"])}"/>'
            )
            continue

        if kind == "circle":
            shape = (
                f'<circle cx="{layer["cx"]:g}" cy="{layer["cy"]:g}" '
                f'r="{layer["r"]:g}" fill="{_hex(layer["fill"])}"{op}/>'
            )
        elif kind == "path":
            shape = f'<path d="{layer["d"]}" fill="{_hex(layer["fill"])}"{op}/>'
        elif kind == "stroke":
            shape = (
                f'<path d="{layer["d"]}" fill="none" '
                f'stroke="{_hex(layer["stroke"])}" stroke-width="{layer["width"]:g}" '
                f'stroke-linecap="round"{op}/>'
            )
        else:
            continue

        # The clip MUST sit on a group outside the transform. SVG resolves
        # clip-path in the element's own user space, so putting both on one
        # element drags the badge outline along with the shape -- which cut the
        # galaxy out of the sky entirely and clipped the wolf, while the Pillow
        # renderer (which masks after placing) drew them correctly.
        if transform:
            tx, ty, scale = transform
            shape = (
                f'<g transform="translate({tx:g} {ty:g}) scale({scale:g})">{shape}</g>'
            )
        if layer.get("clip"):
            shape = f'<g clip-path="url(#ta-badge)">{shape}</g>'
        out.append(shape)
    out.append("</svg>")
    return "".join(out) + "\n"


def create_icon_image(size: int = 64) -> Image.Image:
    """The app tile, using the micro drawing at favicon dimensions."""
    detail = "micro" if size <= 32 else "compact"
    return draw_mark(size, tile=True, detail=detail)


def generate_icon(output_path: str | Path | None = None) -> Path:
    """Generate the multi-size tray ``.ico`` (16, 32, 48, 256).

    Defaults to ``tinyassets/desktop/app.ico``.
    """
    if output_path is None:
        output_path = Path(__file__).parent / "app.ico"
    else:
        output_path = Path(output_path)

    sizes = [16, 32, 48, 256]
    images = [create_icon_image(s) for s in sizes]
    images[-1].save(
        output_path,
        format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=images[:-1],
    )
    return output_path


if __name__ == "__main__":  # pragma: no cover
    print(generate_icon())
