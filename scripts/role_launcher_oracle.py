"""Production-image launcher/broker substep, not engine-class acceptance.

Synthetic data, no network or host mount. The oracle supplies a trusted daemon
fixture as the launcher's child; it does NOT claim the image CMD launches the
real daemon or that accounting/refresh and engine routing are complete.
"""
from __future__ import annotations

import array
import hashlib
import json
import os
import runpy
import signal
import socket
import struct
import tempfile
import time
import traceback
from pathlib import Path


def _request(path, document, *, descriptor=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
        connection.settimeout(35)
        connection.connect(str(path))
        payload = document if isinstance(document, bytes) else json.dumps(document).encode()
        if descriptor is None:
            connection.sendall(payload)
        else:
            connection.sendmsg([payload], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                           array.array("i", [descriptor]))])
        return json.loads(connection.recv(4096))


def _fence(path, proof, **extra):
    from tinyassets import rpc_frames as rf

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(path))
        _pid, uid, gid = struct.unpack("3i", connection.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        assert (uid, gid) == (1002, 1002)
        connection.sendall(rf.control(rf.CONNECTION, {"op": "FENCE", "proof": proof, **extra}))
        return rf.read_frame_blocking(connection).control()


def _seed_ledger(root):
    """Synthetic setup before role retirement; never a daemon ledger open."""
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(root / ".broker/outbound.db", data_root=root)
    for owner in ("alice", "bob"):
        ledger.create_connection(
            connection_id=f"conn-{owner}", owner_user_id=owner, connection_class="http",
            connection_type="http", auth_scheme="bearer", scopes=("GET", "POST"),
            provider="http", destination=f"compute:{owner}", credential_ref="vault://http/fixture",
            allowed_endpoints=[{"host": "models.example.com", "path_template": "/v1/chat",
                                "methods": ["POST"]}],
        )
        ledger.grant_connection(grant_id=f"grant-{owner}", connection_id=f"conn-{owner}",
                                owner_user_id=owner, universe_id=owner)
        ledger.configure_capability(
            connection_id=f"conn-{owner}", capability_kind="model_use", enabled=True,
            descriptor={"wire": "chat_messages", "models": [
                {"id": f"{owner}-fixture", "tools": True, "context": 20000}], "billing": "free"},
        )
    with ledger._connect() as conn:
        conn.execute("INSERT INTO connection_capabilities VALUES (?, ?, ?, 0)",
                     ("conn-bob", "model_discovery", "malformed pricing must still block"))
    for path in (root / ".broker").glob("outbound.db*"):
        os.chown(path, 1002, 1101)
        path.chmod(0o600)


def _query_consumers(root, supervisor):
    from tinyassets import rpc_frames as rf
    from tinyassets.api.connection_uses import model_use_refusal
    from tinyassets.broker.client import BrokerClient, BrokerRefused
    from tinyassets.broker.ledger_queries import (
        DISCOVERY_FACTS,
        GRANTED_RESOURCE,
        HAS_PRICED_SOURCE,
        query_ledger,
    )
    from tinyassets.providers.definition import register_definition
    from tinyassets.providers.discovery_http import (
        ModelDiscoveryUnavailable,
        read_granted_discovery_document,
    )
    from tinyassets.providers.discovery_snapshot import _context
    from tinyassets.storage.outbound_connections import GrantResolutionError

    os.environ["TINYASSETS_DATA_DIR"] = str(root)
    os.environ["TINYASSETS_CREDENTIAL_BROKER"] = "process"
    definition = register_definition(
        universe_id="alice", owner_user_id="alice", access_method="api_key_http",
        protocol="chat_messages", model="alice-fixture", ref="grant-alice",
    )
    context = _context(root, "alice", "alice", definition.id)
    assert context.profile.descriptor()["models"][0]["id"] == "alice-fixture"
    assert len(context.digest) == 64
    assert model_use_refusal(base=root, uid="alice", actor="alice", connection_id="conn-alice",
                             grant_id="grant-alice") is None
    from tinyassets.storage.outbound_connections import MODEL_USE_PRICED_CONFLICT

    assert model_use_refusal(base=root, uid="bob", actor="bob", connection_id="conn-bob",
                             grant_id="grant-bob")["detail"] == MODEL_USE_PRICED_CONFLICT
    arguments = dict(query=DISCOVERY_FACTS, principal="alice", command_center="alice",
                     grant_id="grant-alice", connection_id="conn-alice")
    facts = query_ledger(root, **arguments)
    assert facts["resource"]["owner_user_id"] == "alice"
    assert "bob-fixture" not in json.dumps(facts)
    for changes in ({"principal": "bob"}, {"command_center": "bob"},
                    {"grant_id": "grant-bob"}, {"connection_id": "conn-bob"}):
        try:
            query_ledger(root, **(arguments | changes))
        except GrantResolutionError:
            pass
        else:
            raise AssertionError("broker returned foreign discovery facts")
    assert query_ledger(root, **(arguments | {"query": HAS_PRICED_SOURCE})) == {"priced": False}
    # A profile-independent query still checks scope and the live fence. Bob's
    # malformed model profile cannot break his otherwise legitimate HTTP grant.
    for owner in ("alice", "bob"):
        resource = query_ledger(root, query=GRANTED_RESOURCE, principal=owner,
                                command_center=owner, grant_id=f"grant-{owner}")
        assert resource["resource"]["owner_user_id"] == owner
        assert set(resource) == {"resource"}
    for owner, grant, reason in (("alice", "grant-alice", "missing_discovery_scope"),
                                  ("alice", "grant-bob", "source_revoked")):
        try:
            read_granted_discovery_document(
                db_path=root / "outbound.db", grant_id=grant, owner_user_id=owner,
                universe_id=owner, url="https://models.example.com/ungranted",
            )
        except ModelDiscoveryUnavailable as exc:
            assert exc.reason == reason
        else:
            raise AssertionError("discovery HTTP admitted foreign scope/ungranted URL")
    print("D20 actual discovery HTTP via launcher broker: scoped resource/endpoint "
          "refusals, malformed-profile independence, no daemon ledger: PASS "
          "(successful HTTP stream not claimed)", flush=True)
    bad = BrokerClient(supervisor.socket_path, principal="alice", command_center="alice",
                       fence=lambda: (1, "wrong"), verify_peer=supervisor.verify_broker, timeout=5)
    try:
        bad.ledger_query(query=DISCOVERY_FACTS, grant_id="grant-alice")
    except BrokerRefused:
        pass
    else:
        raise AssertionError("broker admitted unfenced ledger query")
    generation, token = supervisor.fence()
    for extra in ({"query": "_connect"}, {"sql": "SELECT * FROM outbound_connections"},
                  {"path": "/data/outbound.db"}):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(5)
            connection.connect(str(supervisor.socket_path))
            supervisor.verify_broker(connection)
            connection.sendall(rf.control(rf.CONNECTION, {
                "op": "LEDGER_QUERY", "generation": generation, "token": token,
                **arguments, **extra,
            }))
            assert rf.read_frame_blocking(connection).control()["op"] == "LEDGER_REFUSED"
    assert not (root / "outbound.db").exists(), "daemon constructed a fallback ledger"
    print("D11 actual discovery/priced-source consumers via launcher broker; "
          "foreign scope, fence, SQL/path/method refusal; no local ledger: PASS", flush=True)


def _daemon(root, run, ready, control, launcher):
    launcher["retire_child"]("daemon")
    launcher["close_descriptors"]((ready, control))
    assert os.read(ready, 1) == b"1"
    os.close(ready)
    path = run / "launcher.sock"
    from tinyassets.broker import supervisor as supervisor_module

    supervisor_module.LAUNCHER_SOCKET = path
    supervisor_module.BROKER_SOCKET = run / "broker/broker.sock"
    supervisor = supervisor_module.BrokerSupervisor(root)
    proof = supervisor._proof
    start = {"op": "START_BROKER", "proof_sha256": hashlib.sha256(proof.encode()).hexdigest()}
    # Same uid is insufficient. The parent has not reaped this child, so this
    # exercise cannot accidentally test a recycled pid.
    wrong = os.fork()
    if wrong == 0:
        try:
            for name in ("mem", "environ"):
                try:
                    descriptor = os.open(f"/proc/{os.getppid()}/{name}", os.O_RDONLY)
                except PermissionError:
                    pass
                else:
                    os.close(descriptor)
                    raise AssertionError("same-uid child could open daemon private procfs")
            try:
                assert _request(path, start)["op"] == "REFUSED"
            except (ConnectionResetError, BrokenPipeError):
                # The peer gate intentionally closes without reading payload.
                # AF_UNIX may reset a socket that has unread request bytes.
                pass
            os._exit(0)
        except BaseException:
            traceback.print_exc()
            os._exit(1)
    assert os.waitpid(wrong, 0)[1] == 0
    for bad in (b"{", b"[" * 1100 + b"]" * 1100, b"x" * 5000,
                {**start, "argv": ["/bin/sh"]},
                {**start, "op": "spawn"}, {**start, "proof_sha256": "bad"}):
        assert _request(path, bad)["op"] == "REFUSED"
    with open("/dev/null", "rb") as handle:
        assert _request(path, start, descriptor=handle.fileno())["op"] == "REFUSED"
    answer = _request(path, start)
    assert answer["op"] == "BROKER_READY" and answer["restarts"] == 0
    assert answer["socket"] == str(run / "broker/broker.sock")
    socket_info = Path(answer["socket"]).stat()
    assert (socket_info.st_uid, socket_info.st_gid, socket_info.st_mode & 0o7777) == (
        1002, 1101, 0o660)
    fenced = _fence(answer["socket"], proof)
    assert fenced["op"] == "FENCE_ACK" and fenced["generation"] == 1 and fenced["token"]
    assert _fence(answer["socket"], proof) == fenced
    assert _fence(answer["socket"], "wrong")["op"] == "FENCE_REFUSED"
    assert _fence(answer["socket"], proof, generation=1)["op"] == "FENCE_REFUSED"
    supervisor.start()
    assert supervisor_module.get_supervisor(root) is supervisor
    assert supervisor.fence() == (fenced["generation"], fenced["token"])
    _query_consumers(root, supervisor)
    # A socket at the daemon uid must never receive a proof, even when its
    # pathname was supplied by trusted startup configuration.
    fake_path = root / "fake-broker.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as fake:
        fake.bind(str(fake_path))
        fake.listen(1)
        fake.settimeout(5)
        actual_path = supervisor._socket
        supervisor._socket = fake_path
        try:
            supervisor._fence()
        except supervisor_module.BrokerUidSplitRequired:
            pass
        else:
            raise AssertionError("daemon accepted a same-uid broker")
        finally:
            supervisor._socket = actual_path
        with fake.accept()[0] as received:
            received.settimeout(5)
            assert received.recv(1) == b"", "daemon disclosed proof before broker authentication"
    fake_path.unlink()
    print("daemon non-dumpable procfs; same-uid fake broker gets no proof: PASS", flush=True)
    from tinyassets import rpc_frames as rf

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(answer["socket"])
        connection.sendall(rf.control(1, {
            "op": "OPEN", "principal": "alice", "command_center": "alice",
            "op_id": "00000000-0000-4000-8000-000000000001",
            "grant_id": "absent", "connection_id": "absent", "verb": "GET",
            "request": {}, "generation": fenced["generation"], "token": fenced["token"],
        }))
        refused = rf.read_frame_blocking(connection).control()
        assert refused["outcome"] == "refused"
        assert refused["error_class"] == "GrantResolutionError"
    for private in ("outbound.db", "state/fence.json", ".outbound-proxy"):
        try:
            (root / ".broker" / private).stat()
        except PermissionError:
            pass
        else:
            raise AssertionError(f"daemon could access {private}")
    print("launcher exact-pid, malformed/oversized/SCM_RIGHTS/static-operation refusals: PASS",
          flush=True)
    print("launcher broker uid=1002; socket=1002:1101/0660; daemon fences without disk token: PASS",
          flush=True)
    assert _request(path, {**start, "proof_sha256": "f" * 64})["op"] == "REFUSED"
    os.write(control, b"K")
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        answer = _request(path, start)
        if answer["op"] == "BROKER_READY" and answer["restarts"] == 1:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("launcher did not restart broker")
    assert _fence(answer["socket"], proof) == fenced
    assert supervisor.fence() == (fenced["generation"], fenced["token"])
    _query_consumers(root, supervisor)
    from tinyassets.storage.outbound_connections import (
        GrantResolutionError,
        ProxyRequestError,
        _broker_channel,
    )

    os.environ[supervisor_module.ENV_SWITCH] = supervisor_module.PROCESS
    channel = _broker_channel(root, principal="alice", command_center="alice",
                              grant_id="absent", connection_id="absent")
    try:
        channel._client.request(grant_id="absent", connection_id="absent", verb="GET",
                                request={}, op_id="00000000-0000-4000-8000-000000000002")
    except GrantResolutionError:
        pass
    else:
        raise AssertionError("broker admitted missing grant after restart")
    supervisor.stop()
    assert Path(answer["socket"]).is_socket(), "daemon stop unlinked broker socket"
    assert _fence(answer["socket"], proof) == fenced, "daemon stop killed the broker"
    try:
        _broker_channel(root, principal="alice", command_center="alice",
                        grant_id="absent", connection_id="absent")
    except ProxyRequestError:
        pass
    else:
        raise AssertionError("stopped supervisor retained owner authority")
    print("daemon supervisor acquisition, private-memory channel after restart, "
          "stop without signal: PASS",
          flush=True)
    print("launcher broker crash/restart preserves in-memory owner fence: PASS", flush=True)
    os.write(control, b"D")
    # The launcher must signal this foreign uid at shutdown.
    while True:
        signal.pause()


def main():
    launcher = runpy.run_path("/usr/local/libexec/ta-launch.py")
    permissions = runpy.run_path("/usr/local/libexec/ta-egress-migration.py")["_permissions"]

    def directory_permissions(path, uid, gid, mode):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            permissions(fd, uid, gid, mode)
        finally:
            os.close(fd)
    root = Path(tempfile.mkdtemp(prefix="uid-launcher-data-"))
    root.chmod(0o755)
    os.chown(root, 1001, 1001)
    for child in (".broker", ".broker/state", ".broker/.outbound-proxy"):
        directory = root / child
        directory.mkdir(mode=0o2700)
        directory_permissions(directory, 1002, 1101, 0o2700)
    _seed_ledger(root)
    run = Path(tempfile.mkdtemp(prefix="uid-launcher-", dir="/run"))
    run.chmod(0o755)
    ipc = run / "broker"
    ipc.mkdir()
    directory_permissions(ipc, 1002, 1101, 0o2750)
    runner = os.fork()
    if runner == 0:
        server = None
        try:
            # Serving with migration capabilities must fail before any bind.
            try:
                launcher["BrokerLauncher"](root, run, os.getpid())
            except launcher["Refused"]:
                pass
            else:
                raise AssertionError("launcher admitted migration capabilities")
            launcher["retire_migration_authority"]()
            print("launcher migration-capability retirement/readback and pre-bind refusal: PASS",
                  flush=True)
            ready_read, ready_write = os.pipe()
            control_read, control_write = os.pipe()
            daemon = os.fork()
            if daemon == 0:
                try:
                    _daemon(root, run, ready_read, control_write, launcher)
                except BaseException:
                    traceback.print_exc()
                    os._exit(1)
            os.close(ready_read)
            os.close(control_write)
            os.set_blocking(control_read, False)
            server = launcher["BrokerLauncher"](root, run, daemon)
            server.bind()
            assert not server.listener.get_inheritable()
            info = (run / "launcher.sock").stat()
            assert (info.st_uid, info.st_gid, info.st_mode & 0o7777) == (0, 1001, 0o660)
            stranger = os.fork()
            if stranger == 0:
                try:
                    launcher["retire_child"]("broker")
                    launcher["close_descriptors"]()
                    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
                        try:
                            connection.connect(str(run / "launcher.sock"))
                        except PermissionError:
                            os._exit(0)
                    os._exit(1)
                except BaseException:
                    os._exit(1)
            assert os.waitpid(stranger, 0)[1] == 0
            os.write(ready_write, b"1")
            os.close(ready_write)
            complete = False
            deadline = time.monotonic() + 100
            baseline = len(os.listdir("/proc/self/fd"))
            while time.monotonic() < deadline and server.poll():
                try:
                    command = os.read(control_read, 1)
                except BlockingIOError:
                    continue
                if command == b"K":
                    fields = dict(line.split(":", 1) for line in Path(
                        f"/proc/{server.broker_pid}/status").read_text().splitlines())
                    assert all(int(fields[key], 16) == 0 for key in launcher["CAP_FIELDS"])
                    assert int(fields["NoNewPrivs"]) == 1
                    assert fields["Groups"].split() == ["1102"]
                    # /proc/<pid> itself can retain the process uid even when
                    # non-dumpable. Prove the protected file and actual read.
                    environment = Path(f"/proc/{server.broker_pid}/environ")
                    assert environment.stat().st_uid == 0
                    sibling = os.fork()
                    if sibling == 0:
                        try:
                            launcher["retire_child"]("broker")
                            launcher["close_descriptors"]()
                            try:
                                environment.read_bytes()
                            except PermissionError:
                                os._exit(0)
                            os._exit(1)
                        except BaseException:
                            os._exit(1)
                    assert os.waitpid(sibling, 0)[1] == 0
                    assert len(os.listdir("/proc/self/fd")) == baseline
                    os.kill(server.broker_pid, signal.SIGKILL)
                elif command == b"D":
                    complete = True
                    break
            assert complete, "daemon fixture exited or timed out before acceptance"
            server.stop()
            server = None
            print("broker caps=all-zero nnp=1 non-dumpable; no received-fd leak; "
                  "cross-uid shutdown: PASS",
                  flush=True)
            os._exit(0)
        except BaseException:
            traceback.print_exc()
            if server is not None:
                server.stop()
            os._exit(1)
    assert os.waitpid(runner, 0)[1] == 0
    assert not (root / ".broker/owner.json").exists()
    info = (root / ".broker/outbound.db").stat()
    assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (1002, 1101, 0o600)
    assert not (root / "outbound.db").exists()
    print("launcher wrong-uid filesystem refusal; actual broker uses private ledger: PASS",
          flush=True)
    print("LAUNCHER/BROKER SUBSTEP ONLY: real daemon CMD, streams/accounting, "
          "engine classes pending",
          flush=True)


if __name__ == "__main__":
    main()
