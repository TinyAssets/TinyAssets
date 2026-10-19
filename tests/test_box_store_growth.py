"""C10 growth proofs use a fake HTTP API and a disposable loop device only."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location(
        "box_store_grow", ROOT / "deploy/box-store-grow.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_growth_alert_never_prints_exception_credentials(tmp_path, monkeypatch, capsys):
    grow = module()
    token = "synthetic-never-log-token"
    monkeypatch.setattr(sys, "argv", ["grow", "--lock", str(tmp_path / "lock")])
    monkeypatch.setattr(grow.os, "geteuid", lambda: 0)
    def failure(*args):
        raise RuntimeError(token)
    monkeypatch.setattr(grow, "grow", failure)
    logs = []
    monkeypatch.setattr(grow.subprocess, "run", lambda args, **kw: logs.append(args))
    assert grow.main() == 1
    captured = capsys.readouterr()
    assert "ALERT" in captured.err
    assert token not in captured.out + captured.err + repr(logs)
    assert logs[0][1:3] == ["-p", "daemon.crit"]


@pytest.mark.parametrize("maximum", ["101", "0", "-1"])
def test_growth_refuses_cap_outside_budget_before_api(monkeypatch, maximum):
    grow = module()
    monkeypatch.setattr(grow, "private_file", lambda _: (
        "BOX_STORE_DEVICE=/dev/loop0 BOX_STORE_VOLUME_ID=" + "a" * 36 +
        f" BOX_STORE_REGION=test BOX_STORE_DROPLET_ID=1 BOX_STORE_MAX_GIB={maximum}"))
    monkeypatch.setattr(grow, "api", lambda *args: pytest.fail("no API at invalid cap"))
    with pytest.raises(ValueError, match="100 GiB"):
        grow.grow("unused", "/store")


def real_growth_proof(root):
    """Called inside the root, network-isolated XFS/runsc oracle."""
    grow = module()
    backing = root / "growth.img"
    mount = root / "growth"
    mount.mkdir()
    with backing.open("wb") as stream:
        stream.truncate(grow.GIB)
    grow.command("mkfs.xfs", "-f", "-m", "reflink=1", str(backing))
    device = grow.command("losetup", "--find", "--show", str(backing))
    link = Path("/dev/disk/by-id/scsi-0DO_Volume_growth-proof")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(device)
    grow.command("mount", device, str(mount))
    token = "synthetic-growth-token-never-log"
    token_path = root / "growth-token"
    token_path.write_text(token)
    token_path.chmod(0o600)
    config = root / "growth.conf"
    base = (f"BOX_STORE_DEVICE={link}\nBOX_STORE_VOLUME_ID={'a' * 36}\n"
            "BOX_STORE_REGION=test BOX_STORE_DROPLET_ID=1 BOX_STORE_STEP_GIB=1\n"
            f"BOX_STORE_TOKEN_FILE={token_path}\n")
    state = {"size": 1, "fail": False, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.headers["Authorization"] == "Bearer " + token
            state["requests"].append("GET")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"volume": {
                "name": "growth-proof", "region": {"slug": "test"}, "droplet_ids": [1],
                "size_gigabytes": state["size"]}}).encode())

        def do_POST(self):
            assert self.headers["Authorization"] == "Bearer " + token
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert payload == {"type": "resize", "size_gigabytes": 2, "region": "test"}
            state["requests"].append("POST")
            if state["fail"]:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(token.encode())
                return
            with backing.open("r+b") as stream:
                stream.truncate(2 * grow.GIB)
            grow.command("losetup", "--set-capacity", device)
            state["size"] = 2
            self.send_response(201)
            self.end_headers()
            self.wfile.write(b'{"action":{"status":"in-progress"}}')

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    original_open = grow.urllib.request.urlopen
    def local_api(request, **kwargs):
        assert request.full_url.startswith("https://api.digitalocean.com/v2/")
        request.full_url = (f"http://127.0.0.1:{server.server_port}/"
                            + request.full_url.split("/v2/")[1])
        return original_open(request, **kwargs)
    grow.urllib.request.urlopen = local_api
    try:
        def configuration(threshold, cap=2):
            config.write_text(base + f"BOX_STORE_FREE_PERCENT={threshold} "
                              f"BOX_STORE_MAX_GIB={cap}\n")
            config.chmod(0o600)
        before = os.statvfs(mount).f_blocks
        configuration(1)
        grow.grow(str(config), str(mount))
        assert state["requests"] == [] and os.statvfs(mount).f_blocks == before
        configuration(99, cap=1)
        with pytest.raises(ValueError, match="spending cap"):
            grow.grow(str(config), str(mount))
        assert state["requests"] == [] and state["size"] == 1
        configuration(99)
        state["fail"] = True
        argv = sys.argv
        sys.argv = ["grow", "--config", str(config), "--mount", str(mount),
                    "--lock", str(root / "growth.lock")]
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                assert grow.main() == 1
        finally:
            sys.argv = argv
        assert "ALERT" in output.getvalue() and token not in output.getvalue()
        assert state["size"] == 1 and os.statvfs(mount).f_blocks == before
        state["fail"] = False
        # An open cwd makes unmount impossible. The actual resize must stay online.
        old_cwd = os.getcwd()
        os.chdir(mount)
        try:
            grow.grow(str(config), str(mount))
            assert os.statvfs(mount).f_blocks > before
            assert state["size"] == 2
        finally:
            os.chdir(old_cwd)
        configuration(99)
        requests = list(state["requests"])
        with pytest.raises(ValueError, match="spending cap"):
            grow.grow(str(config), str(mount))
        assert state["requests"] == requests and state["size"] == 2
        print("PROOF C10 online block growth cap API-failure token-secrecy PASS", flush=True)
    finally:
        grow.urllib.request.urlopen = original_open
        server.shutdown()
        server.server_close()
        thread.join()
        grow.command("umount", str(mount))
        grow.command("losetup", "--detach", device)
        link.unlink()
