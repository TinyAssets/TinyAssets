"""Network-less process smoke against migrated roots, with real kernel peers.

This is a process venue, not the gVisor isolation acceptance. Owner processes
use private mount/network namespaces, the fixed owner mount and their assigned UIDs.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, "/app")


def owner(root, relay, secret, socket, uid, generation, centers, inherited_mounts):
    import fcntl
    import socket as sockets
    import struct

    # Each owner binds the real fixed proxy port in its own null netns.
    # Bring up only loopback before dropping privilege; no external interface
    # or route is created. SIOCGIFFLAGS/SIOCSIFFLAGS preserve the other flags.
    with sockets.socket(sockets.AF_INET, sockets.SOCK_DGRAM) as interface:
        flags = bytearray(fcntl.ioctl(interface.fileno(), 0x8913, struct.pack("16sH", b"lo", 0)))
        struct.pack_into("H", flags, 16, struct.unpack_from("H", flags, 16)[0] | 1)
        fcntl.ioctl(interface.fileno(), 0x8914, flags)
    subprocess.run(["mount", "--bind", root, "/home/owner"], check=True)
    # The native process venue inherits the launcher's mount namespace, unlike
    # runsc's OCI root. Keep only its fixed owner bind; release every inherited
    # attachment using the launcher's admitted runtime records, without opening
    # a volume or enumerating its content.
    for attachment in json.loads(inherited_mounts):
        subprocess.run(["umount", "--no-canonicalize", attachment], check=True)
    for original, mounted in ((relay, "/run/owner-relay"), (secret, "/run/owner-secret")):
        Path(mounted).touch()
        subprocess.run(["mount", "--bind", original, mounted], check=True)
    uid = int(uid)
    os.environ["TINYASSETS_VOLUME_GENERATION"] = generation
    os.environ["TINYASSETS_ADMITTED_CENTERS"] = centers
    os.setgroups([])
    os.setgid(uid)
    os.setuid(uid)
    from tinyassets.owner_daemon import create_app, serve

    app = create_app(relay=Path("/run/owner-relay"), secret_file=Path("/run/owner-secret"))
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def probe(request):
        from tinyassets.api.universe import _action_control_daemon
        from tinyassets.broker.owner_reads import remote
        from tinyassets.conversation_store import record_turn

        doc = await request.json()
        if doc['action'].startswith('restore_'):
            import uuid

            from tinyassets.agent_loop.owner_tools import OwnerTools
            from tinyassets.boxes.provider import BoxOperationRefused
            from tinyassets.owner_runtime import current

            runtime = current()
            core = runtime.tools
            handle = core.bind(runtime.principal)
            logical = '/cc/' + doc['center'] + '/.restore-proof'
            tools = OwnerTools(runtime.root / doc['center'])
            if doc['action'] == 'restore_prepare':
                core.write(handle, core.new_operation(uuid.uuid4().hex), logical,
                           b'snapshot', max_bytes=100)
                return JSONResponse({key: core.new_operation(uuid.uuid4().hex)
                                     for key in ('write', 'bash', 'unseen')})
            operations = doc['operations']
            if doc['action'] == 'restore_mutate':
                core.write(handle, operations['write'], logical, b'after', max_bytes=100)
                await tools.bash(operations['bash'], 'printf once > .restore-bash')
                return JSONResponse({'mutated': True})
            assert (runtime.root / doc['center'] / '.restore-proof').read_bytes() == b'snapshot'
            refused = 0
            for operation in (operations['write'], operations['unseen']):
                try:
                    core.write(handle, operation, logical, b'replay', max_bytes=100)
                except BoxOperationRefused:
                    refused += 1
                else:
                    raise AssertionError('old-generation write replayed')
            try:
                await tools.bash(operations['bash'], 'printf replay > .restore-bash')
            except BoxOperationRefused:
                refused += 1
            else:
                raise AssertionError('old-generation bash replayed')
            assert not (runtime.root / doc['center'] / '.restore-bash').exists()
            core.write(handle, core.new_operation(uuid.uuid4().hex), logical,
                       b'new work', max_bytes=100)
            await tools.bash(core.new_operation(uuid.uuid4().hex), 'printf fresh > .restore-bash')
            assert (runtime.root / doc['center'] / '.restore-bash').read_text() == 'fresh'
            return JSONResponse({'replay_refusals': refused, 'new_operations': 2})
        if doc["action"] == "model_ask":
            from tinyassets.api.pending_requests import request_from_user
            from tinyassets.daemon_server import get_founder_home

            center = get_founder_home(Path("/home/owner"), request.headers["x-owner"])
            result = request_from_user(
                universe_id=center,
                payload=json.dumps(
                    {
                        "kind": "API",
                        "title": "Confirm rehearsal model use",
                        "body": "Synthetic source",
                        "fields": [{"name": "secret", "type": "secret", "label": "Key"}],
                        "action": {
                            "type": "connect",
                            "destination": "rehearsal-model",
                            "auth_scheme": "bearer",
                            "host": "model.rehearsal.invalid",
                            "path_template": "/v1/chat/completions",
                            "methods": ["POST"],
                            "uses": {
                                "model": {
                                    "wire": "openai_chat",
                                    "billing": "free",
                                    "models": [
                                        {"id": "rehearsal-model", "tools": True, "context": 8192}
                                    ],
                                }
                            },
                        },
                    }
                ),
            )
            return JSONResponse({"center": center, "request": result})
        if doc["action"] == "model_status":
            from tinyassets.api.pending_requests import _serving_llm_bound

            return JSONResponse(
                {
                    "connected": _serving_llm_bound(
                        Path("/home/owner"), doc["center"], request.headers["x-owner"]
                    )
                }
            )
        if doc["action"] == "write":
            record_turn(
                Path("/home/owner") / doc["center"],
                "principal:" + request.headers["x-owner"],
                "founder",
                "rehearsal write",
            )
            return JSONResponse({"written": 1})
        result = json.loads(_action_control_daemon(universe_id=doc["center"], text=doc["action"]))
        return JSONResponse({"control": result, "plan": remote("plan")})

    app.routes.insert(0, Route("/app/rehearsal-probe", probe, methods=["POST"]))
    serve(app, Path(socket))


def broker(directory, *, register=False):
    from tinyassets.broker.fence import Fence
    from tinyassets.broker.ops import OpStore
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.owner_reads import Plans
    from tinyassets.broker.process import _Dispatchers
    from tinyassets.broker.server import BOX_HOST, BrokerServer

    run = Path(directory)
    os.setgroups([1102])
    os.setgid(1002)
    os.setuid(1002)
    registration = None
    if register:
        from tinyassets.boxes.bootstrap import register_broker

        registration = register_broker("r" * 64)
    dispatch = _Dispatchers(Path("/data"), allow_test_fixtures=False)
    identities = OwnerIdentities(
        Path("/data/.broker/state/owner-identities.db"), plans=Plans("/data")
    )
    server = BrokerServer(
        ledger_for=dispatch.ledger_for,
        dispatch_for=dispatch.dispatch_for,
        ops=OpStore(run / "ops.db"),
        fence=Fence(
            run / "fence.json",
            verify_lease_proof=lambda _, proof: proof == "synthetic-rehearsal-proof" * 2,
            lease_sha256=hashlib.sha256(("synthetic-rehearsal-proof" * 2).encode()).hexdigest(),
        ),
        roles={0: BOX_HOST, 1001: "owner"},
        owner_identities=identities,
        data_root=Path("/data"),
    )

    async def serving():
        listener = await server.serve(run / "broker.sock")
        (run / "broker.sock").chmod(0o660 if register else 0o666)
        async with listener:
            if registration:
                from tinyassets.boxes.control_channel import receive, send

                channel, identity = registration
                send(channel, {"ready": True})
                assert receive(channel, identity) == {"ready": True}
            try:
                await listener.serve_forever()
            finally:
                if registration:
                    registration[0].close()

    asyncio.run(serving())


def door(directory, owner_names):
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Route

    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.broker.supervisor import BrokerSupervisor
    from tinyassets.onboarding.model_connect import handle_model_connect

    run = Path(directory)
    supervisor = BrokerSupervisor.from_boxhost(
        Path("/data"), socket_path=run / "broker/broker.sock", proof="synthetic-rehearsal-proof" * 2
    )
    supervisor.start()
    owners = set(json.loads(owner_names))

    async def connect(request):
        name = request.headers["x-rehearsal-owner"]
        if name not in owners:
            raise PermissionError("unadmitted rehearsal identity")
        with identity_context(
            Identity(user_id=name, username=name, capabilities=["tinyassets.universe.write"])
        ):
            return await handle_model_connect(request)

    app = Starlette(routes=[Route("/app/model-connect/{operation}", connect, methods=["POST"])])
    uvicorn.run(app, uds=str(run / "door.sock"), log_level="error")


def smoke(confirm=False):
    import httpx
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.boxes.admission import OwnerRegistry
    from tinyassets.boxes.owner_daemons import OwnerDaemons
    from tinyassets.boxes.relay import OwnerRelays
    from tinyassets.owner_router import BoxRoute, OwnerRouter

    os.environ["TINYASSETS_DATA_DIR"] = "/data"
    os.environ["TINYASSETS_ONBOARDING_APP"] = "1"
    os.environ["UNIVERSE_SERVER_URL"] = "https://rehearsal.invalid/mcp"
    journal = json.loads(Path("/data/.owner-cutover.json").read_text())
    counts = dict.fromkeys(
        ("owners", "centers", "history", "history_turns", "pending", "pending_saved",
         "controls", "plans", "writes", "reconnect", "production_launch"), 0
    )
    if not confirm:
        from rehearsal_launch import production_launch

        production_launch(journal["plan"][0]["principal"])
        counts["production_launch"] = 1
    Path("/home/owner").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="/tmp/s6-") as directory:
        run = Path(directory)
        run.chmod(0o755)
        os.environ["TINYASSETS_BOXES_RUN"] = str(run)
        state = run / "broker"
        state.mkdir()
        os.chown(state, 1002, 1002)
        logs, children = [], []

        class Runtime:
            def __init__(self):
                self.processes = {}
                self.mounts = {}

            def start(self, uid, gid, *, relay, app_dir, secret_file, memory_bytes,
                      volume_mount, volume_generation, centers):
                log = tempfile.TemporaryFile()
                logs.append(log)
                self.mounts[uid] = volume_mount["source"]
                process = subprocess.Popen(
                    [
                        "unshare",
                        "--mount",
                        "--net",
                        "--propagation",
                        "private",
                        sys.executable,
                        __file__,
                        "owner",
                        volume_mount["source"],
                        str(relay),
                        str(secret_file),
                        str(app_dir / "app.sock"),
                        str(uid), str(volume_generation), json.dumps(centers),
                        json.dumps(list(self.mounts.values())),
                    ],
                    stdout=log,
                    stderr=log,
                )
                self.processes[uid] = process
                children.append(process)

            def alive(self, uid):
                return uid in self.processes and self.processes[uid].poll() is None

            def stop(self, uid):
                if self.alive(uid):
                    self.processes[uid].kill()
                    self.processes[uid].wait(20)
                self.mounts.pop(uid, None)

            def shutdown(self, uid):
                if self.alive(uid):
                    self.processes[uid].terminate()
                    if self.processes[uid].wait(30) != 0:
                        raise RuntimeError("owner did not finish graceful shutdown")
                self.mounts.pop(uid, None)

        runtime = Runtime()
        log = tempfile.TemporaryFile()
        logs.append(log)
        process = subprocess.Popen(
            [sys.executable, __file__, "broker", str(state)], stdout=log, stderr=log
        )
        children.append(process)
        deadline = time.monotonic() + 30
        while not (state / "broker.sock").exists() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("broker failed")
            time.sleep(0.05)
        admission = OwnerRegistry(Path("/data"), state / "broker.sock")
        relays = OwnerRelays(run / "relay", admission)

        from tinyassets.boxes.volumes import OwnerVolumes

        volumes = OwnerVolumes(Path('/box-store'), run / 'mounts',
                               ':local:/snapshots/owner-snapshots')

        (run / "apps").mkdir(mode=0o755)
        manager = OwnerDaemons(
            runtime,
            admission,
            volumes,
            relays,
            run / "apps",
            awake_cap=max(1, len(journal["plan"])),
        )

        from tinyassets.boxes.host_service import HostService

        authority = HostService(admission, None, None, None)
        stop = threading.Event()
        erasure = threading.Thread(target=authority.serve,
                                   args=(run / "erasure.sock", 1102, authority.erasure, stop))
        erasure.start()
        deadline = time.monotonic() + 5
        while not (run / "erasure.sock").exists():
            if not erasure.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("rehearsal erasure authority failed")
            time.sleep(.01)

        def resolve(name):
            uid = manager.ensure_awake(name)
            return BoxRoute(run / "apps" / str(uid) / "app.sock", manager.boxes[uid].secret)

        from tinyassets.cutover_control import admit
        from tinyassets.router_traffic import admits

        if not confirm:
            admit(Path('/deployment/cutover'), Path('/data'))
        assert admits(Path('/data'), journal['plan'][0]['principal'])
        router = OwnerRouter(Starlette(), resolver=resolve)
        try:
            if confirm:
                # The shared door adopts its real broker peer after retiring all
                # root authority, exactly as the role launcher requires.
                os.chown(run, 1001, 1001)
                log = tempfile.TemporaryFile()
                logs.append(log)
                child = subprocess.Popen(
                    [
                        "setpriv",
                        "--reuid=1001",
                        "--regid=1001",
                        "--groups=1100,1101,1102",
                        "--bounding-set=-all",
                        "--no-new-privs",
                        sys.executable,
                        __file__,
                        "door",
                        str(run),
                        json.dumps([item["principal"] for item in journal["plan"]]),
                    ],
                    stdout=log,
                    stderr=log,
                )
                children.append(child)
                deadline = time.monotonic() + 30
                while not (run / "door.sock").exists() and time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise RuntimeError("shared door failed")
                    time.sleep(0.05)
            with TestClient(router) as client:
                for item in journal["plan"]:
                    name = item["principal"]
                    needs_reconnect = False
                    with identity_context(
                        Identity(
                            user_id=name, username=name, capabilities=["tinyassets.universe.write"]
                        )
                    ):
                        for center in item["centers"]:
                            response = client.post(
                                "/app/api/status",
                                json={"universe_id": center, "include_conversation": True},
                            )
                            if (
                                response.status_code != 200
                                or "recent_conversation" not in response.json()
                                or "error" in response.json()["recent_conversation"]
                            ):
                                raise RuntimeError("history failed")
                            counts["history"] += 1
                            counts["history_turns"] += response.json()["recent_conversation"][
                                "turn_count"
                            ]
                            response = client.post(
                                "/app/api/read",
                                json={"target": "pending_requests", "graph_id": center},
                            )
                            if response.status_code != 200 or "pending" not in response.json():
                                raise RuntimeError("pending failed")
                            counts["pending"] += 1
                            entries = response.json()["pending"]
                            counts["pending_saved"] += sum(
                                not row["request_id"].startswith("sys_") for row in entries
                            )
                            for row in entries:
                                if (
                                    row.get("request_id") == "sys_connect_model"
                                    and row.get("status") == "pending"
                                    and row.get("action", {}).get("use") == "model"
                                ):
                                    needs_reconnect = True
                            for action in ("pause", "resume"):
                                response = client.post(
                                    "/app/rehearsal-probe",
                                    json={"center": center, "action": action},
                                )
                                result = response.json()
                                if (
                                    response.status_code != 200
                                    or "error" in result["control"]
                                    or result["control"]["status"] != (
                                        "signal_written" if action == "pause" else "resumed"
                                    )
                                    or result["plan"]["tier"] not in {"paid", "free"}
                                ):
                                    raise RuntimeError("control failed")
                                counts["controls"] += 1
                                counts["plans"] += 1
                            response = client.post(
                                "/app/rehearsal-probe", json={"center": center, "action": "write"}
                            )
                            if response.status_code != 200 or response.json() != {"written": 1}:
                                raise RuntimeError("write failed")
                            readback = client.post(
                                "/app/api/status",
                                json={"universe_id": center, "include_conversation": True},
                            )
                            if (readback.status_code != 200 or not any(
                                row["text"] == "rehearsal write" for row in readback.json().get(
                                    "recent_conversation", {}).get("turns", [])
                            )):
                                raise RuntimeError("serving write readback failed")
                            counts["writes"] += 1
                            counts["centers"] += 1
                        counts["owners"] += 1
                        if needs_reconnect and confirm:
                            asked = client.post(
                                "/app/rehearsal-probe",
                                json={"center": item["centers"][0], "action": "model_ask"},
                            ).json()
                            center, request = asked["center"], asked["request"]
                            if request.get("status") != "pending" or "error" in request:
                                raise RuntimeError("model use confirmation failed")
                            with httpx.Client(
                                transport=httpx.HTTPTransport(uds=str(run / "door.sock")),
                                # Like the tunnel hop, the local Unix transport
                                # is HTTP; the public Origin remains HTTPS.
                                base_url="http://rehearsal.invalid",
                                headers={
                                    "origin": "https://rehearsal.invalid",
                                    "x-rehearsal-owner": name,
                                },
                            ) as shared:
                                deposited = shared.post(
                                    "/app/model-connect/deposit",
                                    json={
                                        "center": center,
                                        "request_ref": request["request_id"],
                                        "secret": "synthetic-rehearsal-key-never-sent",
                                        "definition": {
                                            "destination": "rehearsal-model",
                                            "auth_scheme": "bearer",
                                            "endpoints": [
                                                {
                                                    "host": "model.rehearsal.invalid",
                                                    "path_template": "/v1/chat/completions",
                                                    "methods": ["POST"],
                                                }
                                            ],
                                        },
                                    },
                                )
                            if deposited.status_code != 200 or "error" in deposited.json():
                                sys.stderr.write(f"door status={deposited.status_code} "
                                                 f"response={deposited.text}\n")
                                raise RuntimeError("shared model connect failed")
                            # This rail read reconciles the explicitly confirmed
                            # request through the real model-use capability writer.
                            reconciled = client.post(
                                "/app/api/read",
                                json={"target": "pending_requests", "graph_id": center},
                            )
                            connected = client.post(
                                "/app/rehearsal-probe",
                                json={"center": center, "action": "model_status"},
                            )
                            if reconciled.status_code != 200 or connected.json() != {
                                "connected": True
                            }:
                                sys.stderr.write(f"reconciliation={reconciled.text}\n"
                                                 f"serving={connected.text}\n")
                                raise RuntimeError("confirmed source did not become serving")
                            counts["reconnect"] += 1
                if not confirm:
                    item = journal['plan'][0]
                    owner_name, center = item['principal'], item['centers'][0]

                    def router_key(action, key=None):
                        code = """
import json, sys
from pathlib import Path
from tinyassets.api_keys import ApiKeyStore
from tinyassets.broker.supervisor import BrokerSupervisor
supervisor = BrokerSupervisor.from_boxhost(
    Path('/data'), socket_path=Path(sys.argv[1]), proof='synthetic-rehearsal-proof' * 2)
supervisor.start()
action, owner, key = json.load(sys.stdin)
keys = ApiKeyStore('/data')
if action == 'mint':
    key = keys.mint(owner, name='synthetic restore proof', scopes=[], authority=None)
    assert keys.resolve(key['key']) is not None
else:
    if action == 'revoke':
        keys.revoke(owner, key['key_id'], authority=None)
    assert keys.resolve(key['key']) is None
print(json.dumps(key))
"""
                        result = subprocess.run(
                            ['setpriv', '--reuid=1001', '--regid=1001',
                             '--groups=1100,1101,1102', '--bounding-set=-all', '--no-new-privs',
                             sys.executable, '-c', code, str(run / 'broker/broker.sock')],
                            input=json.dumps([action, owner_name, key]),
                            capture_output=True, text=True)
                        assert result.returncode == 0, result.stderr
                        return json.loads(result.stdout)

                    key = router_key('mint')
                    with identity_context(Identity(user_id=owner_name, username=owner_name,
                                                   capabilities=['tinyassets.universe.write'])):
                        def proof(action, **values):
                            answer = client.post('/app/rehearsal-probe', json={
                                'center': center, 'action': action, **values})
                            assert answer.status_code == 200, answer.text
                            return answer.json()

                        operations = proof('restore_prepare')
                        uid = admission.owner(owner_name)['uid']
                        assert manager._drain(manager.boxes[uid], idle=0, stop=True)
                        snapshot = admission.volume(owner_name)['snapshot_id']
                        proof('restore_mutate', operations=operations)
                        router_key('revoke', key)
                        assert manager._drain(manager.boxes[uid], idle=0, stop=True)
                        manager.storage.restore(owner_name, snapshot, lambda: runtime.stop(uid))
                        restored = proof('restore_verify', operations=operations)
                        router_key('refused', key)
                        counts['restore_replay_refusals'] = restored['replay_refusals']
                        counts['restore_new_operations'] = restored['new_operations']
                        counts['restore_revoked_keys'] = 1
        except BaseException:
            # Captured by the parent harness, never included in its report.
            # Synthetic tests retain this evidence when a child fails startup.
            for stream in logs:
                stream.seek(0)
                sys.stderr.write(stream.read().decode(errors="replace"))
            raise
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(20)
            for uid in runtime.processes:
                volumes.detach(uid)
            relays.close()
            stop.set()
            erasure.join(5)
            for stream in logs:
                stream.close()
        if not confirm:
            # Exercise reconnect on a disposable shared-store clone. The separate
            # platform recovery proof invalidates the original shared credentials;
            # owner writes remain on their opaque volumes.
            with tempfile.TemporaryDirectory(prefix="/tmp/s6-connect-") as clone:
                subprocess.run(["cp", "-a", "/data", clone + "/data"], check=True)
                result = subprocess.run(
                    [
                        "unshare",
                        "--mount",
                        "--propagation",
                        "private",
                        sys.executable,
                        __file__,
                        "reconnect",
                        clone,
                    ],
                    capture_output=True,
                    text=True,
                )
                if result.returncode:
                    sys.stderr.write(result.stderr)
                    raise RuntimeError("reconnect failed")
                counts["reconnect"] = json.loads(result.stdout)["reconnect"]
        print(json.dumps(counts))



def host_startup():
    """Production listeners with current opaque volume admission and pinned runsc."""
    from rehearsal_launch import production_host

    runtime = Path('/run/tinyassets-boxhostd')
    state = runtime / 'broker'
    with tempfile.TemporaryFile() as output, production_host(output):
        process = subprocess.Popen([sys.executable, __file__, 'registered-broker', str(state)],
                                   stdout=output, stderr=output)
        try:
            owners = [row['principal'] for row in json.loads(
                Path('/data/.owner-cutover.json').read_text())['plan']]
            code = """
import json, sys, time
from tinyassets.boxes.daemon_control import request
for owner in json.loads(sys.argv[1]):
    deadline = time.monotonic() + 30
    while True:
        try:
            assert request('status', owner)['uid'] >= 300001
            break
        except (RuntimeError, OSError):
            if time.monotonic() > deadline: raise
            time.sleep(.05)
"""
            result = subprocess.run([sys.executable, '-c', code, json.dumps(owners)],
                                    user=1001, group=1001, extra_groups=[1102],
                                    capture_output=True, text=True, timeout=45)
            if result.returncode:
                output.seek(0)
                raise RuntimeError('host startup failed: ' + result.stderr +
                                   output.read().decode(errors='replace'))
            print(json.dumps({'owners': len(owners)}))
        finally:
            process.terminate()
            process.wait(15)


def reconnect(clone):
    subprocess.run(["mount", "--bind", clone + "/data", "/data"], check=True)
    smoke(confirm=True)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        {"owner": owner, "broker": broker, "door": door, "reconnect": reconnect,
         "registered-broker": lambda directory: broker(directory, register=True),
         "host-startup": host_startup}[sys.argv[1]](
            *sys.argv[2:]
        )
    else:
        smoke()
