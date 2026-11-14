"""Prove the public app URL is `https://tinyassets.io/app` and nothing else.

The app moved off `/mcp/app` on 2026-09-30 with no redirect (founder directive:
no back-compat). That makes the move's failure modes *edge* failures, which
green tests and a green daemon cannot see:

* `/app` never bound at the edge -> the apex website origin answers, so the URL
  looks alive and serves the wrong page (this is the 2026-04-19 P0 shape).
* `/app` bound as an EXACT route -> a Cloudflare route matches the whole URL
  including the query, so `/app?code=...&state=...` (the AuthKit return) and
  `/app?subscribed=1` (the Stripe return) match nothing and land on the website
  origin. The app shell loads; sign-in and billing are dark. No unit test can
  see this -- route-pattern semantics live at the edge.
* the `/app*` wildcard that fixes the above also captures apex assets whose path
  merely starts with `app`, e.g. `/apple-touch-icon.png`, which the Worker has
  to hand back to the website origin.
* `/mcp/app` still answering -> the move did not happen; installed shells keep
  working and nobody notices the split brain until the old path is pulled.
* `/app/*` API routes reachable anonymously -> the app left the `/mcp/` prefix
  the bearer challenge used to sweep, and took its auth boundary with it.

Each check names the observation that would refute it, so a failure says which
layer is wrong rather than "the app is down".

    python scripts/probe_app_surface.py
    python scripts/probe_app_surface.py --base-url https://tinyassets.io --verbose

Exit 0 only when every check passes. The MCP connector endpoint is NOT probed
here — `scripts/mcp_public_canary.py` owns that (Hard Rule 11).
"""

from __future__ import annotations

import argparse
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Protocol

#: The daemon stamps this on the app shell. Its presence is what distinguishes
#: "our app answered" from "the website origin answered 200 with a landing page"
#: — a status code alone cannot tell those apart.
APP_BUILD_HEADER = "X-TinyAssets-Build"

#: An apex asset the website really serves. Its old path started with "app" and
#: so witnessed the route-shape hazard; it left that prefix for the platform's
#: `app*` route (B13), and is still probed as the website's.
APEX_APP_PREFIXED_ASSET = "/icons/apple-touch-icon.png"

#: One app API route, picked because it is pure-read and identity-gated: an
#: anonymous 401 proves the boundary moved with the app.
APP_API_PATH = "/app/me"

RETIRED_APP_PATH = "/mcp/app"

#: What "retired" is allowed to look like. `/mcp/app` sits inside the connector
#: namespace, which challenges everything under it, so anonymously it answers
#: 401 exactly like `/mcp/anything`; with a bearer it would be 404. Both mean
#: "nothing is mounted here". 200 (still serving), 3xx (a redirect) and 5xx
#: (a sick origin) all fail.
RETIRED_OK_STATUSES = frozenset({401, 404})


class _Opener(Protocol):
    def open(self, request: urllib.request.Request, timeout: float): ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: a 30x is itself an observation we must report."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _Observation:
    __slots__ = ("status", "headers", "body", "error")

    def __init__(self, status, headers, body, error):
        self.status = status
        self.headers = headers
        self.body = body
        self.error = error

    def header(self, name: str) -> str | None:
        if self.headers is None:
            return None
        return self.headers.get(name)


def _observe(
    opener: _Opener,
    *,
    method: str,
    url: str,
    timeout: float,
) -> _Observation:
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "Accept": "text/html,application/json",
            "User-Agent": "tinyassets-app-surface-probe/1.0",
        },
    )
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    except (urllib.error.URLError, OSError) as exc:
        return _Observation(None, None, b"", f"{type(exc).__name__}: {exc}")
    try:
        body = response.read()
    except (urllib.error.URLError, OSError) as exc:
        return _Observation(None, None, b"", f"{type(exc).__name__}: {exc}")
    return _Observation(response.code, response.headers, body, None)


def verify_app_surface(
    *,
    base_url: str = "https://tinyassets.io",
    deadline_seconds: float = 60,
    request_timeout: float = 10,
    retry_delay: float = 5,
    opener: _Opener | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    verbose: bool = False,
) -> list[str]:
    """Return an empty list only when every app-surface check passes.

    Retries the whole matrix until ``deadline_seconds`` so a probe run right
    after a Worker deploy is not defeated by edge propagation. ``0`` means one
    attempt, which is what the unit tests use.
    """
    if request_timeout <= 0:
        raise ValueError("request_timeout must be positive")
    if deadline_seconds < 0:
        raise ValueError("deadline_seconds must be non-negative")

    root = base_url.rstrip("/")
    http = opener or urllib.request.build_opener(_NoRedirect)
    single_attempt = deadline_seconds == 0
    deadline = monotonic() + deadline_seconds

    while True:
        failures: list[str] = []

        def check(method: str, path: str) -> _Observation | None:
            remaining = deadline - monotonic()
            if not single_attempt and remaining <= 0:
                failures.append("probe deadline exhausted before matrix completed")
                return None
            timeout = (
                request_timeout if single_attempt else min(request_timeout, remaining)
            )
            seen = _observe(http, method=method, url=root + path, timeout=timeout)
            if verbose:
                print(f"{method} {root}{path} -> status={seen.status} error={seen.error}")
            return seen

        # 1. The app shell answers, and it is OUR origin answering.
        shell = check("GET", "/app")
        if shell is None:
            return failures
        if shell.error is not None:
            failures.append(f"GET /app: transport={shell.error}")
        elif shell.status != 200:
            failures.append(
                f"GET /app: status={shell.status} (expected 200; a 404 here usually "
                "means the Cloudflare Worker has no tinyassets.io/app route and the "
                "website origin answered)"
            )
        elif shell.header(APP_BUILD_HEADER) is None:
            failures.append(
                f"GET /app: 200 without {APP_BUILD_HEADER} — something other than "
                "the daemon served this path"
            )

        # 2. The QUERY-BEARING app URLs reach the daemon. These carry the whole
        #    sign-in and billing flow (`/app?code=…&state=…` from AuthKit,
        #    `/app?subscribed=1` from Stripe), and a Cloudflare route is matched
        #    against the entire URL INCLUDING the query — so an exact
        #    `tinyassets.io/app` route matches only the bare path and sends
        #    every callback to the website origin. Found by review, not by
        #    tests: no unit test can see a route pattern's semantics.
        #
        #    The build header is required here too. A website 200 would
        #    otherwise satisfy this check and hide exactly the failure it
        #    exists to catch.
        for query in ("?code=probe-not-a-real-code&state=probe", "?subscribed=1"):
            seen = check("GET", "/app" + query)
            if seen is None:
                return failures
            if seen.error is not None:
                failures.append(f"GET /app{query}: transport={seen.error}")
            elif seen.status != 200:
                failures.append(
                    f"GET /app{query}: status={seen.status} (expected 200; a "
                    "query-bearing app URL that 404s means the Worker route is "
                    "an exact path instead of tinyassets.io/app* , which breaks "
                    "the OAuth and Stripe returns)"
                )
            elif seen.header(APP_BUILD_HEADER) is None:
                failures.append(
                    f"GET /app{query}: 200 without {APP_BUILD_HEADER} — the "
                    "website origin answered the callback URL, not the daemon"
                )

        # 3. The retired path does not serve the app, and does not redirect.
        #
        #    It is NOT asserted to be 404. `/mcp/app` is inside the connector's
        #    namespace, and the connector challenges that whole namespace, so an
        #    anonymous caller gets the same 401 as any other absent `/mcp/*`
        #    path — `/mcp/anything` included. Carving the retired path out to
        #    make it 404 would be a special case FOR the retired path, which is
        #    the back-compat this move exists to avoid. What must hold is that
        #    it is not the app and not a redirect, so that is what is checked:
        #    a refusal status, no `Location`, and no app build header. A 5xx
        #    fails — that is an outage, not a retirement.
        retired = check("GET", RETIRED_APP_PATH)
        if retired is None:
            return failures
        if retired.error is not None:
            failures.append(f"GET {RETIRED_APP_PATH}: transport={retired.error}")
        else:
            if retired.status not in RETIRED_OK_STATUSES:
                failures.append(
                    f"GET {RETIRED_APP_PATH}: status={retired.status} "
                    f"Location={retired.header('Location')!r} — expected one of "
                    f"{sorted(RETIRED_OK_STATUSES)}. A 200 means the retired path "
                    "still serves the app; a 3xx means a redirect, which is the "
                    "back-compat this move deliberately does not have; a 5xx "
                    "means the origin is sick, not retired"
                )
            if retired.header("Location") is not None:
                failures.append(
                    f"GET {RETIRED_APP_PATH}: Location="
                    f"{retired.header('Location')!r} — no redirect, no alias"
                )
            if retired.header(APP_BUILD_HEADER) is not None:
                failures.append(
                    f"GET {RETIRED_APP_PATH}: carries {APP_BUILD_HEADER} — the "
                    "app is still being served from the retired path"
                )

        # 4. An apex asset whose name starts with "app" still reaches the site.
        asset = check("GET", APEX_APP_PREFIXED_ASSET)
        if asset is None:
            return failures
        if asset.error is not None:
            failures.append(f"GET {APEX_APP_PREFIXED_ASSET}: transport={asset.error}")
        elif asset.status != 200:
            failures.append(
                f"GET {APEX_APP_PREFIXED_ASSET}: status={asset.status} (expected 200 "
                "from the website origin; the app route is bound too widely — use "
                "tinyassets.io/app + tinyassets.io/app/* , never tinyassets.io/app*)"
            )
        elif asset.header(APP_BUILD_HEADER) is not None:
            failures.append(
                f"GET {APEX_APP_PREFIXED_ASSET}: served by the daemon "
                f"({APP_BUILD_HEADER} present) — the Worker route is too wide"
            )

        # 5. The auth boundary moved with the app.
        api = check("GET", APP_API_PATH)
        if api is None:
            return failures
        if api.error is not None:
            failures.append(f"GET {APP_API_PATH}: transport={api.error}")
        elif api.status != 401:
            failures.append(
                f"GET {APP_API_PATH}: status={api.status} (expected 401 for an "
                "anonymous read; the app left the /mcp/ prefix the bearer challenge "
                "swept, so this is the boundary that had to move with it)"
            )

        if not failures or single_attempt:
            return failures
        remaining = deadline - monotonic()
        if remaining <= 0:
            return failures
        sleep(min(retry_delay, remaining))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://tinyassets.io")
    parser.add_argument("--deadline", type=float, default=60)
    parser.add_argument("--request-timeout", type=float, default=10)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    failures = verify_app_surface(
        base_url=args.base_url,
        deadline_seconds=args.deadline,
        request_timeout=args.request_timeout,
        verbose=args.verbose,
    )
    if failures:
        print("\n".join(failures))
        return 1
    print(f"app surface green: {args.base_url.rstrip('/')}/app serves the app, "
          f"{RETIRED_APP_PATH} does not, apex app-prefixed assets are untouched, "
          "and the app API still challenges anonymous reads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
