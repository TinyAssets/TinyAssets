"""The web app's ES modules, served as static files from ``tinyassets/onboarding/app/``.

app.html carries the whole web app in one inline script. The split
(docs/design-notes/2026-10-01-split-app-html-into-es-modules.md) moves that
script into native ES modules the page imports from ``/app/m/<build>/<name>.js``.
This module is S0: the route, its allowlist and the CSP grant, with nothing
loaded yet.

* **Allowlist, not a path.** Only basenames of ``.js`` files present in the
  module directory are served (``[a-z0-9_]+.js``); there is no path join of
  request input, so ``..``, ``/`` or an encoded variant can only ever 404.
* **Content-keyed.** ``<build>`` is a hash of the module files THIS process
  serves (``build_segment``), not the deployment receipt: during a deploy the
  new image runs before the receipt moves, and a receipt-keyed URL would have
  cached the new bytes forever under the old key (Codex on #4281). A request
  for another valid content hash redirects without caching to the current URL.
  New bytes are never cached under an older page's URL. Current responses stay
  cacheable forever (``immutable``); the independent shell recovery listener
  handles module load failure and replaces shells from an earlier build.
* **Public.** Like ``/app`` and ``/app/sw.js`` the modules load before any
  bearer exists. They are static, carry no secret and no identity; the auth
  middleware exempts exactly this path shape (``is_module_path``).
"""

from __future__ import annotations

import functools
import hashlib
import re
from pathlib import Path
from typing import Any

MODULE_DIR = Path(__file__).resolve().parent / "app"
_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}\.js$")
#: Exactly one build segment and one module basename; nothing deeper.
_PATH_RE = re.compile(r"^/app/m/[A-Za-z0-9._-]{1,64}/[a-z0-9_]{1,64}\.js$")


def module_names() -> frozenset[str]:
    """The servable basenames: ``.js`` files present in the module directory."""
    if not MODULE_DIR.is_dir():
        return frozenset()
    # Never a symlink: the contract is "files inside this directory", and a
    # link could publish a file from anywhere else (Codex on #4281).
    return frozenset(
        p.name for p in MODULE_DIR.iterdir()
        if not p.is_symlink() and p.is_file() and _NAME_RE.fullmatch(p.name)
    )


@functools.lru_cache(maxsize=1)
def build_segment() -> str:
    """A hash of the module files this process serves: their names and bytes.

    Computed once per process, so it names exactly the code this image runs.
    """
    digest = hashlib.sha256()
    for name in sorted(module_names()):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update((MODULE_DIR / name).read_bytes() + b"\0")
    return digest.hexdigest()[:16]


def module_url(name: str) -> str:
    return f"/app/m/{build_segment()}/{name}"


def chat_renderer_source() -> str:
    """Trusted packaged script, also loadable by a frontend without owner storage."""
    return (MODULE_DIR.parent / "chat_render.js").read_text(encoding="utf-8")


def is_module_path(path: str) -> bool:
    """The exact public path shape, for the auth middleware's carve-out."""
    return _PATH_RE.fullmatch(path) is not None


def script_source(resource: str) -> str:
    """The CSP ``script-src`` entry that admits the modules and nothing else.

    A path-restricted host source (``https://tinyassets.io/app/m/``), derived
    from the connector's public resource URL so it names the public origin even
    behind the tunnel. Deliberately NOT ``'strict-dynamic'``: that would let any
    script the page's trusted code inserted execute, and this page's CSP is
    nonce-only so that a bug inserting bundle script still runs nothing. An
    injected ``<script src>`` under this path can only load one of our own
    allowlisted module files. Empty when the origin is unknown (no modules load).
    """
    from urllib.parse import urlsplit

    parts = urlsplit(resource or "")
    if parts.scheme not in ("https", "http") or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}/app/m/"


async def handle_app_module(request: Any) -> Any:
    """``GET``/``HEAD /app/m/{build}/{name}``: one allowlisted module, or 404."""
    from starlette.responses import PlainTextResponse, RedirectResponse, Response

    from tinyassets.onboarding import onboarding_enabled

    build = request.path_params.get("build", "")
    name = request.path_params.get("name", "")
    current = build_segment()
    if not onboarding_enabled() or name not in module_names():
        return PlainTextResponse("Not Found", status_code=404)
    if build != current:
        if not re.fullmatch(r"[0-9a-f]{16}", build):
            return PlainTextResponse("Not Found", status_code=404)
        return RedirectResponse(module_url(name), status_code=307,
                                headers={"Cache-Control": "no-store"})
    body = (MODULE_DIR / name).read_bytes()
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    media = "text/javascript; charset=utf-8"
    if request.method == "HEAD":
        # The GET representation's length, not 0 (RFC 9110 section 8.6).
        headers["Content-Length"] = str(len(body))
        return Response(status_code=200, media_type=media, headers=headers)
    return Response(body, media_type=media, headers=headers)
