"""Required real-user sign-in check; never uses the canary sign-in door.

Install: python -m pip install httpx==0.28.1 playwright==1.58.0
         python -m playwright install chromium
Run:     python scripts/live_signin_probe.py
Requires TINYASSETS_CANARY_EMAIL, TINYASSETS_CANARY_PASSWORD and
TINYASSETS_CANARY_USER_ID. No WorkOS API key, saved browser session or bypass.
CAPTCHA, MFA and email verification fail closed; see docs/host-actions.md.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import secrets
import sys
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

BASE = "https://tinyassets.io"


class Refused(Exception):
    """Only sanitized observations may cross this boundary into the log."""


class Probe:
    def __init__(self, email, password, user_id, *, base=BASE):
        self.email, self.password, self.user_id = email, password, user_id
        self.base = base
        self.redirect = base + "/app"
        self.verifier = secrets.token_urlsafe(48)
        self.state = secrets.token_urlsafe(24)
        self.hidden = [email, password, self.verifier, self.state]

    def safe(self, value):
        text = str(value)
        for secret in sorted(filter(None, self.hidden), key=len, reverse=True):
            text = text.replace(secret, "[redacted]")
        text = re.sub(r"https?://\S+", "[url redacted]", text)
        text = re.sub(r"\beyJ[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+", "[redacted]", text)
        text = re.sub(
            r"\b(?:sk_|authkit_authz_code_|pending_authentication_token)[A-Za-z0-9_-]+",
            "[redacted]",
            text,
        )
        return " ".join(text.split())[:1000]

    def fail(self, step, detail):
        raise Refused(f"{step}: {self.safe(detail)}")

    def response(self, response, step, *, as_json=True):
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            for key in ("access_token", "refresh_token", "session_ref", "code", "id_token"):
                if isinstance(payload.get(key), str):
                    self.hidden.append(payload[key])
        if response.status_code != 200:
            if isinstance(payload, dict):
                # Do not dump arbitrary payloads: they may contain credentials.
                detail = {
                    k: payload[k]
                    for k in ("error", "error_description", "message", "detail")
                    if isinstance(payload.get(k), str)
                }
                detail = json.dumps(detail) if detail else "no error description"
            else:
                detail = (
                    response.text
                    if "text/html" not in response.headers.get("content-type", "")
                    else "HTML error response"
                )
            self.fail(step, f"HTTP {response.status_code}: {detail}")
        if as_json and not isinstance(payload, dict):
            self.fail(step, "HTTP 200: expected a JSON object")
        return payload

    def request(self, client, method, url, step, **kwargs):
        try:
            return client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            # Transport exceptions can contain URLs, codes and headers.
            self.fail(step, f"transport failure ({type(exc).__name__})")

    def config(self, client):
        response = self.request(client, "GET", self.redirect, "app config")
        self.response(response, "app config", as_json=False)
        # app_recovery.js has a server-injected JSON literal, not executable config.
        match = re.search(r"const cfg=(\{)", response.text)
        try:
            cfg, _ = json.JSONDecoder().raw_decode(response.text[match.start(1) :])
        except (AttributeError, ValueError):
            self.fail("app config", "missing server-injected AuthKit config")
        if not isinstance(cfg, dict) or not cfg.get("configured"):
            self.fail("app config", "AuthKit not configured")
        for field in ("issuer", "authorization_endpoint", "client_id", "resource", "scopes"):
            if not isinstance(cfg.get(field), str) or not cfg[field]:
                self.fail("app config", f"missing {field}")
        issuer = urlsplit(cfg["issuer"])
        if issuer.scheme != "https" or not issuer.hostname or issuer.username or issuer.password:
            self.fail("app config", "invalid AuthKit issuer")
        if cfg["authorization_endpoint"] != cfg["issuer"].rstrip("/") + "/oauth2/authorize":
            self.fail("app config", "unexpected authorization endpoint")
        return cfg

    def authorize_url(self, cfg):
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        return (
            cfg["authorization_endpoint"]
            + "?"
            + urlencode(
                {
                    "client_id": cfg["client_id"],
                    "redirect_uri": self.redirect,
                    "response_type": "code",
                    "scope": cfg["scopes"],
                    "resource": cfg["resource"],
                    "state": self.state,
                    "nonce": secrets.token_urlsafe(24),
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    def callback_code(self, url):
        parts = urlsplit(url)
        if parts.scheme + "://" + parts.netloc + parts.path != self.redirect or parts.fragment:
            self.fail("callback", "unexpected redirect URI")
        query = parse_qs(parts.query, keep_blank_values=True)
        self.hidden.extend(query.get("code", []))
        if query.get("state") != [self.state]:
            self.fail("callback", "missing, duplicate or mismatched state")
        if "error" in query:
            self.fail("authorize", " ".join(query["error"] + query.get("error_description", [])))
        codes = query.get("code", [])
        if len(codes) != 1 or not codes[0]:
            self.fail("callback", "missing or duplicate authorization code")
        return codes[0]

    def exchange(self, client, callback):
        code = self.callback_code(callback)
        # Exercise the real query-bearing edge route, without executing the app
        # JS (which could consume the code itself or start unrelated app work).
        response = self.request(client, "GET", callback, "callback /app")
        self.response(response, "callback /app", as_json=False)
        if not response.headers.get("x-tinyassets-build"):
            self.fail("callback /app", "HTTP 200 without X-TinyAssets-Build (wrong origin)")
        response = self.request(
            client,
            "POST",
            self.base + "/app/token",
            "token exchange",
            headers={"Origin": self.base, "Content-Type": "application/json"},
            json={"code": code, "code_verifier": self.verifier, "redirect_uri": self.redirect},
        )
        payload = self.response(response, "token exchange")
        access = payload.get("access_token")
        if not isinstance(access, str) or not access.strip():
            self.fail("token exchange", "HTTP 200 without access_token")
        # A cookie must not mask a rejected bearer. /app/me must authenticate
        # exactly the access token returned by the public code exchange.
        client.cookies.clear()
        response = self.request(
            client,
            "GET",
            self.base + "/app/me",
            "identity /app/me",
            headers={"Authorization": f"Bearer {access}"},
        )
        identity = self.response(response, "identity /app/me")
        if identity.get("principal_id") != self.user_id:
            self.fail("identity /app/me", "HTTP 200 for a missing or different principal_id")

    def hosted_login(self, page, cfg, *, timeout=60):
        callback = []
        navigation_errors = []
        http_refusals = []

        def intercept(route):
            request = route.request
            if request.url.startswith(self.redirect + "?"):
                callback.append(request.url)
                route.fulfill(status=200, content_type="text/html", body="Sign-in probe callback")
                return
            if not request.is_navigation_request():
                route.continue_()
                return
            try:
                # Playwright only routes the FIRST URL of an HTTP redirect
                # chain. Do not let a 302 silently execute the real app JS and
                # consume the code. Each redirect becomes a fresh navigation,
                # retaining the browser context's upstream cookies.
                response = route.fetch(max_redirects=0, timeout=20000)
                if response.status in (301, 302, 303):
                    target = urljoin(request.url, response.headers.get("location", ""))
                    if not response.headers.get("location"):
                        navigation_errors.append("redirect without Location")
                        route.fulfill(status=502, body="Invalid hosted redirect")
                        return
                    if target.startswith(self.redirect + "?"):
                        callback.append(target)
                        route.fulfill(
                            status=200, content_type="text/html", body="Sign-in probe callback"
                        )
                    elif urlsplit(target)[:2] == urlsplit(cfg["issuer"])[:2]:
                        route.fulfill(
                            status=200,
                            content_type="text/html",
                            body=(
                                '<meta http-equiv="refresh" content="0;url='
                                + html.escape(target, quote=True)
                                + '">'
                            ),
                        )
                    else:
                        navigation_errors.append("unexpected hosted redirect origin")
                        route.fulfill(status=502, body="Unexpected hosted redirect")
                elif 300 <= response.status < 400:
                    navigation_errors.append(f"unsupported hosted redirect HTTP {response.status}")
                    route.fulfill(status=502, body="Unsupported hosted redirect")
                else:
                    if response.status >= 400:
                        http_refusals.append(response.status)
                    route.fulfill(response=response)
            except Exception as exc:
                navigation_errors.append(f"navigation transport failure ({type(exc).__name__})")
                route.fulfill(status=502, body="Hosted navigation failed")

        page.route("**/*", intercept)
        stage = "authorize"
        try:
            response = page.goto(self.authorize_url(cfg), wait_until="domcontentloaded")
            if navigation_errors:
                self.fail(stage, navigation_errors[0])
            if response and response.status >= 400:
                self.fail(stage, f"HTTP {response.status}: {page.locator('body').inner_text()}")
            deadline = time.monotonic() + timeout
            submitted = set()
            while time.monotonic() < deadline:
                if navigation_errors:
                    self.fail(stage, navigation_errors[0])
                if callback:
                    return callback[0]
                if http_refusals:
                    self.fail(
                        stage, f"HTTP {http_refusals[-1]}: " + page.locator("body").inner_text()
                    )
                # Only enter credentials on the deployed app's AuthKit origin.
                if urlsplit(page.url)[:2] != urlsplit(cfg["issuer"])[:2]:
                    self.fail(stage, "unexpected hosted-login origin")
                alert = page.locator('[role="alert"]')
                if alert.count() and alert.first.is_visible() and alert.first.inner_text().strip():
                    self.fail(stage, alert.first.inner_text())
                email = page.locator('input[type="email"]')
                password = page.locator('input[type="password"]')
                if password.count() and password.first.is_visible() and "password" not in submitted:
                    stage = "hosted password"
                    if email.count() and email.first.is_visible():
                        email.first.fill(self.email)
                    password.first.fill(self.password)
                    page.locator('button[type="submit"]').first.click()
                    submitted.add("password")
                elif email.count() and email.first.is_visible() and "email" not in submitted:
                    stage = "hosted email"
                    email.first.fill(self.email)
                    page.locator('button[type="submit"]').first.click()
                    submitted.add("email")
                else:
                    consent = page.get_by_role(
                        "button", name=re.compile(r"^(Allow|Authorize|Accept)$", re.I)
                    )
                    if (
                        "password" in submitted
                        and "consent" not in submitted
                        and consent.count()
                        and consent.first.is_visible()
                    ):
                        stage = "hosted consent"
                        consent.first.click()
                        submitted.add("consent")
                page.wait_for_timeout(200)
            self.fail(
                stage, "no OAuth callback; hosted page says: " + page.locator("body").inner_text()
            )
        except Refused:
            raise
        except Exception as exc:
            # Playwright's call log includes fill values and callback URLs.
            try:
                visible = page.locator("body").inner_text(timeout=1000)
            except Exception:
                visible = "hosted page unavailable"
            self.fail(
                stage,
                f"browser failure ({type(exc).__name__}); " + visible,
            )


def main():
    names = ("TINYASSETS_CANARY_EMAIL", "TINYASSETS_CANARY_PASSWORD", "TINYASSETS_CANARY_USER_ID")
    values = [os.environ.get(name, "") for name in names]
    missing = [name for name, value in zip(names, values) if not value.strip()]
    if missing:
        print("FAIL configuration: missing " + ", ".join(missing), file=sys.stderr)
        return 1
    probe = Probe(*values)
    try:
        from playwright.sync_api import sync_playwright

        with httpx.Client(timeout=20, follow_redirects=False) as client:
            cfg = probe.config(client)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    context = browser.new_context(service_workers="block")
                    page = context.new_page()
                    page.set_default_timeout(10000)
                    callback = probe.hosted_login(page, cfg)
                    probe.exchange(client, callback)
                finally:
                    browser.close()
    except Refused as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"FAIL probe runtime: {type(exc).__name__}; "
            "install the pinned dependencies and Chromium",
            file=sys.stderr,
        )
        return 1
    print("PASS AuthKit hosted password -> PKCE /app/token -> bearer /app/me: pinned identity")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
