"""Custody and native refresh proof through the real owner relay and broker."""
# ruff: noqa: F811 -- imported pytest fixtures are named by their consumers
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.support.broker_ipc import broker  # noqa: F401
from tests.test_owner_broker_egress import box  # noqa: F401
from tests.test_subscription_source_integration import connected  # noqa: F401
from tinyassets.providers.subscription_lease import custody, tmpfs_root
from tinyassets.providers.subscription_runtime import SubscriptionRuntime

pytestmark = pytest.mark.verifies(
    "box-subscription-sources:Broker custody and one-run native leases")


def decoded(runtime):
    state = custody(runtime, "read")["state"]
    return {name: base64.b64decode(value) for name, value in state.items()}


def test_broker_bounds_owner_custody(connected, monkeypatch):
    from tinyassets.broker import subscription_custody as store
    from tinyassets.owner_channel import request

    def call(action, source, **fields):
        return request(connected.owner.principal, {"op": "SUBSCRIPTION_CUSTODY",
            "document": {"action": action, "source": source, **fields}})

    entries = [call("begin", f"source-{i}") for i in range(16)]
    with pytest.raises(PermissionError):
        call("begin", "source-overflow")
    initial_size = len(json.dumps({f"source-{i}": entry for i, entry in enumerate(entries)}))
    monkeypatch.setattr(store, "MAX_OWNER_BYTES", initial_size + 100)
    state = {"token": base64.b64encode(b"x" * 40).decode()}
    saved = call("save", "source-0", revision=entries[0]["revision"], state=state)
    with pytest.raises(PermissionError):
        call("save", "source-1", revision=entries[1]["revision"], state=state)
    assert call("read", "source-0") == saved
    assert call("read", "source-1") == entries[1]
    with pytest.raises(PermissionError):
        call("save", "source-1", revision=entries[1]["revision"],
             state={"long-path-" + "x" * 100: "eA=="})


def test_connection_upgrade_accepts_record_before_lock_digest(connected):
    from tinyassets.providers.source_record import SourceRecord
    from tinyassets.providers.subscription_source import digest

    old = connected.source.document()
    old["state"]["location"] = "owner_box"
    old["sign_in"]["cli_login"]["install"].pop("lock_sha256", None)
    current = connected.source.document()
    current["sign_in"]["cli_login"]["install"]["lock_sha256"] = "a" * 64
    connected.source = SourceRecord.parse(current)
    marker = connected.directory / "connection"
    marker.write_text(json.dumps({"source": digest(old), "incarnation": "preserved"}))
    connected.require_accepted()
    assert json.loads(marker.read_text()) == {
        "source": digest(connected.source), "incarnation": "preserved"}
    old["sign_in"]["cli_login"]["install"]["version"] = "0.0.1"
    marker.write_text(json.dumps({"source": digest(old), "incarnation": "preserved"}))
    with pytest.raises(PermissionError):
        connected.require_accepted()


@pytest.mark.parametrize("record", [None, "{broken"])
def test_disconnect_without_resolvable_record(connected, record):
    from tinyassets.providers.subscription_runtime import disconnect_source

    assert connected.authenticated()
    path = connected.home / ".ta-cli/sources/fake-cli.json"
    if record is None:
        path.unlink()
    else:
        path.write_text(record)
    assert disconnect_source("fake-cli")["status"] == "disconnected"
    assert custody(connected, "read") == {"revision": None, "state": {}}
    assert not connected.directory.exists()


@pytest.mark.parametrize("failure", ["before_save", "after_save", "partial", "exhausted"])
def test_lease_writeback_retries_and_preserves_parseable_state(connected, monkeypatch, failure):
    from tinyassets.providers import subscription_lease as leases

    (connected.home / ".fake-login").write_text('{"token":"old"}')
    assert connected.authenticated()
    original = leases.custody
    attempts, delays, homes = [], [], []
    monkeypatch.setattr(leases, "time", type("Clock", (), {"sleep": delays.append}), raising=False)

    def interrupted(runtime, action, **fields):
        if action == "save":
            attempts.append(fields)
            if failure == "exhausted" or len(attempts) == 1:
                if failure == "after_save":
                    original(runtime, action, **fields)
                raise OSError("broker restarted")
        return original(runtime, action, **fields)

    monkeypatch.setattr(leases, "custody", interrupted)
    def run():
        with connected.lease():
            homes.append(connected.state_home)
            (connected.state_home / ".fake-login").write_text(
                '{"token":' if failure == "partial" else '{"token":"new"}')

    if failure == "exhausted":
        with pytest.raises(OSError, match="broker restarted"):
            run()
        assert len(attempts) == 3 and len(delays) == 2
    else:
        run()
        if failure == "before_save":
            assert len(attempts) == 2 and delays
        elif failure == "partial":
            assert not attempts
    expected = b'{"token":"old"}' if failure in {"partial", "exhausted"} else b'{"token":"new"}'
    assert decoded(connected)[".fake-login"] == expected
    assert all(not home.exists() for home in homes)


def test_legacy_login_moves_to_custody_and_status_uses_tmpfs(connected, monkeypatch):
    runtime = connected
    old = runtime.source.document()
    old["state"]["location"] = "owner_box"
    declaration = runtime.home / ".ta-cli/sources/fake-cli.json"
    declaration.write_text(json.dumps(old))
    from tinyassets.providers.subscription_source import digest

    marker = runtime.directory / "connection"
    connection = json.loads(marker.read_text())
    connection["source"] = digest(old)
    marker.write_text(json.dumps(connection))
    homes = []
    run = runtime.owner.tools.run

    def observe(*args, **kwargs):
        home = Path(kwargs["env"]["HOME"])
        homes.append(home)
        assert home.parent == tmpfs_root()
        assert home.stat().st_mode & 0o777 == 0o700
        assert not (runtime.home / ".fake-login").exists()
        return run(*args, **kwargs)

    monkeypatch.setattr(runtime.owner.tools, "run", observe)
    assert runtime.authenticated()
    assert json.loads(declaration.read_text())["state"]["location"] == "broker"
    runtime.require_accepted()
    assert decoded(runtime) == {".fake-login": b"fixture-login"}
    assert json.loads(marker.read_text())["incarnation"] == connection["incarnation"]
    assert json.loads(marker.read_text())["source"] == digest(runtime.source)
    assert homes and all(not home.exists() for home in homes)
    # A stale leftover from a crash never overwrites an already refreshed token.
    (runtime.home / ".fake-login").write_text("stale")
    assert runtime.authenticated()
    assert decoded(runtime)[".fake-login"] == b"fixture-login"
    assert not (runtime.home / ".fake-login").exists()


REFRESH_CLI = '''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
state = Path(os.environ['HOME']) / '.fake-login'
if sys.argv[1] == 'status':
    sys.exit(0 if state.is_file() else 1)
value = int(state.read_text()) + 1
time.sleep(.15)
state.write_text(str(value))
if sys.argv[-1] == 'fail':
    sys.exit(3)
print(json.dumps({'result': str(value), 'is_error': False}))
'''


def test_two_runs_serialize_refresh_and_failed_run_writes_back(connected):
    connected.binary.write_text(REFRESH_CLI)
    (connected.home / ".fake-login").write_text("0")
    before = set(tmpfs_root().glob("ta-native-*/"))
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(SubscriptionRuntime("fake-cli").invoke, "refresh") for _ in range(2)]
        assert sorted(f.result(20) for f in futures) == ["1", "2"]
    assert decoded(connected)[".fake-login"] == b"2"
    from tinyassets.providers.subscription_runtime import SubscriptionError

    with pytest.raises(SubscriptionError, match="invocation failed"):
        connected.invoke("fail")
    assert decoded(connected)[".fake-login"] == b"3"
    assert not (connected.home / ".fake-login").exists()
    assert set(tmpfs_root().glob("ta-native-*/")) == before


def test_disconnect_invalidates_late_writeback_and_reconnect_generation(connected):
    from tinyassets.onboarding.subscription_connect import disconnect

    assert connected.authenticated()
    leased = custody(connected, "read")
    assert disconnect("fake-cli")["status"] == "disconnected"
    assert not connected.directory.exists()
    assert not (connected.home / ".fake-login").exists()
    assert custody(connected, "read") == {"revision": None, "state": {}}
    for reconnect in (False, True):
        if reconnect:
            assert custody(connected, "begin")["revision"] != leased["revision"]
        with pytest.raises(PermissionError):
            custody(connected, "save", **leased)
        assert not custody(connected, "read")["state"]


def test_disconnect_cancels_pending_login_before_erasure(connected):
    from tinyassets.onboarding.subscription_connect import disconnect
    from tinyassets.providers.subscription_runtime import _logins, login_operation

    (connected.home / ".fake-login").unlink()
    state = login_operation("begin", source_id="fake-cli")
    login = _logins[state["flow"]]
    deadline = time.monotonic() + 20
    while not state["authorize_url"] and time.monotonic() < deadline:
        time.sleep(.02)
        state = login_operation("poll", flow=state["flow"])
    assert state["authorize_url"]
    assert disconnect("fake-cli")["status"] == "disconnected"
    assert login.cancelled.is_set() and not login.thread.is_alive()
    assert not connected.directory.exists()
    assert not (connected.home / ".fake-login").exists()
    assert not custody(connected, "read")["state"]
    with pytest.raises(PermissionError):
        login_operation("code", flow=state["flow"], code="approved")


def test_config_environment_and_files_follow_leased_home(connected):
    doc = connected.source.document()
    config = ".ta-cli/fake-cli/config/token"
    doc["state"]["paths"] = [config]
    cli = doc["sign_in"]["cli_login"]
    cli["env"] = {"FIXTURE_CONFIG": "{config}"}
    cli["files"] = {"settings.json": '{"enabled": true}'}
    cli["login"] = {"argv": ["{bin}", "login"], "state_checks": [{"path": config,
                      "present": ["token"]}]}
    (connected.home / ".ta-cli/sources/fake-cli.json").write_text(json.dumps(doc))
    runtime = SubscriptionRuntime("fake-cli")
    target = runtime.home / config
    target.parent.mkdir(exist_ok=True, parents=True)
    target.write_text('{"token":"fixture"}')
    runtime.accept()
    runtime.binary.write_text('''#!/usr/bin/env python3
import json, os
from pathlib import Path
config = Path(os.environ['FIXTURE_CONFIG'])
assert config.is_relative_to(os.environ['HOME'])
assert json.loads((config / 'settings.json').read_text())['enabled']
assert json.loads((config / 'token').read_text())['token'] == 'fixture'
print(json.dumps({'result':'config leased','is_error':False}))
''')
    assert runtime.invoke("hello") == "config leased"
    assert not target.exists()


@pytest.mark.parametrize("state", [{"../escape": "eA=="}, {"/absolute": "eA=="},
                                   {"token": "invalid"}])
def test_broker_refuses_malformed_state_without_replacing_credentials(connected, state):
    assert connected.authenticated()
    entry = custody(connected, "read")
    with pytest.raises(PermissionError):
        custody(connected, "save", revision=entry["revision"], state=state)
    assert custody(connected, "read") == entry


def test_failed_lease_preparation_never_writes_partial_state(connected):
    assert connected.authenticated()
    entry = custody(connected, "read")
    doc = connected.source.document()
    doc["state"]["paths"] = ["new-layout"]
    (connected.home / ".ta-cli/sources/fake-cli.json").write_text(json.dumps(doc))
    changed = SubscriptionRuntime("fake-cli")
    before = set(tmpfs_root().glob("ta-native-*/"))
    with pytest.raises(PermissionError, match="layout changed"):
        with changed.lease():
            pytest.fail("incompatible state was leased")
    assert custody(connected, "read") == entry
    assert set(tmpfs_root().glob("ta-native-*/")) == before


def test_refused_refresh_writeback_still_wipes_tmpfs(connected, monkeypatch):
    import tinyassets.providers.subscription_lease as leases

    assert connected.authenticated()
    original = leases.custody
    homes = []

    def refuse(runtime, action, **fields):
        if action == "save":
            raise PermissionError("synthetic unavailable broker")
        return original(runtime, action, **fields)

    monkeypatch.setattr(leases, "custody", refuse)
    with pytest.raises(PermissionError, match="unavailable broker"):
        with connected.lease():
            homes.append(connected.state_home)
            (connected.state_home / ".fake-login").write_text('"refreshed"')
    assert all(not home.exists() for home in homes)
    assert not (connected.home / ".fake-login").exists()


def test_account_erasure_removes_native_custody(connected, box):
    from tests.support.broker_double import broker_process
    from tinyassets.broker.vault import erase_owner

    assert connected.authenticated()
    with broker_process():
        erase_owner(box.shared, "owner")
    assert custody(connected, "read") == {"revision": None, "state": {}}


def test_cancelled_login_cannot_recreate_custody_after_disconnect(connected):
    import threading

    from tinyassets.onboarding.subscription_connect import disconnect
    from tinyassets.providers.subscription_runtime import SubscriptionError

    disconnect("fake-cli")
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(SubscriptionError, match="cancelled"):
        with connected.lease(login=True, cancelled=cancelled):
            pytest.fail("cancelled login acquired custody")
    assert custody(connected, "read") == {"revision": None, "state": {}}
    assert not connected.directory.exists()
