"""Anonymous entry point for immutable published listings, never private resources."""
from __future__ import annotations

import hashlib
import html
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")


def public_path(path: str) -> bool:
    parts = path.split("/")
    return (len(parts) in (4, 5) and parts[:3] == ["", "app", "run"]
            and bool(_ID.fullmatch(parts[3]))
            and (len(parts) == 4 or parts[4] == "preview.png"))


def share_url(definition_id: str) -> str:
    return "https://tinyassets.io/app/run/" + quote(definition_id, safe="")


def preview_path(base: str | Path, definition_id: str) -> Path:
    digest = hashlib.sha256(definition_id.encode()).hexdigest()
    return Path(base) / "published_previews" / (digest + ".png")


def save_preview(base: str | Path, definition_id: str, png: bytes) -> None:
    """Only called with the renderer's public-component output after publication."""
    path = preview_path(base, definition_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(png)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


async def handle_public_run(request):
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import FileResponse, HTMLResponse, Response

    from tinyassets.api.helpers import _base_path
    from tinyassets.custom_agents import get_definition
    from tinyassets.onboarding import onboarding_enabled

    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    ident = request.path_params["listing"]
    if not onboarding_enabled() or not public_path(request.url.path):
        return Response(status_code=404, headers=headers)
    base = _base_path()
    listing = await run_in_threadpool(get_definition, base, ident)
    if listing is None:
        return Response(status_code=404, headers=headers)
    picture = preview_path(base, ident)
    screen = next((c for c in listing["components"].values()
                   if c.get("kind") == "tinyassets.app-ui.v1"), None)
    if request.url.path.endswith("/preview.png"):
        if not picture.is_file():
            if screen is None:
                return Response(status_code=404, headers=headers)
            from tinyassets.ui_preview import PreviewUnavailable, preview_public_component

            # Legacy published listings have no cached screenshot yet. The
            # existing renderer owns concurrency and sandbox/process bounds;
            # this path passes no publisher home, identity or private asset.
            try:
                report = await run_in_threadpool(preview_public_component, screen)
                await run_in_threadpool(save_preview, base, ident, report["png"])
            except PreviewUnavailable:
                return Response(status_code=503, headers=headers)
        return FileResponse(picture, media_type="image/png", headers=headers)
    esc = html.escape
    image = (f'<img src="/app/run/{quote(ident, safe="")}/preview.png" alt="Preview of '
             f'{esc(listing["name"])}">' if screen is not None or picture.is_file()
             else '<svg viewBox="0 0 800 280" role="img" aria-label="Workflow listing preview">'
             '<rect width="800" height="280" rx="16" fill="#202d44"/>'
             '<path d="M210 140H590" stroke="#c3d5ff" stroke-width="6"/>'
             '<g fill="#c3d5ff"><circle cx="210" cy="140" r="36"/>'
             '<circle cx="400" cy="140" r="36"/><circle cx="590" cy="140" r="36"/></g>'
             '<text x="400" y="240" text-anchor="middle" fill="white" '
             'font-size="22">Shared workflow</text></svg>')
    body = f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(listing["name"])} — TinyAssets</title>
<style>body{{font:18px system-ui;background:#10131a;color:#f3f4f7;
max-width:800px;margin:8vh auto;padding:24px}}
img{{width:100%;border-radius:16px}}p{{white-space:pre-wrap;line-height:1.6}}
a{{display:inline-block;background:#c3d5ff;color:#10131a;padding:16px;border-radius:12px}}</style>
<main><h1>{esc(listing["name"])}</h1>{image}<p>{esc(listing["description"])}</p>
<a href="/app?run={quote(ident, safe='')}">Run in your universe</a>
<p>Sign in or create an account, then review what will be installed in your own universe.</p>
</main></html>'''
    headers["Content-Security-Policy"] = (
        "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'none'")
    return HTMLResponse(body, headers=headers)
