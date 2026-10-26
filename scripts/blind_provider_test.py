#!/usr/bin/env python3
"""Blind test: which providers can the standards detector connect, by address alone?

    python scripts/blind_provider_test.py providers.txt [--json out.json]
    python scripts/blind_provider_test.py --address api.example.com --address ...

Addresses come from the command line or a file (one per line, ``#`` comments),
never from the repo. Each one runs the product detector
(``tinyassets.connection_oauth.model_discovery.detect``) with a fetch that is
GET-only, unauthenticated, follows no redirects, honours ``robots.txt``,
waits between requests to the same host and caps every body. Nothing signs in,
no credential is sent and nothing is paid for.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tinyassets.connection_oauth.model_discovery import detect  # noqa: E402
from tinyassets.connection_oauth.transport import OAuthError  # noqa: E402

AGENT = "TinyAssets-blind-discovery/1 (+https://tinyassets.io)"
SMALL, LARGE = 65536, 8 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class PoliteFetch:
    def __init__(self, delay):
        self.delay = delay
        self.last: dict[str, float] = {}
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.opener = urllib.request.build_opener(_NoRedirect)

    def _wait(self, host):
        pause = self.last.get(host, 0) + self.delay - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        self.last[host] = time.monotonic()

    def _allowed(self, url):
        parts = urlsplit(url)
        if parts.netloc not in self.robots:
            parser = urllib.robotparser.RobotFileParser()
            self._wait(parts.netloc)
            try:
                with self.opener.open(urllib.request.Request(
                        f"https://{parts.netloc}/robots.txt",
                        headers={"User-Agent": AGENT}), timeout=10) as response:
                    parser.parse(response.read(SMALL).decode("utf-8", "replace").splitlines())
            except (urllib.error.URLError, OSError, ValueError):
                parser = None  # no robots.txt: nothing is disallowed
            self.robots[parts.netloc] = parser
        parser = self.robots[parts.netloc]
        return parser is None or parser.can_fetch(AGENT, url)

    def __call__(self, url, *, large=False):
        if not self._allowed(url):
            raise OAuthError("robots_disallowed")
        self._wait(urlsplit(url).netloc)
        request = urllib.request.Request(url, headers={
            "User-Agent": AGENT, "Accept": "application/json"})
        try:
            response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, OSError, ValueError):
            raise OAuthError("unreachable") from None
        with response:
            limit = LARGE if large else SMALL
            raw = response.read(limit + 1)
            status = response.status if hasattr(response, "status") else response.code
            headers = {k.lower(): v for k, v in response.headers.items()}
        if len(raw) > limit:
            raise OAuthError("body_too_large")
        try:
            doc = json.loads(raw) if raw.strip() else None
        except (ValueError, RecursionError):
            doc = None
        return status, doc, headers


def mechanisms(record):
    found = []
    api, sign_in = record["model_api"], record["sign_in"]
    catalogue = api.get("catalogue")
    if catalogue:
        found.append("catalogue:" + ("public" if catalogue["public"] else "auth"))
    for key in ("chat_completions", "responses"):
        if (api.get(key) or {}).get("present"):
            found.append(key)
    if api.get("blanket_auth"):
        found.append(f"blanket-{api['blanket_auth']}")
    if api.get("openapi"):
        found.append("openapi")
    found += ["key:" + s for s in api.get("key_styles") or []]
    prm = sign_in.get("protected_resource")
    if prm and not prm.get("error"):
        found.append("rfc9728:" + prm["via"])
    for server in sign_in.get("authorization_servers") or []:
        if server.get("error"):
            found.append("as-error:" + server["error"])
            continue
        found.append(server["kind"])
        found += [name for name, on in (
            ("dcr", server["dynamic_registration"]),
            ("cimd", server["client_id_metadata_document"]),
            ("device", server["device_authorization"]),
            ("pkce-S256", "S256" in server["pkce_methods"])) if on]
    return found


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file", nargs="?", type=Path)
    parser.add_argument("--address", action="append", default=[])
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between hits per host")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    entries = list(args.address)
    if args.file:
        entries += [line.split("#", 1)[0].strip()
                    for line in args.file.read_text("utf-8").splitlines()]
    entries = [e for e in dict.fromkeys(entries) if e]
    fetch = PoliteFetch(args.delay)
    results = []
    print("| Address | Mechanisms detected | Verdict | Why |")
    print("|---|---|---|---|")
    for entry in entries:
        try:
            record = detect(entry, fetch=fetch)
            chosen = record["chosen"]
            row = {"address": entry, "mechanisms": mechanisms(record),
                   "verdict": chosen["verdict"], "reason": chosen.get("reason", ""),
                   "chosen": chosen}
        except OAuthError as exc:
            row = {"address": entry, "mechanisms": [], "verdict": "not_connectable",
                   "reason": exc.code}
        results.append(row)
        print(f"| {entry} | {' '.join(row['mechanisms']) or '-'} | {row['verdict']} "
              f"| {row['reason'] or '-'} |", flush=True)
    if args.json:
        args.json.write_text(json.dumps(results, indent=1), "utf-8")
    counts = {v: sum(r["verdict"] == v for r in results)
              for v in ("oauth", "api_key", "not_connectable")}
    print(f"\n{len(results)} providers: " + ", ".join(f"{v} {n}" for v, n in counts.items()))


if __name__ == "__main__":
    main()
