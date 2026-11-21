#!/usr/bin/env python3
"""Render the site's Open Graph card (1200 x 630) with the real web fonts.

Pillow cannot lay out Fraunces, so the card is an HTML page rendered by
Playwright (Python `playwright`, already used by the site sweep). The card is
the mark, the wordmark, and the one-sentence positioning, on paper.

    python WebSite/brand/render_og.py

Writes WebSite/site-react/public/og-image.png.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from WebSite.brand.mark import mark_svg  # noqa: E402

OUT = REPO / "WebSite" / "site-react" / "public" / "og-image.png"

# The card is one HTML document; its CSS lines are long by nature.
# ruff: noqa: E501
HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..700&family=Source+Sans+3:wght@400;500&family=IBM+Plex+Mono:wght@400;500&display=block" rel="stylesheet">
<style>
  html, body { margin: 0; width: 1200px; height: 630px; background: #14140f; color: #f2efe6; }
  .card { position: relative; width: 1200px; height: 630px; padding: 72px 84px; box-sizing: border-box;
          font-family: "Source Sans 3", sans-serif; }
  .brand { display: flex; align-items: center; gap: 22px; }
  .brand svg { width: 86px; height: 86px; }
  .word { font-family: "Fraunces", serif; font-size: 44px; font-weight: 500; letter-spacing: -0.01em;
          font-variation-settings: "opsz" 48, "SOFT" 30; }
  h1 { font-family: "Fraunces", serif; font-weight: 500; font-size: 84px; line-height: 1.02; letter-spacing: -0.015em;
       margin: 48px 0 24px; max-width: 18ch; font-variation-settings: "opsz" 144, "SOFT" 30; }
  p { font-size: 28px; line-height: 1.35; margin: 0; max-width: 40ch; color: #cdc7ba; }
  .rule { position: absolute; left: 84px; right: 84px; bottom: 48px; border-top: 1px solid rgba(242,239,230,.55);
          padding-top: 16px; display: flex; justify-content: space-between;
          font-family: "IBM Plex Mono", monospace; font-size: 20px; color: #8f8a7e; }
  .rule b { color: #e0703f; font-weight: 500; }
</style></head>
<body><div class="card">
  <div class="brand">__MARK__<span class="word">TinyAssets</span></div>
  <h1>A universe of your own.</h1>
  <p>A cloud agent that runs on the subscription you already pay for, builds any automation to any platform, and learns you as it goes.</p>
  <div class="rule"><span>tinyassets.io</span><span><b>free</b> to found · open source</span></div>
</div></body></html>
"""


def main() -> int:
    html = HTML.replace("__MARK__", mark_svg(tile=False).strip())
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 630}, device_scale_factor=1)
        page.set_content(html, wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT), clip={"x": 0, "y": 0, "width": 1200, "height": 630})
        browser.close()
    print(OUT.relative_to(REPO).as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
