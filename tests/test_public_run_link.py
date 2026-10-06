"""Anonymous listing projection and normal install handoff, without private reads."""
import asyncio
from pathlib import Path

import pytest
from starlette.requests import Request

from tests.test_custom_agents import _definition
from tinyassets.custom_agents import publish_definition
from tinyassets.onboarding.public_run import handle_public_run, save_preview, share_url


def request(path, ident):
    return Request({"type": "http", "method": "GET", "path": path,
                    "query_string": b"", "headers": [(b"host", b"tinyassets.io")],
                    "path_params": {"listing": ident}})


def test_public_listing_is_escaped_and_picture_is_only_public_output(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    listing = publish_definition(tmp_path, author_id="alice",
                                 payload=_definition("<script>bad</script>"))
    ident = listing["agent_definition_id"]
    save_preview(tmp_path, ident, b"public-picture")
    page = asyncio.run(handle_public_run(request("/app/run/" + ident, ident)))
    assert page.status_code == 200
    text = page.body.decode()
    assert "<script>bad" not in text and "&lt;script&gt;bad" in text
    assert f'/app?run={ident}' in text and "Run in your universe" in text
    assert f'src="/app/run/{ident}/preview.png"' in text
    assert share_url(ident) == "https://tinyassets.io/app/run/" + ident
    picture = asyncio.run(handle_public_run(request("/app/run/" + ident + "/preview.png", ident)))
    assert Path(picture.path).read_bytes() == b"public-picture"
    assert page.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("ident", ["private-ui", "missing", "..", "private/secret"])
def test_missing_and_private_resources_have_no_public_projection(tmp_path, monkeypatch, ident):
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    (tmp_path / "private-ui").mkdir()
    (tmp_path / "private-ui" / "secret.txt").write_text("private")
    response = asyncio.run(handle_public_run(request("/app/run/" + ident, ident)))
    assert response.status_code == 404 and b"private" not in response.body


def test_only_exact_public_listing_paths_bypass_challenge():
    from tinyassets.auth.middleware import _auth_challenge_path

    assert not _auth_challenge_path("/app/run/agent_123")
    assert not _auth_challenge_path("/app/run/agent_123/preview.png")
    for path in ("/app/run/../me", "/app/run/agent_123/install", "/app/unread", "/app/me"):
        assert _auth_challenge_path(path), path


def test_legacy_preview_renders_only_published_component_and_caches(tmp_path, monkeypatch):
    from tests.test_app_ui_by_talking import _ui
    from tinyassets import ui_preview

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    screen = _ui("public-screen")
    listing = publish_definition(tmp_path, author_id="alice", payload=_definition(
        "Public screen", components={"ui": screen}))
    ident = listing["agent_definition_id"]
    rendered = []

    def render(component):
        rendered.append(component)
        return {"png": b"public-png"}

    monkeypatch.setattr(ui_preview, "preview_public_component", render)
    path = "/app/run/" + ident + "/preview.png"
    for _ in range(2):
        response = asyncio.run(handle_public_run(request(path, ident)))
        assert response.status_code == 200
        assert Path(response.path).read_bytes() == b"public-png"
    assert rendered == [screen]
    # A cached picture is not an authority to expose an unpublished ID.
    save_preview(tmp_path, "private-ui", b"private")
    response = asyncio.run(handle_public_run(
        request("/app/run/private-ui/preview.png", "private-ui")))
    assert response.status_code == 404
