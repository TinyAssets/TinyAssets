"""The agent can see the UI it built: a real headless render of its own UI.

Real Chromium (``real_browser``): the shipped frame renders the stored
component, its assets and libraries; the report carries what a person would see
and what went wrong -- a PNG, frames per second, uncaught errors, refused
bridge actions -- and nothing reaches a network.
"""
# ruff: noqa: E501 -- embedded JavaScript bundles are stored verbatim
from __future__ import annotations

import io
import struct
import zlib

import pytest

from tinyassets import custom_agents as ca
from tinyassets import ui_preview

# `api.app_ui` is imported HERE, before any test can patch what it binds: it
# copies `_base_path` out of `api.helpers` at import time, so a first import
# under such a patch would freeze the stand-in for every later test in the
# process.
from tinyassets.api import app_ui as _app_ui  # noqa: F401

OWNER, HOME = "alice", "u-alice"


def _png(rgb=(32, 160, 64), size=8) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _add(base, ui_id, **fields):
    component = {"kind": "tinyassets.app-ui.v1", "version": 1, "ui_id": ui_id,
                 "name": ui_id, "markup": "", "style": "", "script": "", **fields}
    ca.change_app_ui_entry(base, owner_user_id=OWNER, universe_id=HOME,
                           operation="add_ui", payload={"component": component})


def _pixel(png: bytes, x: int, y: int) -> tuple[int, int, int]:
    image = pytest.importorskip("PIL.Image")
    return image.open(io.BytesIO(png)).convert("RGB").getpixel((x, y))


def _need_browser():
    if not ui_preview.available():
        pytest.skip("Playwright is not installed on this host")


@pytest.mark.real_browser
def test_the_agent_sees_its_ui_as_rendered_with_its_assets(tmp_path):
    _need_browser()
    _add(tmp_path, "village",
         markup='<div id=sky></div><img id=grass src="ta-asset:img/grass.png">',
         style="html,body{margin:0}#sky{position:fixed;inset:0;background:#2050c0}"
               "#grass{position:fixed;left:0;bottom:0;width:100%;height:50%}",
         script="(function spin(){requestAnimationFrame(spin);})();"
                "tinyassets.whoami().then(w=>document.title=w.command_center_id);"
                "tinyassets.sendMessage('hello').catch(()=>{});")
    ca.put_app_ui_asset(tmp_path, owner_user_id=OWNER, universe_id=HOME, ui_id="village",
                        path="img/grass.png", data=_png())

    try:
        report = ui_preview.preview_app_ui(tmp_path, owner_user_id=OWNER, universe_id=HOME,
                                           ui_id="village", width=400, height=300)
    except ui_preview.PreviewUnavailable as exc:
        pytest.skip(str(exc))

    assert report["png"].startswith(b"\x89PNG"), "a real screenshot, not a placeholder"
    assert _pixel(report["png"], 200, 40) == (32, 80, 192), "the sky is the UI's own style"
    assert _pixel(report["png"], 200, 260) == (32, 160, 64), "the grass is the stored asset"
    assert report["fps"] and report["fps"] > 5, report["fps"]
    # The reads answer; the action is refused as a preview, and both are reported.
    assert report["bridge_calls"] == {"whoami": 1, "send_message": 1}
    assert report["uncaught_errors"] == [] and report["missing_assets"] == []
    assert report["blocked_requests"] == []


@pytest.mark.real_browser
def test_a_broken_ui_reports_its_error_and_its_blocked_egress(tmp_path):
    _need_browser()
    _add(tmp_path, "broken", libraries=["three"], script_type="module",
         script='import * as THREE from "three";\n'
                'fetch("https://elsewhere.example/x").catch(()=>{});\n'
                'document.body.style.background="#fff";\n'
                'throw new Error("village exploded at r" + THREE.REVISION);')
    try:
        report = ui_preview.preview_app_ui(tmp_path, owner_user_id=OWNER, universe_id=HOME,
                                           ui_id="broken", width=320, height=240)
    except ui_preview.PreviewUnavailable as exc:
        pytest.skip(str(exc))

    assert any("village exploded at r170" in line
               for line in report["uncaught_errors"] + report["console"]), report
    assert any("Refused to connect" in line or "elsewhere.example" in line
               for line in report["console"] + report["blocked_requests"]), report


def test_an_unknown_ui_is_named_not_rendered(tmp_path):
    _add(tmp_path, "village")
    with pytest.raises(ca.AgentNotFoundError, match="installed: \\['village'\\]"):
        ui_preview.preview_app_ui(tmp_path, owner_user_id=OWNER, universe_id=HOME,
                                  ui_id="nope")


def test_another_persons_ui_is_not_reachable(tmp_path):
    _add(tmp_path, "village")
    with pytest.raises(ca.AgentNotFoundError):
        ui_preview.preview_app_ui(tmp_path, owner_user_id="bob", universe_id=HOME,
                                  ui_id="village")


def test_no_browser_is_a_loud_refusal_not_a_blank_image(tmp_path, monkeypatch):
    _add(tmp_path, "village")
    monkeypatch.setattr(ui_preview, "available", lambda: False)
    with pytest.raises(ui_preview.PreviewUnavailable, match="ui_preview_unavailable"):
        ui_preview.preview_app_ui(tmp_path, owner_user_id=OWNER, universe_id=HOME,
                                  ui_id="village")


def test_one_render_at_a_time(tmp_path, monkeypatch):
    _add(tmp_path, "village")
    monkeypatch.setattr(ui_preview, "available", lambda: True)
    assert ui_preview._SLOT.acquire(blocking=False)
    try:
        with pytest.raises(ui_preview.PreviewUnavailable, match="ui_preview_busy"):
            ui_preview.preview_app_ui(tmp_path, owner_user_id=OWNER, universe_id=HOME,
                                      ui_id="village")
    finally:
        ui_preview._SLOT.release()


@pytest.mark.parametrize("width, height", [(10, 300), (300, 5000), ("400", 300)])
def test_the_viewport_is_bounded(tmp_path, width, height):
    _add(tmp_path, "village")
    with pytest.raises(ValueError, match="width and height"):
        ui_preview.preview_app_ui(tmp_path, owner_user_id=OWNER, universe_id=HOME,
                                  ui_id="village", width=width, height=height)


# --------------------------------------------------------------------------- #
# writing the screenshot into the agent's own folder
# --------------------------------------------------------------------------- #


def test_the_screenshot_lands_in_previews_and_replaces_a_planted_hard_link(tmp_path):
    import os

    universe = tmp_path / "u-alice"
    universe.mkdir()
    (universe / "soul.md").write_text("the persona", encoding="utf-8")
    (universe / "previews").mkdir()
    os.link(universe / "soul.md", universe / "previews" / "village.png")

    shown = ui_preview.write_preview(universe, "village", b"\x89PNG-bytes")

    assert shown == "/u/previews/village.png"
    assert (universe / "previews" / "village.png").read_bytes() == b"\x89PNG-bytes"
    assert (universe / "soul.md").read_text(encoding="utf-8") == "the persona", (
        "the write replaced the link instead of writing through it")
    assert sorted(p.name for p in (universe / "previews").iterdir()) == ["village.png"]


@pytest.mark.parametrize("ui_id", ["../soul", "a/b", "", "UPPER", "x" * 65, "con", "lpt1"])
def test_only_the_apps_id_shape_names_a_file(tmp_path, ui_id):
    with pytest.raises(ui_preview.PreviewUnavailable, match="ui_id"):
        ui_preview.write_preview(tmp_path, ui_id, b"png")
    assert list(tmp_path.iterdir()) == []


def test_a_previews_entry_that_is_not_a_folder_is_refused(tmp_path):
    (tmp_path / "previews").write_text("a file", encoding="utf-8")
    with pytest.raises(ui_preview.PreviewUnavailable, match="ui_preview_failed"):
        ui_preview.write_preview(tmp_path, "village", b"png")


@pytest.mark.skipif(__import__("os").name != "posix", reason="needs symlink creation")
def test_a_previews_symlink_is_refused(tmp_path):
    import os

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    universe = tmp_path / "u"
    universe.mkdir()
    os.symlink(elsewhere, universe / "previews")
    with pytest.raises(ui_preview.PreviewUnavailable, match="ui_preview_failed"):
        ui_preview.write_preview(universe, "village", b"png")
    assert list(elsewhere.iterdir()) == []


@pytest.mark.skipif(__import__("os").name != "posix", reason="needs symlink creation")
def test_a_link_at_or_above_the_universe_root_is_refused(tmp_path):
    """The one writer resolves its root, so the root is checked before it runs.

    `api/helpers._universe_dir` resolves before calling, so this cannot fire
    through the served handle; it keeps a future caller that passes an
    unresolved path from writing through a linked ancestor.
    """
    import os

    real = tmp_path / "real"
    (real / "previews").mkdir(parents=True)
    os.symlink(real, tmp_path / "via", target_is_directory=True)
    with pytest.raises(ui_preview.PreviewUnavailable, match="ui_preview_failed"):
        ui_preview.write_preview(tmp_path / "via", "village", b"png")
    assert list((real / "previews").iterdir()) == []
    # the same path, resolved, is still written: the check refuses the LINK,
    # not the directory it pointed at.
    assert ui_preview.write_preview(real, "village", b"png") == "/u/previews/village.png"
    assert (real / "previews" / "village.png").read_bytes() == b"png"


# --------------------------------------------------------------------------- #
# the served handle: read_graph target="app_ui_preview"
# --------------------------------------------------------------------------- #


def test_the_engine_handle_renders_writes_and_reports(tmp_path, monkeypatch):
    import json

    from tests.engine_authority_helpers import mock_engine_admission, seed_engine_authority
    from tinyassets import engine_mcp_server as s

    root = tmp_path / "data"
    (root / "u-a").mkdir(parents=True)
    # The data root moves by env var only. Patching `helpers._base_path` instead
    # would be both redundant and a process-wide leak: `api.app_ui` binds that
    # name at import (`from ...helpers import _base_path`), so whichever test
    # imports it first under such a patch keeps the stand-in for the whole run.
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setattr(s, "_ACTOR_ID", "actor-a")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-a")
    seed_engine_authority(root, actor="actor-a", graph="u-a")
    mock_engine_admission(monkeypatch, {"u-a"})
    seen = {}

    def fake_render(base, *, owner_user_id, universe_id, ui_id, **_):
        seen.update(owner=owner_user_id, universe=universe_id, ui=ui_id)
        return {"ui_id": ui_id, "fps": 60.0, "uncaught_errors": [], "png": b"\x89PNG-shot"}

    monkeypatch.setattr(ui_preview, "preview_app_ui", fake_render)
    envelope = json.loads(s.read_graph(target="app_ui_preview", query="village"))
    assert envelope["untrusted"] is True, "the UI's output is data, never instructions"
    report = envelope["content"]

    assert seen == {"owner": "actor-a", "universe": "u-a", "ui": "village"}, (report,
        "the owner and universe come from the binding, never the arguments")
    assert report["screenshot"] == "/u/previews/village.png"
    assert report["see_it"] == 'read path="/u/previews/village.png"'
    assert "png" not in report, "bytes go to the folder, never into the result"
    assert (root / "u-a" / "previews" / "village.png").read_bytes() == b"\x89PNG-shot"

    monkeypatch.setattr(ui_preview, "preview_app_ui", lambda *a, **k: (_ for _ in ()).throw(
        ui_preview.PreviewUnavailable("ui_preview_busy: another preview is rendering")))
    busy = json.loads(s.read_graph(target="app_ui_preview", query="village"))
    assert busy["error"] == "ui_preview_busy"
    missing = json.loads(s.read_graph(target="app_ui_preview", query=""))
    assert missing["error"] == "app_ui_validation_error"


def test_the_browser_sandbox_is_never_turned_off():
    """Playwright launches Chromium with its sandbox OFF unless asked. The UI is
    somebody's code, so the render keeps the browser's own confinement."""
    import inspect

    source = inspect.getsource(ui_preview._child)
    assert "chromium_sandbox=True" in source
    assert "--no-sandbox" not in source


def test_stored_ui_text_never_reaches_the_parent_page():
    """Codex 2026-10-02 (P1): the bundle was spliced into the parent's <script>,
    so a stored `</script>` ran code in the unrestricted parent. The parent is a
    fixed page under a nonce-only policy and fetches the UI as JSON."""
    import inspect

    assert "__SPEC__" not in ui_preview._PARENT
    assert "fetch('/__preview/spec.json')" in ui_preview._PARENT
    source = inspect.getsource(ui_preview._child)
    assert "script-src 'nonce-{nonce}'" in source
    for flag in ("--host-resolver-rules=MAP * ~NOTFOUND", "--proxy-server=http://127.0.0.1:9",
                 "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"):
        assert flag in source, flag
    assert ui_preview.FRAME_SANDBOX == "allow-scripts allow-forms"


def test_the_preview_frame_sandbox_is_the_apps():
    import re
    from pathlib import Path

    app = Path("tinyassets/onboarding/app_ui.js").read_text(encoding="utf-8")
    assert re.search(r'SANDBOX:"([^"]+)"', app).group(1) == ui_preview.FRAME_SANDBOX


@pytest.mark.real_browser
def test_a_hostile_ui_cannot_break_out_or_flood_the_report(tmp_path):
    _need_browser()
    _add(tmp_path, "hostile",
         markup="</script><script>window.top.__pwned=1</script><p id=ok>still a frame</p>",
         script="for(const name of ['constructor','__proto__','toString'])"
                "{tinyassets.call(name,{}).catch(()=>{});}"
                "for(let i=0;i<3000;i++){tinyassets.call('act-'+(i%200),{}).catch(()=>{});}"
                "try{new WebSocket('wss://elsewhere.example/s')}catch(e){}")
    try:
        report = ui_preview.preview_app_ui(tmp_path, owner_user_id=OWNER, universe_id=HOME,
                                           ui_id="hostile", width=320, height=240)
    except ui_preview.PreviewUnavailable as exc:
        pytest.skip(str(exc))
    for name in ("constructor", "__proto__", "toString"):
        assert report["bridge_calls"][name] == 1
    assert len(report["bridge_calls"]) <= ui_preview.MAX_ACTIONS
    assert report["bridge_actions_dropped"] > 0
    assert report["delivery_error"] == ""
    assert report["png"].startswith(b"\x89PNG")


_TREE = """
import os, time
if os.fork() == 0:
    for _ in range(3):
        if os.fork() == 0:
            os.setsid()
            time.sleep(0.5)
            hog = bytearray(MEGS * 1024 * 1024)
            for i in range(0, len(hog), 4096): hog[i] = 1
            time.sleep(60)
            os._exit(0)
    time.sleep(60)
    os._exit(0)
time.sleep(60)
"""


@pytest.mark.skipif(__import__("sys").platform != "linux", reason="PID namespaces need Linux")
@pytest.mark.parametrize("megs, wall, breach", [(100, 30.0, "memory"), (1, 2.0, "timeout")])
def test_the_whole_render_tree_is_bounded_and_reaped(monkeypatch, megs, wall, breach):
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    bwrap = shutil.which("bwrap")
    if not bwrap:
        pytest.skip("bubblewrap is not installed")
    probe = subprocess.run(
        [bwrap, "--unshare-pid", "--bind", "/", "/", "--", "true"],
        capture_output=True, timeout=10,
    )
    if probe.returncode:
        pytest.skip("this host does not permit bubblewrap PID namespaces")
    monkeypatch.setattr(ui_preview, "TREE_MEMORY_BYTES", 150 * 1024 * 1024)
    argv = [sys.executable, "-c", _TREE.replace("MEGS", str(megs))]
    real_snapshot = ui_preview._proc_snapshot
    grandchildren = set()

    def remember():
        snapshot = real_snapshot()
        # The intermediate child forks exactly three detached grandchildren.
        for pid, (ppid, _, _, _) in snapshot.items():
            try:
                command = Path(f"/proc/{pid}/cmdline").read_bytes()
                stat = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b")", 1)[1].split()
            except OSError:
                continue
            if argv[-1].encode() in command and int(stat[3]) == pid and ppid != os.getpid():
                grandchildren.add(pid)
        return snapshot

    monkeypatch.setattr(ui_preview, "_proc_snapshot", remember)
    _, _, _, found = ui_preview._supervised(b"", wall, argv=argv)
    assert found.startswith(breach), found
    assert len(grandchildren) == 3, "observed all detached grandchildren by host PID"
    snapshot = real_snapshot()
    for pid in grandchildren:
        assert pid not in snapshot or snapshot[pid][2], f"grandchild {pid} survived"


def test_preview_refuses_a_read_only_caller_before_writing(tmp_path, monkeypatch):
    from tinyassets.api import app_ui, helpers

    denial = {"error": "write_access_denied"}
    monkeypatch.setattr(app_ui, "_binding_universe", lambda uid: HOME)
    monkeypatch.setattr(app_ui, "_binding_access", lambda uid, *, write: denial if write else None)
    monkeypatch.setattr(app_ui, "_authenticated_actor", lambda: OWNER)
    monkeypatch.setattr(app_ui, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(helpers, "_universe_dir", lambda uid: tmp_path)
    monkeypatch.setattr(ui_preview, "preview_app_ui", lambda *a, **k: {"png": b"PNG"})

    assert app_ui.preview_app_ui(universe_id=HOME, ui_id="village") == denial
    assert list(tmp_path.iterdir()) == []



@pytest.mark.parametrize("survivor", [True, False])
def test_failed_containment_disables_previews_and_keeps_locks(tmp_path, monkeypatch, survivor):
    import os
    import signal
    import sys
    import threading
    from types import SimpleNamespace
    from unittest.mock import Mock

    process = SimpleNamespace(pid=12345, stdin=io.BytesIO(), stdout=io.BytesIO(),
                              stderr=io.BytesIO(), returncode=-9, wait=Mock())
    spawn = Mock(return_value=process)
    clock = [0.0]
    joins = []

    class Reader:
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

        def join(self, timeout):
            joins.append(timeout)
            clock[0] += timeout

        def is_alive(self):
            return not survivor

    def sleep(seconds):
        clock[0] += seconds

    # Exercise the real host-slot context on every OS, with a harmless fake flock.
    flock = Mock()
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(
        flock=flock, LOCK_EX=2, LOCK_NB=4))
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ui_preview, "_SLOT", threading.BoundedSemaphore(1))
    monkeypatch.setattr(ui_preview, "_POISONED", "")
    monkeypatch.setattr(ui_preview, "_POISONED_HOST_FD", None)
    monkeypatch.setattr(ui_preview, "_spec_for", lambda *a: {})
    monkeypatch.setattr(ui_preview, "available", lambda: True)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ui_preview.shutil, "which", lambda name: "bwrap")
    monkeypatch.setattr(ui_preview.subprocess, "Popen", spawn)
    monkeypatch.setattr(ui_preview.threading, "Thread", Reader)
    monkeypatch.setattr(ui_preview.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ui_preview.time, "sleep", sleep)
    monkeypatch.setattr(os, "waitid", lambda *a: object(), raising=False)
    for name in ("P_PID", "WEXITED", "WNOHANG", "WNOWAIT"):
        monkeypatch.setattr(os, name, 1, raising=False)
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    kill = Mock()
    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(ui_preview, "_proc_snapshot", lambda: {12346: (12345, 1, False, 7)})
    monkeypatch.setattr(ui_preview, "_descendants", lambda *a: {12346} if survivor else set())
    try:
        with pytest.raises(ui_preview.PreviewUnavailable, match="could not be stopped") as first:
            ui_preview.preview_app_ui(tmp_path, owner_user_id=OWNER, universe_id=HOME, ui_id="x")
        with pytest.raises(ui_preview.PreviewUnavailable) as second:
            ui_preview.preview_app_ui(tmp_path, owner_user_id=OWNER, universe_id=HOME, ui_id="x")
        assert str(first.value) == str(second.value) == ui_preview._POISONED
        spawn.assert_called_once()
        assert not ui_preview._SLOT.acquire(blocking=False)
        assert ui_preview._POISONED_HOST_FD is not None
        os.fstat(ui_preview._POISONED_HOST_FD)  # still open, retaining the flock
        flock.assert_called_once()
        assert len(joins) == 2 and all(0 <= timeout <= 10 for timeout in joins)
        assert clock[0] < 10.2
        assert kill.call_args.args[0] == process.pid
        process.wait.assert_called_once()
    finally:
        if ui_preview._POISONED_HOST_FD is not None:
            os.close(ui_preview._POISONED_HOST_FD)
        # monkeypatch restores the original poison state and lock globals.


@pytest.mark.skipif(__import__("sys").platform != "linux", reason="PID namespaces need Linux")
def test_timeout_only_signals_the_unreaped_bwrap_pid(monkeypatch):
    import os
    import shutil
    import subprocess
    import sys

    bwrap = shutil.which("bwrap")
    if not bwrap:
        pytest.skip("bubblewrap is not installed")
    probe = subprocess.run(
        [bwrap, "--unshare-pid", "--bind", "/", "/", "--", "true"],
        capture_output=True, timeout=10,
    )
    if probe.returncode:
        pytest.skip("this host does not permit bubblewrap PID namespaces")
    real_popen, real_kill = subprocess.Popen, os.kill
    spawned, signalled = [], []

    def spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    def kill(pid, sig):
        assert spawned[0].returncode is None, "bwrap must remain unreaped"
        signalled.append(pid)
        return real_kill(pid, sig)

    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(os, "kill", kill)
    _, _, _, breach = ui_preview._supervised(
        b"", 2, argv=[sys.executable, "-c", _TREE.replace("MEGS", "1")])
    assert breach.startswith("timeout")
    assert signalled == [spawned[0].pid]
