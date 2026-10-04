"""S0 of the app.html split: the ES-module route, its allowlist and CSP grant.

docs/design-notes/2026-10-01-split-app-html-into-es-modules.md. Nothing loads a
module yet; these pin the serving contract S1 relies on.
"""

from __future__ import annotations

import os

import anyio
import pytest

from tinyassets.auth.middleware import _auth_challenge_path
from tinyassets.onboarding import _csp, app_modules, onboarding_routes


class _Request:
    def __init__(self, build: str, name: str, method: str = "GET") -> None:
        self.path_params = {"build": build, "name": name}
        self.method = method


def _get(build: str, name: str, method: str = "GET"):
    return anyio.run(app_modules.handle_app_module, _Request(build, name, method))


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    return app_modules.build_segment()


def test_the_entry_module_is_served_as_cacheable_javascript(live):
    response = _get(live, "main.js")
    body = (app_modules.MODULE_DIR / "main.js").read_bytes()
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "immutable" in response.headers["cache-control"]
    assert bytes(response.body) == body


def test_head_advertises_the_get_length(live):
    """Codex on #4281: an empty HEAD response said Content-Length: 0."""
    head = _get(live, "main.js", "HEAD")
    assert head.status_code == 200
    assert head.headers["content-length"] == str(
        len((app_modules.MODULE_DIR / "main.js").read_bytes())
    )


def test_the_key_is_a_hash_of_the_served_files_not_the_deploy_receipt(live, monkeypatch):
    """The new image runs before the receipt moves; a receipt key would have
    cached the new bytes forever under the old URL (Codex on #4281)."""
    monkeypatch.setattr("tinyassets.onboarding.build_sha", lambda: "a-different-receipt")
    assert app_modules.build_segment() == live
    assert len(live) == 16 and int(live, 16) >= 0
    assert app_modules.module_url("main.js") == f"/app/m/{live}/main.js"


@pytest.mark.parametrize(
    "name",
    ["nope.js", "Main.js", "main.mjs", "main.js.map", "../app.html", "..%2Fapp.html",
     "app.html", "", "main"],
)
def test_only_allowlisted_basenames_are_served(live, name):
    assert _get(live, name).status_code == 404


def test_another_hash_is_refused(live):
    """A stale page never imports newer code under its old URL."""
    assert _get("f" * 16, "main.js").status_code == 404


def test_a_symlink_in_the_module_dir_is_never_served(tmp_path, monkeypatch):
    target = tmp_path / "outside.txt"
    target.write_text("not a module", encoding="utf-8")
    modules = tmp_path / "app"
    modules.mkdir()
    (modules / "real.js").write_text("export {};\n", encoding="utf-8")
    try:
        os.symlink(target, modules / "linked.js")
    except OSError:
        pytest.skip("this host cannot create symlinks")
    monkeypatch.setattr(app_modules, "MODULE_DIR", modules)
    assert app_modules.module_names() == frozenset({"real.js"})


def test_the_dark_flag_hides_modules(monkeypatch):
    monkeypatch.delenv("TINYASSETS_ONBOARDING_APP", raising=False)
    assert _get(app_modules.build_segment(), "main.js").status_code == 404


def test_the_route_is_registered_for_reads_only():
    routes = [r for r in onboarding_routes() if getattr(r, "path", "") == "/app/m/{build}/{name}"]
    assert len(routes) == 1
    assert set(routes[0].methods) == {"GET", "HEAD"}


def test_the_auth_carve_out_is_exactly_the_module_shape():
    key = "0123456789abcdef"
    assert not _auth_challenge_path(f"/app/m/{key}/main.js")
    for near in (
        f"/app/m/{key}/main.js/x", f"/app/m/{key}/../me", f"/app/m/{key}/main.json",
        f"/app/m/{key}/sub/main.js", "/app/m/main.js", f"/app/m/{key}/", "/app/m",
        f"/app/m/{key}/Main.js", "/app/me",
    ):
        assert _auth_challenge_path(near), near


def test_the_csp_grants_the_module_path_and_never_strict_dynamic():
    policy = _csp("n0nce", "https://auth.example", "https://tinyassets.io/mcp")
    script = next(d for d in policy.split(";") if d.strip().startswith("script-src"))
    assert script.split() == ["script-src", "'nonce-n0nce'", "https://tinyassets.io/app/m/"]
    assert "strict-dynamic" not in policy and "'self'" not in script
    # No resource origin: no module grant at all, still nonce-only.
    bare = _csp("n0nce", "", "")
    assert "script-src 'nonce-n0nce';" in bare


def test_module_files_ship_with_the_package():
    """Data a feature reads must be packaged: the Docker image copies tinyassets/."""
    assert "main.js" in app_modules.module_names()
