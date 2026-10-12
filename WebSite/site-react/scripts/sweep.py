#!/usr/bin/env python3
"""Playwright sweep of the static export (dev machine; needs `playwright` for Python).

Serves `out/` on a local port, opens every public route and alias at desktop
and phone widths, and asserts:

- zero console errors and zero console warnings on every route;
- the nav, the H1 and the footer render;
- no horizontal overflow at 390 px or 1280 px;
- every alias lands on its destination.

Screenshots go to the directory given by --shots (optional).

    python scripts/sweep.py --shots ../../out-shots
"""
from __future__ import annotations

import argparse
import http.server
import socketserver
import sys
import threading
from functools import partial
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "out"
PORT = 4323

ROUTES = [
    "/", "/start/", "/connect/", "/build/", "/commons/",
    "/developers/", "/fine-print/", "/legal/", "/account/",
]
ALIASES = {
    "/host/": "/start/",
    "/soul/": "/build/", "/graph/": "/build/", "/notebook/": "/build/",
    "/goals/": "/commons/", "/goal/": "/commons/", "/catalog/": "/commons/",
    "/patterns/": "/commons/", "/wiki/": "/commons/",
    "/alliance/": "/developers/", "/contribute/": "/developers/",
    "/loop/": "/fine-print/", "/patch-loop/": "/fine-print/", "/proof/": "/fine-print/",
    "/status/": "/fine-print/", "/economy/": "/fine-print/",
}
WIDTHS = {"phone": 390, "desktop": 1280}


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):  # noqa: D401
        pass


class _Server(socketserver.ThreadingTCPServer):
    # Threaded on purpose: the alias pages redirect while their first request
    # is still open, and a single-threaded server deadlocks on the overlap.
    daemon_threads = True
    allow_reuse_address = True


def serve() -> _Server:
    handler = partial(Quiet, directory=str(OUT))
    srv = _Server(("127.0.0.1", PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=Path)
    args = ap.parse_args()
    if not (OUT / "index.html").is_file():
        print("out/index.html missing: run `npm run build` first", file=sys.stderr)
        return 2
    srv = serve()
    base = f"http://127.0.0.1:{PORT}"
    failures: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for label, width in WIDTHS.items():
                ctx = browser.new_context(viewport={"width": width, "height": 900})
                page = ctx.new_page()
                errs: list[str] = []
                warns: list[str] = []
                def on_console(msg, errs=errs, warns=warns):
                    if msg.type == "error":
                        errs.append(msg.text)
                    elif msg.type == "warning":
                        warns.append(msg.text)

                page.on("console", on_console)
                page.on("pageerror", lambda e: errs.append(str(e)))
                for route in ROUTES:
                    errs.clear()
                    warns.clear()
                    page.goto(base + route, wait_until="networkidle")
                    # Font/MCP fetches to other origins are expected to fail offline; ignore those.
                    net = [
                        e for e in errs
                        if "Failed to load resource" not in e and "net::ERR" not in e
                    ]
                    if net:
                        failures.append(f"{label} {route}: console errors {net}")
                    if warns:
                        failures.append(f"{label} {route}: console warnings {warns}")
                    if not page.locator("header nav, header button").count():
                        failures.append(f"{label} {route}: no nav")
                    h1s = page.locator("main h1").count()
                    if h1s != 1:
                        failures.append(f"{label} {route}: expected one H1, got {h1s}")
                    if not page.locator("footer").count():
                        failures.append(f"{label} {route}: no footer")
                    overflow = page.evaluate(
                        "document.documentElement.scrollWidth"
                        " - document.documentElement.clientWidth"
                    )
                    if overflow > 0:
                        failures.append(f"{label} {route}: horizontal overflow {overflow}px")
                    if args.shots:
                        args.shots.mkdir(parents=True, exist_ok=True)
                        name = route.strip("/").replace("/", "-") or "home"
                        shot = args.shots / f"{label}-{name}.png"
                        page.screenshot(path=str(shot), full_page=True)
                    print(
                        f"ok  {label:7} {route}  errs={len(net)} "
                        f"warns={len(warns)} overflow={overflow}"
                    )
                if label == "desktop":
                    for src, dest in ALIASES.items():
                        page.goto(base + src, wait_until="load")
                        page.wait_for_url(f"**{dest}", timeout=6000)
                        if not page.url.endswith(dest):
                            failures.append(f"alias {src} landed on {page.url}")
                        else:
                            print(f"ok  alias   {src} -> {dest}")
                ctx.close()
            browser.close()
    finally:
        srv.shutdown()
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nsweep clean: errs 0, warns 0, no overflow, all aliases land")
    return 0


if __name__ == "__main__":
    sys.exit(main())
