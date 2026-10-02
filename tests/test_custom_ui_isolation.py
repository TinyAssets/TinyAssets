"""The isolation boundary a user-authored UI bundle runs behind.

A bundle is arbitrary code written by somebody the viewer may never have met, so
these assertions are the containment itself, not a nicety. Each one is written
against the property that holds (an opaque origin, no network of its own) rather
than against the exact spelling of a policy string, so a reordered CSP still
passes and a *weakened* one still fails.

What this file cannot prove: that a real browser enforces the policy. That needs
a rendered conversation through the live connector. These are the preconditions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tinyassets.onboarding import onboarding_routes, render_app_html
from tinyassets.onboarding.ui_frame import BOOTSTRAP_HTML, FRAME_CSP, FRAME_HEADERS

APP_UI = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")


def _directives(policy: str) -> dict[str, list[str]]:
    parsed: dict[str, list[str]] = {}
    for part in policy.split(";"):
        tokens = part.split()
        if tokens:
            parsed[tokens[0]] = tokens[1:]
    return parsed


def test_frame_document_is_an_opaque_origin_with_no_network() -> None:
    frame = _directives(FRAME_CSP)

    # The whole boundary: sandboxed, and NEVER with allow-same-origin. With that
    # grant the frame would share the app's origin and could read the access
    # token out of sessionStorage.
    assert "sandbox" in frame, FRAME_CSP
    # allow-forms only lets a <form> fire `submit` for the bundle's handler;
    # form-action 'none' (below) refuses every real submission.
    assert frame["sandbox"] == ["allow-scripts", "allow-forms"], frame["sandbox"]

    # No network of its own, so the only way out is the bridge. An image URL is a
    # GET a bundle could smuggle data through, so remote images are refused too.
    # Every source is local to the browser: data:, and blob: URLs the frame
    # mints from bytes the parent posted. No host, no 'self', no scheme that
    # leaves the device -- in ANY directive, so a new one cannot open a hole.
    local = {"data:", "blob:", "'unsafe-inline'", "'unsafe-eval'", "'wasm-unsafe-eval'",
             "'none'"}
    for directive, sources in frame.items():
        if directive.endswith("-src"):
            assert set(sources) <= local, (directive, sources)
    assert set(frame["connect-src"]) == {"data:", "blob:"}
    assert frame["form-action"] == ["'none'"]
    assert frame["default-src"] == ["'none'"]
    assert set(frame["img-src"]) == {"data:", "blob:"}
    # Workers come only from blob: URLs the frame minted; a worker scope has no
    # WebRTC constructor and inherits this same network policy.
    assert frame["worker-src"] == ["blob:"]
    assert "script-src-elem" not in frame and "frame-src" not in frame

    # frame-src is absent, so it falls back to default-src 'none': the bundle
    # cannot nest a frame to shop for a weaker context.
    assert "frame-src" not in frame and "child-src" not in frame
    assert frame["frame-ancestors"] == ["'self'"]
    assert frame["base-uri"] == ["'none'"]


def test_frame_response_carries_the_policy_and_no_user_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert FRAME_HEADERS["Content-Security-Policy"] == FRAME_CSP
    assert FRAME_HEADERS["Cache-Control"] == "no-store"
    assert FRAME_HEADERS["X-Content-Type-Options"] == "nosniff"

    # The sandbox rides the RESPONSE, so a direct top-level navigation to this
    # route is sandboxed too -- an iframe attribute alone would not cover that.
    assert "sandbox" in FRAME_HEADERS["Content-Security-Policy"]

    # The document is fixed. No placeholder and nowhere for a bundle to be
    # inlined server-side: the parent posts it in after "ready".
    assert "__TA_" not in BOOTSTRAP_HTML
    assert 'type: "ready"' in BOOTSTRAP_HTML

    # And the served body IS that constant -- nothing per-request is woven in, so
    # there is no request-shaped path for content to reach this origin.
    import asyncio

    from tinyassets.onboarding.ui_frame import handle_ui_frame

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    served = asyncio.run(handle_ui_frame(object()))
    assert served.body.decode("utf-8") == BOOTSTRAP_HTML


def test_frame_closes_the_egress_channels_csp_does_not_cover() -> None:
    """WebRTC and DNS prefetch are not `connect-src` traffic.

    ICE gathering resolves attacker-controlled STUN hostnames, so a bundle could
    encode what the bridge showed it into DNS lookups with no permission prompt
    and nothing for the policy to block (Codex, 2026-09-26). There is no CSP
    directive for it, so the capability is removed from the realm instead.
    """
    assert FRAME_HEADERS["X-DNS-Prefetch-Control"] == "off"

    # Removed with a non-configurable, non-writable definition, so the bundle
    # cannot simply assign them back.
    assert "RTCPeerConnection" in BOOTSTRAP_HTML
    assert "webkitRTCPeerConnection" in BOOTSTRAP_HTML
    assert "configurable: false" in BOOTSTRAP_HTML
    assert "writable: false" in BOOTSTRAP_HTML

    # The removal only holds because there is no route to a pristine realm. Each
    # of these would hand one back, and each is refused by this document's policy
    # rather than by the bootstrap.
    frame = _directives(FRAME_CSP)
    assert "frame-src" not in frame          # falls back to default-src 'none'
    assert "child-src" not in frame
    # A worker realm is allowed from blob: only, and a worker scope exposes no
    # RTCPeerConnection to hand back (proved in a real browser:
    # test_custom_ui_real_browser.py, the worker's own `typeof RTCPeerConnection`).
    assert frame["worker-src"] == ["blob:"]
    assert frame["default-src"] == ["'none'"]
    assert "allow-popups" not in frame["sandbox"]

    # And the removal happens before any bundle code can run.
    assert BOOTSTRAP_HTML.index("RTCPeerConnection") < BOOTSTRAP_HTML.index("function start(")


def test_frame_route_is_registered_for_reads_only() -> None:
    frame_routes = [r for r in onboarding_routes() if getattr(r, "path", "") == "/app/ui-frame"]
    assert len(frame_routes) == 1
    assert set(frame_routes[0].methods) == {"GET", "HEAD"}


def test_app_page_grants_frames_but_keeps_nonce_only_script() -> None:
    _html, policy = render_app_html()
    app = _directives(policy)

    # The one grant a bundle needs, and it reaches only this origin's fixed
    # bootstrap.
    assert app["frame-src"] == ["'self'"]

    # Deliberately unchanged: even a bug that inserted bundle script into this
    # page would not execute it, because nothing carries the per-request nonce.
    assert len(app["script-src"]) == 1
    assert app["script-src"][0].startswith("'nonce-")
    assert "'unsafe-inline'" not in app["script-src"]
    assert "'unsafe-eval'" not in app["script-src"]


def test_bundle_source_never_enters_the_app_document() -> None:
    # The frame is the ONLY renderer. A bundle field assigned to innerHTML,
    # document.write, eval or a Function constructor in the parent would put
    # somebody else's code on the app's own origin.
    for forbidden in ("document.write", "eval(", "new Function", "insertAdjacentHTML"):
        assert forbidden not in APP_UI, forbidden

    # innerHTML does appear -- inside the frame's bootstrap, not here.
    assert "innerHTML" not in APP_UI
    assert "innerHTML" in BOOTSTRAP_HTML

    # The sandbox attribute is the second, independent lock. Assert the property
    # (no same-origin grant), not the literal string.
    sandbox = re.search(r'SANDBOX:"([^"]*)"', APP_UI)
    assert sandbox, "AppUI must declare the sandbox it applies"
    grants = sandbox.group(1).split()
    assert grants == ["allow-scripts", "allow-forms"], grants
    assert 'setAttribute("sandbox",this.SANDBOX)' in APP_UI


def test_frame_handler_honours_the_dark_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from tinyassets.onboarding.ui_frame import handle_ui_frame

    monkeypatch.delenv("TINYASSETS_ONBOARDING_APP", raising=False)
    off = asyncio.run(handle_ui_frame(object()))
    assert off.status_code == 404
    assert "Content-Security-Policy" not in off.headers

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    on = asyncio.run(handle_ui_frame(object()))
    assert on.status_code == 200
    assert on.headers["Content-Security-Policy"] == FRAME_CSP


def test_the_home_transition_funnel_revokes_the_bridge() -> None:
    """`AppUI.homeChanged` is only a guard if the app actually calls it.

    A behavioural test can drive `homeChanged` directly and pass while nothing in
    the app ever reaches it. The same account CAN move home mid-session -- the
    status poll observes it -- so the one funnel every transition goes through has
    to be the caller.
    """
    html = Path("tinyassets/onboarding/app.html").read_text(encoding="utf-8")

    body = re.search(r"function setQueueScope\(next\)\{(.*?)\n  \}", html, re.S)
    assert body, "setQueueScope must still be the single home-transition funnel"
    assert "AppUI.homeChanged(value)" in body.group(1), body.group(1)

    # And it must fire on a CHANGE, not on every call -- re-reporting the same
    # home would tear down a working bundle on every status poll.
    assert "value!==queueScope" in body.group(1)

    # There are exactly two writers of the app's home, and BOTH revoke. A third
    # one appearing without a revoke is the bug this pins: it would move the home
    # under a mounted bundle and leave its bridge live.
    assignments = [
        line for line in html.splitlines()
        if re.search(r"^\s*queueScope\s*=", line)
    ]
    assert len(assignments) == 2, assignments

    clear = re.search(r"function clearAccountScopedState\(\)\{(.*?)\n  \}", html, re.S)
    assert clear, "the account-scoped clear must still be a function"
    assert 'queueScope=""' in clear.group(1)
    assert "AppUI.reset()" in clear.group(1), clear.group(1)


def test_bundle_bounds_are_the_servers_bounds() -> None:
    """The JS bounds are the Python bounds, not a coincidence.

    The library has NO bound -- not a count of UIs and not a byte total. What is
    bounded is one UI, on both sides, and the two sets of numbers must agree so
    raising either without the other fails here instead of at a user's write.
    """
    from tinyassets import custom_agents

    def constant(name: str) -> int:
        found = re.search(rf"\b{name}:(\d+)", APP_UI)
        assert found, name
        return int(found.group(1))

    assert constant("MAX_TEXT_BYTES") == custom_agents.APP_UI_MAX_COMPONENT_TEXT_BYTES
    assert constant("MAX_ASSET_BYTES") == custom_agents.APP_UI_MAX_ASSET_BYTES
    assert constant("MAX_UI_ASSET_BYTES") == custom_agents.APP_UI_MAX_UI_ASSET_BYTES
    assert constant("MAX_ASSET_FILES") == custom_agents.APP_UI_MAX_ASSET_FILES
    # The 49,152-byte bundle bound that made a game impossible is gone.
    assert "MAX_BUNDLE_BYTES" not in APP_UI and "49152" not in APP_UI

    # No library-wide ceiling on either side, by bytes or by count.
    assert not hasattr(custom_agents, "MAX_APP_UI_LIBRARY_BYTES")
    assert "MAX_LIBRARY_BYTES" not in APP_UI
    assert "LIBRARY_LIMIT" not in APP_UI
    assert "remove one first" not in APP_UI

    # Sizes are measured the way the server measures them. Counting UTF-16
    # units accepted multi-byte bundles that the byte cap then refused.
    assert "new TextEncoder().encode(String(value)).length" in APP_UI
    assert "JSON.stringify(component).length" not in APP_UI
