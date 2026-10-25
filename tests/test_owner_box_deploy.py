"""Synthetic deploy-control results and a loopback router/box acceptance server."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from scripts import owner_box_canary as canary
from scripts import roll_owner_boxes as driver

SHA = "a" * 40


@pytest.mark.parametrize("counts,code", [
    ({"slept": 2, "asleep": 3, "incomplete": 0}, 0),
    ({"slept": 1, "asleep": 3, "incomplete": 1}, 0),
    ({"error": "private owner data"}, 1),
    ({"slept": -1, "asleep": 0, "incomplete": 0}, 1),
])
def test_receipt_counts_are_allowlisted_and_a_busy_owner_defers_to_its_next_wake(counts, code):
    calls = []
    receipt, actual = driver.run(lambda: counts, lambda: calls.append("canary") or True,
                                 lambda: None)
    assert actual == code
    assert set(receipt) == {"counts", "canary", "control", "reason"}
    assert set(receipt["counts"]) == set(driver.OUTCOMES)
    assert calls == ([] if code else ["canary"])
    assert "private" not in json.dumps(receipt)
    if counts.get("incomplete"):
        # The busy owner adopts the image at its next wake; the canary decides.
        assert receipt["control"] == "deferred"
        assert receipt["reason"] == "passed"
        assert receipt["canary"] == "passed"
    elif code == 0:
        assert receipt["reason"] == "passed"
    else:
        assert receipt["control"] == "failed"
        assert receipt["reason"] == "invalid_drain"


def test_canary_failure_and_control_failure_are_fatal_and_do_not_leak():
    def broken():
        raise RuntimeError("owner private error")
    receipt, code = driver.run(broken, lambda: pytest.fail("control failed"), lambda: None)
    assert code == 1
    assert receipt["control"] == receipt["canary"] == "failed"
    assert receipt["reason"] == "control_failed"
    assert "private" not in json.dumps(receipt)
    receipt, code = driver.run(lambda: {"slept": 1, "asleep": 0, "incomplete": 0},
                               lambda: False, lambda: None)
    assert code == 1 and receipt["canary"] == "failed"
    assert receipt["reason"] == "canary_probe_failed"


def test_control_ssh_error_code_is_kept_and_other_text_is_not():
    secret = "LEAK-TOKEN-0123456789abcdef"

    def coded():
        raise subprocess.CalledProcessError(
            1, ["ssh"], output='{"error":"image_drain_failed"}')

    receipt, code = driver.run(coded, lambda: True, lambda: None)
    assert code == 1 and receipt["reason"] == "image_drain_failed"
    assert receipt["counts"] == {"slept": 0, "asleep": 0, "incomplete": 0}

    def leaked():
        raise subprocess.CalledProcessError(1, ["ssh"], output=json.dumps({"error": secret}))

    receipt, code = driver.run(leaked, lambda: True, lambda: None)
    assert receipt["reason"] == "control_failed"
    assert secret not in json.dumps(receipt)


def test_host_control_failure_prints_a_fixed_code(capsys, monkeypatch):
    def boom(_image_ref):
        raise PermissionError("peer uid 1 principal secret")

    monkeypatch.setattr(driver, "host_control", boom)
    code = driver.main(["--image-ref", "synthetic@sha256:" + "a" * 64, "--control"])
    assert code == 1
    assert capsys.readouterr().out.strip() == '{"error": "broker_peer_refused"}'


def test_deploy_uses_one_control_call_regardless_of_fleet_size():
    calls = []
    def control():
        calls.append("deploy")
        return {"slept": 6, "asleep": 1000, "incomplete": 0}
    receipt, code = driver.run(control, lambda: True, lambda: None)
    assert code == 0 and receipt["counts"] == {"slept": 6, "asleep": 1000, "incomplete": 0}
    assert calls == ["deploy"]


def test_acceptance_waits_for_post_drain_host_readiness():
    events = []
    receipt, code = driver.run(
        lambda: events.append("drain") or {"slept": 1, "asleep": 0, "incomplete": 0},
        lambda: events.append("canary") or True,
        lambda: events.append("prepare"),
        lambda: events.append("ready"))
    assert code == 0 and receipt["canary"] == "passed"
    assert events == ["prepare", "drain", "ready", "canary"]


def test_canary_public_wake_precedes_drain_and_acceptance(router_box):
    origin, state = router_box
    events = []

    def prepare():
        assert canary.wake(origin)[2] == "300001"
        events.append("awake")

    def control():
        assert events == ["awake"]
        events.append("slept")
        return {"slept": 1, "asleep": 1000, "incomplete": 0}

    def accept():
        assert events == ["awake", "slept"]
        return canary.probe(origin, SHA)

    receipt, code = driver.run(control, accept, prepare)
    assert code == 0 and receipt["canary"] == "passed"
    assert state.calls == ["/app/token", "/app/token", "/mcp", "/mcp", "/mcp"]
    assert state.broker_turns == 1


@pytest.fixture
def router_box(monkeypatch, tmp_path):
    import base64

    from scripts import canary_session

    for name, value in {"USER_ID": "canary-owner", "UNIVERSE_ID": "canary-home"}.items():
        monkeypatch.setenv("TINYASSETS_CANARY_" + name, value)
    monkeypatch.setenv("TINYASSETS_CANARY_SESSION_KEY",
                       base64.urlsafe_b64encode(b"k" * 32).decode())
    monkeypatch.setenv("TINYASSETS_CANARY_SESSION_STATE", str(tmp_path / "session"))
    monkeypatch.setenv("TINYASSETS_CANARY_SESSION_SEED", canary_session.seal("handle-0"))
    state = SimpleNamespace(failure="", calls=[], broker_turns=0, handle="handle-0")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, value, *, status=200, turn=False):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("x-tinyassets-box-uid",
                             "300002" if turn and state.failure == "wrong_box" else "300001")
            self.send_header("x-tinyassets-box-sha",
                             "b" * 40 if state.failure == "stale_sha" else SHA)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_GET(self):
            assert self.path == "/app/me"
            assert self.headers["Authorization"] == "Bearer synthetic-session"
            self.respond({"principal_id": "canary-owner", "universe_id":
                          "foreign" if state.failure == "wrong_home" else "canary-home"})

        def do_POST(self):
            doc = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.calls.append(self.path)
            if self.path == "/app/token":
                # Renewal always presents the newest rotated handle.
                assert doc == {"grant_type": "refresh_token", "session_ref": state.handle}
                if state.failure == "login":
                    return self.respond({"error": "refresh_failed"}, status=401)
                state.handle = "handle-" + str(len(state.calls))
                return self.respond({"access_token": "synthetic-session",
                                     "session_ref": state.handle})
            assert self.path == "/mcp"
            assert self.headers["Authorization"] == "Bearer synthetic-session"
            if doc["method"] == "tools/call":
                assert doc["params"]["name"] == "converse"
                assert doc["params"]["arguments"]["graph_id"] == "canary-home"
                state.broker_turns += 1  # synthetic model egress seam
                nonce = doc["params"]["arguments"]["message"].split()[-1]
                result = {"reply": "bad" if state.failure == "reply" else nonce}
                return self.respond({"jsonrpc": "2.0", "id": 2, "result": {
                    "isError": state.failure == "tool_error", "structuredContent": result}},
                    turn=True)
            self.respond({"jsonrpc": "2.0", "id": 1, "result": {}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_canary_signs_in_and_runs_one_turn_through_router_box(router_box):
    origin, state = router_box
    assert canary.main(["--origin", origin, "--expected-sha", SHA]) == 0
    assert state.calls == ["/app/token", "/mcp", "/mcp", "/mcp"]
    assert state.broker_turns == 1


@pytest.mark.parametrize("failure", ["login", "wrong_home", "wrong_box", "stale_sha",
                                     "reply", "tool_error"])
def test_canary_fails_closed_on_auth_identity_box_revision_and_reply(router_box, failure, capsys):
    origin, state = router_box
    state.failure = failure
    assert canary.main(["--origin", origin, "--expected-sha", SHA]) == 1
    assert capsys.readouterr().err == "owner_box_canary=failed\n"
    if failure in {"login", "wrong_home", "stale_sha"}:
        assert state.broker_turns == 0


def test_canary_missing_credentials_never_contacts_any_router(monkeypatch):
    monkeypatch.delenv("TINYASSETS_CANARY_USER_ID", raising=False)
    assert canary.main(["--origin", "http://unused.invalid", "--expected-sha", SHA]) == 1


def test_driver_cli_writes_one_receipt_and_invokes_canary(tmp_path, monkeypatch):
    calls = []

    def ssh(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps({"slept": 0, "asleep": 0, "incomplete": 0}))

    monkeypatch.setattr(driver.subprocess, "run", ssh)
    monkeypatch.setattr(canary, "probe", lambda *args: True)
    monkeypatch.setattr(canary, "wake", lambda *args: True)
    receipt = tmp_path / "receipt.json"
    assert driver.main(["--image-ref", "synthetic@sha256:" + "a" * 64,
                        "--ssh-target", "synthetic", "--ssh-key", "unused",
                        "--expected-sha", SHA, "--receipt", str(receipt)]) == 0
    assert len(calls) == 2  # control, then box-host readiness before public acceptance
    assert json.loads(receipt.read_text())["canary"] == "passed"
    assert set(tmp_path.iterdir()) == {receipt}


def test_box_provenance_overwrites_handler_headers_from_immutable_build_file(tmp_path, monkeypatch):
    import httpx
    from starlette.responses import JSONResponse

    from tinyassets import owner_activity, owner_ingress, owner_runtime
    from tinyassets.auth.provider import Identity

    secret = tmp_path / "secret"
    secret.write_text("s" * 43)
    build = tmp_path / "build-sha"
    build.write_text(SHA + "\n")
    monkeypatch.setattr(owner_ingress, "BUILD_SHA_FILE", build)
    monkeypatch.setattr(owner_activity, "activity", owner_activity.Activity())
    monkeypatch.setattr(owner_runtime, "current", lambda: SimpleNamespace(
        principal="canary-owner", uid=300001))
    app = owner_ingress.OwnerIngress(JSONResponse({"ok": True}, headers={
        "x-tinyassets-box-uid": "forged", "x-tinyassets-box-sha": "forged"}), secret)
    identity = Identity(user_id="canary-owner", username="canary")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                     base_url="http://synthetic-box") as client:
            response = await client.get("/app/me", headers={
                "x-box-secret": "s" * 43, "x-owner": "canary-owner", "x-owner-identity":
                base64.urlsafe_b64encode(json.dumps(identity.to_dict()).encode()).decode()})
            assert response.status_code == 200
            assert response.headers["x-tinyassets-box-uid"] == "300001"
            assert response.headers["x-tinyassets-box-sha"] == SHA
    asyncio.run(exercise())


def test_install_image_extracts_only_static_image_and_reuses_it(tmp_path, monkeypatch):
    commands = []

    def docker(command, **kwargs):
        commands.append(command)
        if command[:2] == ["docker", "create"]:
            return SimpleNamespace(stdout="synthetic-container\n")
        if command[0] == "tar":
            tree = driver.Path(command[-1])
            (tree / "data").mkdir()
            (tree / "data/forbidden").write_text("not mounted")
            (tree / "app").mkdir()
            (tree / "app/code.py").write_text("code")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(driver.subprocess, "run", docker)
    root = driver.install_image("synthetic@sha256:" + "a" * 64, "sha256:" + "b" * 64, tmp_path)
    assert not (root / "data").exists()
    assert (root / "home/owner").is_dir()
    assert (root / ".box-image-id").read_text().strip() == "sha256:" + "b" * 64
    assert (root / "app/code.py").stat().st_mode & 0o222 == 0
    assert not any("run" in command or "--mount" in command for command in commands)
    assert commands[-1] == ["docker", "rm", "synthetic-container"]
    count = len(commands)
    assert driver.install_image("synthetic", "sha256:" + "b" * 64, tmp_path) == root
    assert len(commands) == count


def test_existing_secret_loader_can_select_only_the_canary_profile(tmp_path):
    source = tmp_path / "synthetic.env"
    source.write_text("TINYASSETS_CANARY_SESSION_KEY=synthetic-only-key\n"
                      "UNRELATED_CANARY_PROBE_SECRET=must-stay-out\n")
    env = {**os.environ, "TINYASSETS_SECRETS_PROFILE": "canary",
           "TINYASSETS_SECRETS_VENDOR": "plaintext",
           "TINYASSETS_SECRETS_PLAINTEXT_PATH": str(source)}
    env.pop("UNRELATED_CANARY_PROBE_SECRET", None)
    if os.name == "nt":
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                   '. ./scripts/load_secrets.ps1; '
                   'if ($env:TINYASSETS_CANARY_SESSION_KEY -ne "synthetic-only-key") {exit 1}; '
                   'if ($env:UNRELATED_CANARY_PROBE_SECRET) {exit 2}']
    else:
        command = ["bash", "-c", '. scripts/load_secrets.sh && '
                   'test "$TINYASSETS_CANARY_SESSION_KEY" = synthetic-only-key && '
                   'test -z "${UNRELATED_CANARY_PROBE_SECRET:-}"']
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_workflow_requires_box_acceptance_after_health_and_keeps_failure_local():
    from pathlib import Path

    import yaml

    workflow = yaml.safe_load((Path(__file__).parents[1] /
                               ".github/workflows/deploy-prod.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    roll = next(step for step in steps if step.get("id") == "owner_boxes")
    app = next(step for step in steps if step.get("id") == "app_probe")
    rollback = next(step for step in steps if step.get("name") ==
                    "Roll back if the public canary is red")
    assert steps.index(app) < steps.index(roll)
    assert "continue-on-error" not in roll and "if" not in roll
    assert "scripts/roll_owner_boxes.py" in roll["run"]
    assert "--expected-sha" in roll["run"] and "--receipt" in roll["run"]
    assert "founder_canary_waiver" not in str(roll)
    assert "owner_boxes" not in rollback["if"]
    artifact = next(step for step in steps if step.get("with", {}).get("name") == "owner-box-roll")
    assert "always()" in artifact["if"]
    assert "runner.temp" in artifact["with"]["path"]


pytestmark = pytest.mark.verifies('owner-daemon-in-box:C12')
