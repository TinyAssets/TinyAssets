"""Brand-parity invariant: every committed mark matches its generator receipt."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote

from . import CheckResult, Invariant, Status

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RECEIPT = REPO_ROOT / "WebSite" / "brand" / "generated-assets.json"
ANDROID_ROOT = REPO_ROOT / "mobile" / "resources" / "android"
TILE_SVG = REPO_ROOT / "WebSite" / "brand" / "mark-tile.svg"
APP_HTML = REPO_ROOT / "tinyassets" / "onboarding" / "app.html"
TEXT_SUFFIXES = {".html", ".py", ".svg", ".tsx", ".webmanifest"}
REQUIRED_SURFACES = {
    "WebSite/site-react/public/favicon.ico",
    "WebSite/site-react/public/icon.svg",
    "WebSite/site-react/public/icons/apple-touch-icon.png",
    "WebSite/site-react/public/site.webmanifest",
    "tinyassets/desktop/app.ico",
    "assets/brand/tinyassets-app.ico",
    "assets/brand/tinyassets-app.icns",
    "mobile/resources/icon.png",
    "mobile/resources/android/mipmap-mdpi/ic_launcher.png",
    "docs/ops/play-assets/icon-512.png",
    "docs/ops/play-assets/feature-graphic-1024x500.png",
}


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_ICON_LINK = re.compile(r"""<link\b[^>]*\brel=["']?(?:shortcut\s+)?icon\b[^>]*>""", re.I)
_BRAND_MARK_DECL = re.compile(r"--brand-mark\s*:\s*([^;}]*)")
_BRAND_DOT_RULE = re.compile(r"\.brand-dot\b[^{}]*\{([^}]*)\}")


def app_badge_data_uri(tile_svg: str) -> str:
    """The data URI the served app uses for its favicon and brand glyph.

    The one definition: render_marks.py writes app.html with it, and the check
    below derives what app.html must contain from the committed mark-tile.svg
    (itself receipt-bound) with it.
    """
    return "data:image/svg+xml," + quote(tile_svg.strip(), safe="/:=,;'#")


def app_badge_problems(app_html: str, tile_svg: str) -> list[str]:
    """What is wrong with the app's badge, checked against the tile mark.

    app.html used to sit in the receipt as a WHOLE-FILE hash, so every feature
    edit to a 7,000-line file had to re-run the brand exporter or fail
    brand-parity -- a second copy of app.html's bytes that only the badge
    needed. The badge is the only part render_marks owns, so it is the only
    part checked, and checked as what the browser uses, not as a substring:

    - comments are ignored, so a commented-out copy cannot satisfy the check;
    - there is exactly ONE favicon link, and it is the current mark;
    - ``--brand-mark`` is declared exactly once, as the current mark;
    - the glyph (``.brand-dot``) paints ``var(--brand-mark)`` and nothing else.
    """
    uri = app_badge_data_uri(tile_svg)
    live = _HTML_COMMENT.sub("", _CSS_COMMENT.sub("", app_html))
    problems = []
    icons = _ICON_LINK.findall(live)
    if icons != [f'<link rel="icon" href="{uri}" />']:
        problems.append(
            f"app.html must have exactly one favicon link, the current tile mark "
            f"(found {len(icons)})"
        )
    marks = _BRAND_MARK_DECL.findall(live)
    if marks != [f'url("{uri}")']:
        problems.append(
            f"app.html must declare --brand-mark exactly once, as the current tile "
            f"mark (found {len(marks)})"
        )
    glyphs = _BRAND_DOT_RULE.findall(live)
    if not glyphs or any(
        "var(--brand-mark)" not in rule or "url(" in rule for rule in glyphs
    ):
        problems.append("app.html .brand-dot must paint var(--brand-mark) and no other url()")
    return problems


def _sha256(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


class BrandParityInvariant(Invariant):
    name = "brand-parity"
    description = "Every website, desktop, mobile, and store mark matches one source."
    pre_commit_scope = True
    poll_interval_s = None
    auto_heal = False

    def _check(self) -> CheckResult:
        if not RECEIPT.is_file():
            return CheckResult(
                status=Status.VIOLATED,
                message="brand receipt missing; run python WebSite/brand/render_marks.py",
                evidence={"missing": [RECEIPT.relative_to(REPO_ROOT).as_posix()]},
            )

        receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
        generated = receipt.get("generated", {})
        problems: list[str] = []
        if receipt.get("canonical_source") != "tinyassets/desktop/icon_gen.py":
            problems.append("canonical_source is not tinyassets/desktop/icon_gen.py")
        if not re.fullmatch(r"[0-9a-f]{12}", str(receipt.get("mark_version", ""))):
            problems.append("mark_version is not a 12-hex content fingerprint")
        omitted = sorted(REQUIRED_SURFACES - set(generated))
        problems.extend(f"receipt omits required surface: {path}" for path in omitted)

        for group in ("generators", "generated"):
            entries = receipt.get(group)
            if not isinstance(entries, dict):
                problems.append(f"receipt field {group} is not an object")
                continue
            for relative, expected in entries.items():
                path = REPO_ROOT / relative
                if not path.is_file():
                    problems.append(f"missing {group[:-1]}: {relative}")
                elif _sha256(path) != expected:
                    problems.append(f"drifted {group[:-1]}: {relative}")

        actual_android = {
            path.relative_to(REPO_ROOT).as_posix()
            for path in ANDROID_ROOT.rglob("*.png")
        }
        recorded_android = {
            path
            for path in generated
            if path.startswith("mobile/resources/android/") and path.endswith(".png")
        }
        for path in sorted(actual_android ^ recorded_android):
            problems.append(f"Android receipt set differs: {path}")

        if not TILE_SVG.is_file() or not APP_HTML.is_file():
            problems.append("mark-tile.svg or app.html is missing")
        else:
            problems.extend(
                app_badge_problems(
                    APP_HTML.read_text(encoding="utf-8"),
                    TILE_SVG.read_text(encoding="utf-8").replace("\r\n", "\n"),
                )
            )

        if problems:
            return CheckResult(
                status=Status.VIOLATED,
                message=(
                    f"{len(problems)} brand parity problem(s); run "
                    "python WebSite/brand/render_marks.py"
                ),
                evidence={"problems": problems},
            )
        return CheckResult(
            status=Status.OK,
            message=f"{len(generated)} generated brand artifact(s) match",
            evidence={"checked": len(generated)},
        )
