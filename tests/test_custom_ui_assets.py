"""Custom UIs carry assets and name shared libraries (change custom-ui-assets).

The founder asked his agent for a village with real textures and lighting; the
agent reported a 49 KB screen, no assets and no libraries. These pin the
replacement: a UI's bytes are its owner's storage, written one path at a time,
held only by their owner, checked whenever a row names them, and delivered to
the sealed frame by the authenticated app -- the frame still fetches nothing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from pathlib import Path

import pytest

from tests import test_owner_door as _owner_door
from tests.test_owner_door import HOME_A, HOME_B, A, B, _as, _headers
from tinyassets import custom_agents as ca
from tinyassets.onboarding import ui_library_set

door = _owner_door.door  # the owner door's real routes and middleware

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ASSET = "/app/api/ui-asset"


def _bundle(ui_id: str = "village", **extra) -> dict:
    return {"kind": "tinyassets.app-ui.v1", "version": 1, "ui_id": ui_id,
            "name": "Village", "markup": "<canvas id=c></canvas>", "style": "",
            "script": "tinyassets.whoami()", **extra}


def _write(op: str, payload: dict, owner: str = A, home: str = HOME_A) -> dict:
    from tinyassets.universe_server import write_graph

    with _as(owner):
        return json.loads(write_graph(target="app_ui", operation=op, graph_id=home,
                                      payload_json=json.dumps(payload)))


def _row(base: Path, owner: str = A, home: str = HOME_A) -> dict:
    return ca.get_app_ui(base, owner_user_id=owner, universe_id=home)


def _blobs(base: Path) -> list[tuple[str, str]]:
    with ca._agent_connect(base) as conn:
        return [(r["owner_user_id"], r["sha256"]) for r in conn.execute(
            "SELECT owner_user_id, sha256 FROM universe_app_ui_asset ORDER BY 1, 2")]


# --------------------------------------------------------------------------- #
# writing assets by talking
# --------------------------------------------------------------------------- #


def test_put_asset_from_a_file_the_agents_wrote_under_u(door):
    client, base = door
    (base / HOME_A / "art").mkdir()
    (base / HOME_A / "art" / "grass.png").write_bytes(PNG)
    assert _write("add_ui", {"component": _bundle()})["status"] == "saved"

    out = _write("put_asset", {"ui_id": "village", "path": "img/grass.png",
                               "from_file": "/u/art/grass.png"})

    assert out["status"] == "saved", out
    sha = hashlib.sha256(PNG).hexdigest()
    entry = _row(base)["ui_library"][0]
    assert entry["assets"] == {"img/grass.png": {"sha256": sha, "size": len(PNG),
                                                 "media_type": "image/png"}}
    assert ca.read_app_ui_asset(base, owner_user_id=A, sha256=sha) == PNG
    # The model reads paths and sizes, never bodies.
    from tinyassets.universe_server import read_graph

    with _as(A):
        index = json.loads(read_graph(target="app_ui", graph_id=HOME_A, query="index"))
    assert index["app_ui"]["uis"][0]["assets"] == {"img/grass.png": len(PNG)}


def test_put_asset_text_and_base64_and_exactly_one_source(door):
    _, base = door
    _write("add_ui", {"component": _bundle()})
    assert _write("put_asset", {"ui_id": "village", "path": "game/world.js",
                                "text": "export const w = 1;"})["status"] == "saved"
    ogg = base64.b64encode(b"OggS....").decode()
    assert _write("put_asset", {"ui_id": "village", "path": "snd/hit.ogg",
                                "base64": ogg})["status"] == "saved"
    assets = _row(base)["ui_library"][0]["assets"]
    assert assets["game/world.js"]["media_type"] == "text/javascript"
    assert assets["snd/hit.ogg"]["media_type"] == "audio/ogg"

    for bad in ({"ui_id": "village", "path": "a.png"},
                {"ui_id": "village", "path": "a.png", "text": "x", "base64": "eA=="}):
        refused = _write("put_asset", bad)
        assert refused["error"] == "app_ui_validation_error", refused
        assert "exactly one of" in refused["detail"]


@pytest.mark.parametrize("path, needle", [
    ("../escape.png", "must be up to"),
    ("img/../x.png", "must be up to"),
    ("/abs.png", "must be up to"),
    ("tool.exe", "is not one a UI can load"),
    ("noext", "is not one a UI can load"),
])
def test_put_asset_refuses_a_path_that_is_not_a_bundle_path(door, path, needle):
    _, base = door
    _write("add_ui", {"component": _bundle()})
    refused = _write("put_asset", {"ui_id": "village", "path": path, "text": "x"})
    assert refused["error"] == "app_ui_validation_error" and needle in refused["detail"]
    assert "assets" not in _row(base)["ui_library"][0]
    assert _blobs(base) == [], "a refused path stores no bytes"


def test_from_file_reaches_only_the_callers_own_folder(door):
    _, base = door
    (base / HOME_B / "secret.png").write_bytes(PNG)
    _write("add_ui", {"component": _bundle()})
    for source in ("../" + HOME_B + "/secret.png", "/" + HOME_B + "/secret.png", "secret.png"):
        refused = _write("put_asset", {"ui_id": "village", "path": "a.png", "from_file": source})
        assert refused["error"] == "app_ui_not_found", (source, refused)
    assert _blobs(base) == []


def test_remove_asset_and_a_stale_etag(door):
    _, base = door
    _write("add_ui", {"component": _bundle()})
    put = _write("put_asset", {"ui_id": "village", "path": "a.css", "text": "body{}"})
    stale = put["etag"]
    _write("put_asset", {"ui_id": "village", "path": "b.css", "text": "p{}"})

    refused = _write("remove_asset", {"ui_id": "village", "path": "a.css", "expected_etag": stale})
    assert refused["error"] == "app_ui_conflict"
    missing = _write("remove_asset", {"ui_id": "village", "path": "zzz.css"})
    assert missing["error"] == "app_ui_not_found" and "b.css" in missing["detail"]

    assert _write("remove_asset", {"ui_id": "village", "path": "a.css"})["removed"] is True
    assert set(_row(base)["ui_library"][0]["assets"]) == {"b.css"}
    _write("remove_asset", {"ui_id": "village", "path": "b.css"})
    assert "assets" not in _row(base)["ui_library"][0], "an empty map is dropped, not kept"


# --------------------------------------------------------------------------- #
# a row can never name bytes its owner does not hold
# --------------------------------------------------------------------------- #


def test_a_row_cannot_name_another_persons_blob(door):
    _, base = door
    _write("add_ui", {"component": _bundle()}, owner=B, home=HOME_B)
    _write("put_asset", {"ui_id": "village", "path": "a.png", "base64":
                         base64.b64encode(PNG).decode()}, owner=B, home=HOME_B)
    bobs = _row(base, B, HOME_B)["ui_library"][0]["assets"]

    # Alice copies Bob's manifest verbatim, by every write path.
    stolen = _bundle(assets=bobs)
    for op, payload in (("add_ui", {"component": stolen}),):
        refused = _write(op, payload)
        assert refused["error"] == "app_ui_validation_error", refused
        assert "not in your UI storage" in refused["detail"]
    with pytest.raises(ca.AgentValidationError, match="not in your UI storage"):
        ca.save_app_ui(base, owner_user_id=A, universe_id=HOME_A, expected_revision=0,
                       changes={"ui_library": [stolen]})
    _write("add_ui", {"component": _bundle()})
    refused = _write("replace_ui", {"component": stolen})
    assert refused["error"] == "app_ui_validation_error"
    # And the byte route keyed by Alice does not hand her Bob's bytes either.
    assert ca.read_app_ui_asset(base, owner_user_id=A, sha256=bobs["a.png"]["sha256"]) is None


def test_a_manifest_must_match_the_stored_size_and_type(door):
    _, base = door
    _write("add_ui", {"component": _bundle()})
    _write("put_asset", {"ui_id": "village", "path": "a.png",
                         "base64": base64.b64encode(PNG).decode()})
    ref = dict(_row(base)["ui_library"][0]["assets"]["a.png"])
    lie = dict(ref, size=ref["size"] + 1)
    refused = _write("replace_ui", {"component": _bundle(assets={"a.png": lie})})
    assert refused["error"] == "app_ui_validation_error"
    renamed = _write("replace_ui", {"component": _bundle(assets={"a.jpg": ref})})
    assert renamed["error"] == "app_ui_validation_error", "png bytes are not a jpeg by renaming"


@pytest.mark.parametrize("writer", ["save", "add_ui"])
def test_a_sweep_cannot_delete_a_blob_between_the_check_and_the_write(tmp_path, monkeypatch,
                                                                      writer):
    """Codex 2026-10-02 (P1): the held-check read without a lock, so a sweep
    could commit between it and the row write, and the save returned success
    naming a deleted blob. The writer now holds the write lock from the check to
    the commit; a sweep racing it waits, then sees the reference."""
    import threading

    old = ca.store_app_ui_asset(tmp_path, owner_user_id=A, data=PNG, media_type="image/png")
    with ca._agent_connect(tmp_path) as conn:  # unreferenced and past its grace
        conn.execute("UPDATE universe_app_ui_asset SET created_at = ?",
                     (time.time() - 2 * ca._ASSET_GC_GRACE_SECONDS,))
    real_check = ca._check_assets_held
    sweeper: list[threading.Thread] = []

    def check_then_race(conn, owner, library):
        real_check(conn, owner, library)
        # Another asset write (which sweeps) starts right after the check.
        thread = threading.Thread(target=ca.store_app_ui_asset, kwargs=dict(
            base_path=tmp_path, owner_user_id=A, data=b"other", media_type="text/plain"))
        thread.start()
        sweeper.append(thread)
        time.sleep(0.5)  # ample time for an unserialized sweep to commit

    monkeypatch.setattr(ca, "_check_assets_held", check_then_race)
    component = _bundle(assets={"a.png": old})
    if writer == "save":
        saved = ca.save_app_ui(tmp_path, owner_user_id=A, universe_id=HOME_A,
                               expected_revision=0, changes={"ui_library": [component]})
    else:
        saved = ca.change_app_ui_entry(tmp_path, owner_user_id=A, universe_id=HOME_A,
                                       operation="add_ui", payload={"component": component})
    sweeper[0].join(timeout=30)
    assert not sweeper[0].is_alive()
    assert saved["revision"] == 1
    assert ca.read_app_ui_asset(tmp_path, owner_user_id=A, sha256=old["sha256"]) == PNG, (
        "the saved row names bytes that still exist")


def test_the_same_bytes_under_two_types_change_nothing_shared(door):
    """Codex 2026-10-02 (P2): a refused upload rewrote the shared blob's media
    type under an existing reference. The type now lives only in each
    reference, so both paths save and both stay valid."""
    _, base = door
    _write("add_ui", {"component": _bundle()})
    for path in ("a.txt", "a.json"):
        out = _write("put_asset", {"ui_id": "village", "path": path, "text": "{}"})
        assert out["status"] == "saved", out
    assets = _row(base)["ui_library"][0]["assets"]
    assert assets["a.txt"]["media_type"] == "text/plain"
    assert assets["a.json"]["media_type"] == "application/json"
    assert assets["a.txt"]["sha256"] == assets["a.json"]["sha256"]
    assert len(_blobs(base)) == 1
    # Later edits keeping both references still validate.
    assert _write("edit_ui", {"ui_id": "village", "set": {"name": "V2"}})["status"] == "saved"


# --------------------------------------------------------------------------- #
# bounds: one UI, not the account
# --------------------------------------------------------------------------- #


def test_a_game_past_the_old_49_kb_bound_is_saved(door):
    _, base = door
    big = _bundle(script="/*" + "x" * 200_000 + "*/", libraries=["three"], script_type="module")
    assert _write("add_ui", {"component": big})["status"] == "saved"
    assert len(_row(base)["ui_library"][0]["script"]) > 200_000


def test_component_text_libraries_and_script_type_are_bounded(door):
    _write("add_ui", {"component": _bundle()})
    over = _bundle(script="x" * (ca.APP_UI_MAX_COMPONENT_TEXT_BYTES + 1))
    refused = _write("replace_ui", {"component": over})
    assert "Move code into a JS asset" in refused["detail"]
    def replaced(**fields):
        return _write("replace_ui", {"component": _bundle(**fields)})

    assert "jquery" in replaced(libraries=["jquery"])["detail"]
    assert "script_type" in replaced(script_type="wasm")["detail"]
    # edit_ui can set the library list and is validated after the edit.
    edited = _write("edit_ui", {"ui_id": "village", "set": {"libraries": ["pixi.js"]}})
    assert edited["status"] == "saved"
    assert "jquery" in _write("edit_ui", {"ui_id": "village",
                                          "set": {"libraries": ["jquery"]}})["detail"]


def test_asset_count_and_per_file_bounds(door, monkeypatch):
    _, base = door
    _write("add_ui", {"component": _bundle()})
    monkeypatch.setattr(ca, "APP_UI_MAX_ASSET_BYTES", 8)
    refused = _write("put_asset", {"ui_id": "village", "path": "a.txt", "text": "123456789"})
    assert "the bound per file" in refused["detail"]
    monkeypatch.setattr(ca, "APP_UI_MAX_ASSET_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(ca, "APP_UI_MAX_ASSET_FILES", 2)
    for name in ("a", "b"):
        _write("put_asset", {"ui_id": "village", "path": f"{name}.txt", "text": name})
    third = _write("put_asset", {"ui_id": "village", "path": "c.txt", "text": "c"})
    assert "the bound is 2" in third["detail"]
    assert set(_row(base)["ui_library"][0]["assets"]) == {"a.txt", "b.txt"}


# --------------------------------------------------------------------------- #
# storage: the owner's quota, measured and swept
# --------------------------------------------------------------------------- #


def test_asset_bytes_count_as_the_owners_ui_storage_once_per_hash(door):
    from tinyassets import storage_accounting as sa

    _, base = door
    _write("add_ui", {"component": _bundle()})
    _write("add_ui", {"component": _bundle("other")})
    before = sa._ui_library(base, A)
    image = PNG + b"\x01" * 65536
    payload = base64.b64encode(image).decode()
    _write("put_asset", {"ui_id": "village", "path": "a.png", "base64": payload})
    _write("put_asset", {"ui_id": "other", "path": "same.png", "base64": payload})
    growth = sa._ui_library(base, A) - before
    assert growth >= len(image)
    assert growth < 2 * len(image), "one blob for one hash, however many UIs use it"
    assert len(_blobs(base)) == 1


def test_a_put_past_the_quota_is_refused_before_anything_lands(tmp_path, monkeypatch):
    from tinyassets import storage_accounting as sa
    from tinyassets.daemon_server import grant_universe_ownership, initialize_author_server

    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", str(100 * 1024 / 1024**3))
    initialize_author_server(root)
    owner = "workos|alice"
    grant_universe_ownership(root, universe_id="u-a", owner_id=owner)
    (root / "u-a").mkdir()
    ca.change_app_ui_entry(root, owner_user_id=owner, universe_id="u-a",
                           operation="add_ui", payload={"component": _bundle()})
    with pytest.raises(sa.StorageRefused):
        ca.put_app_ui_asset(root, owner_user_id=owner, universe_id="u-a", ui_id="village",
                            path="big.bin", data=b"x" * (200 * 1024))
    assert _blobs(root) == []
    assert "assets" not in ca.get_app_ui(root, owner_user_id=owner,
                                         universe_id="u-a")["ui_library"][0]


def test_unreferenced_blobs_are_swept_after_their_grace(door):
    _, base = door
    _write("add_ui", {"component": _bundle()})
    _write("put_asset", {"ui_id": "village", "path": "old.css", "text": "a{}"})
    _write("put_asset", {"ui_id": "village", "path": "kept.css", "text": "b{}"})
    _write("remove_asset", {"ui_id": "village", "path": "old.css"})
    old_sha = hashlib.sha256(b"a{}").hexdigest()
    kept_sha = hashlib.sha256(b"b{}").hexdigest()
    # Age everything past the grace period.
    with ca._agent_connect(base) as conn:
        conn.execute("UPDATE universe_app_ui_asset SET created_at = ?",
                     (time.time() - 2 * ca._ASSET_GC_GRACE_SECONDS,))
    # A young unreferenced blob survives: it may be about to be referenced.
    ca.store_app_ui_asset(base, owner_user_id=A, data=b"young", media_type="text/plain")
    held = {sha for _, sha in _blobs(base)}
    assert old_sha not in held, "unreferenced and old: swept"
    assert kept_sha in held, "referenced: kept however old"
    assert hashlib.sha256(b"young").hexdigest() in held


def test_account_deletion_takes_the_owners_blobs(tmp_path):
    from tinyassets.account_deletion import deletion_plan

    ca.store_app_ui_asset(tmp_path, owner_user_id=A, data=PNG, media_type="image/png")
    with ca._agent_connect(tmp_path) as conn:
        plan = deletion_plan(conn, principal=A, home=HOME_A)
    assert plan["universe_app_ui_asset"] == [("owner_user_id", "principal")]


# --------------------------------------------------------------------------- #
# the byte route: the app fetches, the frame never does
# --------------------------------------------------------------------------- #


def test_the_owner_fetches_their_blob_as_an_opaque_download(door):
    client, base = door
    _write("add_ui", {"component": _bundle()})
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    _write("put_asset", {"ui_id": "village", "path": "x.svg", "text": svg.decode()})
    sha = hashlib.sha256(svg).hexdigest()

    reply = client.post(ASSET, headers=_headers(A), json={"graph_id": HOME_A, "sha256": sha})
    assert reply.status_code == 200 and reply.content == svg
    # Never the asset's own type: stored SVG cannot render or run on the app origin.
    assert reply.headers["content-type"] == "application/octet-stream"
    assert reply.headers["x-content-type-options"] == "nosniff"
    assert reply.headers["content-disposition"] == "attachment"
    assert reply.headers["content-security-policy"].startswith("sandbox")
    assert reply.headers["cache-control"] == "no-store"

    # Bob knows the hash and still reaches nothing: not via his home, not via hers.
    assert client.post(ASSET, headers=_headers(B),
                       json={"graph_id": HOME_B, "sha256": sha}).status_code == 404
    assert client.post(ASSET, headers=_headers(B),
                       json={"graph_id": HOME_A, "sha256": sha}).status_code == 404
    assert client.post(ASSET, json={"graph_id": HOME_A, "sha256": sha},
                       headers={"Content-Type": "application/json"}).status_code == 401


def test_the_route_serves_a_pinned_library_and_refuses_unknown_shapes(door):
    client, _ = door
    reply = client.post(ASSET, headers=_headers(A), json={"library": "howler"})
    assert reply.status_code == 200
    pin = ui_library_set.manifest()["howler"]["sha384"]
    assert "sha384-" + base64.b64encode(hashlib.sha384(reply.content).digest()).decode() == pin
    assert client.post(ASSET, headers=_headers(A), json={"library": "jquery"}).status_code == 404
    for body in ({}, {"library": "howler", "graph_id": HOME_A}, {"sha256": "ab"},
                 {"graph_id": HOME_A, "sha256": 7}):
        assert client.post(ASSET, headers=_headers(A), json=body).status_code == 400, body


def test_a_host_without_the_library_files_says_so(door, monkeypatch, tmp_path):
    client, _ = door
    monkeypatch.setattr(ui_library_set, "LIBRARY_DIR", tmp_path / "absent")
    ui_library_set.library_bytes.cache_clear()
    try:
        reply = client.post(ASSET, headers=_headers(A), json={"library": "phaser"})
    finally:
        ui_library_set.library_bytes.cache_clear()
    assert reply.status_code == 404 and reply.json()["error"] == "library_unavailable"


# --------------------------------------------------------------------------- #
# the library set: pinned, mirrored as a manifest only, one table in two places
# --------------------------------------------------------------------------- #


def test_every_vendored_library_matches_its_pin():
    for name, entry in ui_library_set.manifest().items():
        data = (ui_library_set.LIBRARY_DIR / entry["file"]).read_bytes()
        assert len(data) == entry["size"], name
        assert ui_library_set.library_bytes(name) == data
        assert (ui_library_set.LIBRARY_DIR / entry["license_file"]).is_file(), name
        for dependency in entry["requires"]:
            assert dependency in ui_library_set.manifest(), (name, dependency)


def test_the_apps_pin_table_is_the_manifest():
    controller = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    block = re.search(r"LIBRARIES:Object\.freeze\((\{.*?\})\),\n", controller, re.S)
    assert block, "the controller must declare its pin table"
    # The JS object literal uses bare keys for format/sha384/requires.
    as_json = re.sub(r"([{,])(format|sha384|requires):", r'\1"\2":', block.group(1))
    table = json.loads(as_json)
    assert table == {name: {"format": e["format"], "sha384": e["sha384"], "requires": e["requires"]}
                     for name, e in ui_library_set.manifest().items()}


def test_the_plugin_mirror_ships_the_manifest_but_not_the_files():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_plugin", "packaging/claude-plugin/build_plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert "ui_libraries" in module._TREE_EXCLUDES
    runtime = Path("packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/"
                   "tinyassets/onboarding")
    assert (runtime / "ui_libraries.json").is_file()
    assert not (runtime / "ui_libraries").exists()


def test_library_load_order_puts_requirements_first():
    order = ui_library_set.load_order(["three/addons/loaders/GLTFLoader.js"])
    assert order == ["three", "three/addons/utils/BufferGeometryUtils.js",
                     "three/addons/loaders/GLTFLoader.js"]
    with pytest.raises(ValueError, match="not one this app provides"):
        ui_library_set.load_order(["jquery"])


# --------------------------------------------------------------------------- #
# publish: libraries travel, private files do not leave silently
# --------------------------------------------------------------------------- #


def test_publish_carries_libraries_and_refuses_assets_by_name():
    from tinyassets.api.publish_requests import export_ui_component

    exported = export_ui_component(_bundle(libraries=["phaser"], script_type="classic"))
    assert exported["libraries"] == ["phaser"] and exported["script_type"] == "classic"
    with pytest.raises(ValueError, match="loads its own files"):
        export_ui_component(_bundle(assets={"a.png": {"sha256": "0" * 64, "size": 1,
                                                      "media_type": "image/png"}}))
