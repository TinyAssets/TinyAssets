"""C5/C6 through the real router, broker socket, owner processes and job records."""
# ruff: noqa: F811
import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from tests.support.admission import complete_deletion, drive, projection, suspend
from tests.test_owner_router import (  # noqa: F401
    admitted,
    boxes,
    broker,
    cloud_runtime,
    initialized_ledger,
    router,
)
from tinyassets.auth.provider import Identity
from tinyassets.broker.admission import digest, read
from tinyassets.broker.deletion_jobs import STEPS, DeletionJobs, load_unfinished_jobs, request
from tinyassets.broker.deletion_records import RemoteRecords


class Remote:
    """Fake remote storage; production rclone commands still run through dispatch."""
    def __init__(self, root):
        root.mkdir(exist_ok=True)
        self.transport = RemoteRecords(":local:" + str(root))
        self.transport._run = self.transfer

    @staticmethod
    def transfer(operation, destination, *flags, input=None):
        path = Path(destination.removeprefix(":local:"))
        if operation == "cat":
            return path.read_bytes()
        if operation == "rcat":
            path.parent.mkdir(exist_ok=True)
            with path.open("wb") as stream:
                stream.write(input)
                stream.flush()
                os.fsync(stream.fileno())
            return b""
        if operation == "lsjson":
            assert flags == ("--recursive", "--files-only", "--include", "/deletion-jobs/*.json")
            return json.dumps([{"Path": str(file.relative_to(path))}
                               for file in path.glob("deletion-jobs/*.json")]).encode()
        assert operation == "delete" and flags[0] == "--include"
        for file in path.glob(flags[1].strip("/").replace("/**", "/*")):
            file.unlink()
        return b""

    def load(self, key):
        return self.transport.execute("read", {"key": key})

    def save(self, job):
        self.transport.execute("write", {"key": job["id"], "record": job})

    def records(self):
        return [self.load(key) for key in self.transport.execute("list", {})]


@pytest.fixture
def jobs(boxes, broker, admitted, tmp_path, monkeypatch):  # noqa: F811
    from tinyassets.boxes import daemon_control
    from tinyassets.broker.policy import PolicyStore

    remote = Remote(tmp_path / "off-host")
    server = broker.server
    server._policy_store = PolicyStore(tmp_path, admitted)
    server._policy_store.bootstrap()
    service = DeletionJobs(server, records=remote)
    server._deletion_jobs = service
    original = daemon_control.request

    def control(operation, owner=None, **fields):
        if operation == "delete_snapshots":
            remote.transfer("delete", remote.transport.remote,
                            "--include", f"/{fields['uid']}/**")
            return {"deleted": True}
        return original(operation, owner, **fields)

    monkeypatch.setattr(daemon_control, "request", control)
    # Every external API in this suite must be supplied explicitly by a test.
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("real API call"))
    return SimpleNamespace(service=service, remote=remote, server=server)


def test_202_status_survives_revoked_credentials_and_restart(router, boxes, admitted, jobs,
                                                           tmp_path, monkeypatch):
    from tinyassets.onboarding import app_config

    destroy = jobs.service._volume
    monkeypatch.setattr(jobs.service, "_volume",
                        lambda job: (_ for _ in ()).throw(OSError("offline")))
    resource = urlsplit(app_config()["resource"])
    headers = {"authorization": "Bearer alice", "origin": resource.scheme + "://" + resource.netloc}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(router),
                                     base_url="http://test") as client:
            for body in ({}, {"confirm": " DELETE "}):
                assert (await client.post("/app/account/delete", headers=headers,
                                          json=body)).status_code == 400
            wrong = {**headers, "origin": "https://other.invalid"}
            assert (await client.post("/app/account/delete", headers=wrong,
                                      json={"confirm": "DELETE"})).status_code == 403
            response = await client.post("/app/account/delete", headers=headers,
                                         json={"confirm": "DELETE", "owner": "bob"})
            assert response.status_code == 202, response.text
            assert read(admitted, "alice").state == "erased"
            assert read(admitted, "bob").state == "admitted"
            assert not boxes.runtime.alive(admitted.owner_machine("alice"))
            assert boxes.runtime.alive(admitted.owner_machine("bob"))
            url = response.json()["status_url"]
            assert response.headers["location"] == url
            pending = (await client.get(url + "?format=json")).json()
            assert pending["completed_steps"] == ["withdrawal"]
            assert "Deletion in progress" in (await client.get(url)).text
            monkeypatch.setattr(jobs.service, "_volume", destroy)
            restarted = DeletionJobs(jobs.server, records=Remote(tmp_path / "off-host"))
            loaded, = load_unfinished_jobs(restarted.records)
            await asyncio.to_thread(drive, restarted.resume_loaded_job(loaded))
            result = await client.get(url + "?format=json")
            assert result.status_code == 200, result.text
            assert result.json()["status"] == "deleted"
            assert result.json()["completed_steps"] == list(STEPS)
            wrong_url = url[:-1] + ("A" if url[-1] != "A" else "B")
            assert (await client.get(wrong_url)).status_code == 503
            assert (await client.post("/app/account/delete", headers=headers,
                                      json={"confirm": "DELETE"})).status_code == 401
            assert not boxes.roots["alice"].exists()
            assert boxes.roots["bob"].exists()
            assert load_unfinished_jobs(restarted.records) == []

    asyncio.run(exercise())


@pytest.mark.parametrize("step", STEPS)
def test_each_effect_replays_after_crash_before_offhost_completion(jobs, admitted,
                                                                 tmp_path, monkeypatch, step):
    service = jobs.service
    original_save = jobs.remote.save
    crashed = []

    class Crash(BaseException):
        pass

    def save(job):
        if step in job["completed_steps"] and not crashed:
            crashed.append(True)
            raise Crash()
        original_save(job)

    monkeypatch.setattr(jobs.remote, "save", save)
    with pytest.raises(Crash):
        complete_deletion(service, "alice")
        drive(service.resume_loaded_job(jobs.remote.load(digest("alice"))))
    assert crashed
    restarted = DeletionJobs(jobs.server, records=Remote(tmp_path / "off-host"))
    result = drive(restarted.resume_loaded_job(restarted.records.load(digest("alice"))))
    assert result["status"] == "deleted"
    assert result["pending_steps"] == []
    assert read(admitted, "bob").state == "admitted"


def test_offhost_failure_never_withdraws_admission(jobs, admitted, monkeypatch):
    def unavailable(job):
        raise OSError("remote unavailable")

    monkeypatch.setattr(jobs.remote, "save", unavailable)
    with pytest.raises(OSError):
        complete_deletion(jobs.service, "alice")
    assert read(admitted, "alice").state == "admitted"


@pytest.mark.parametrize("step", STEPS)
def test_failure_records_retry_and_never_runs_a_later_step(jobs, tmp_path, monkeypatch, step):
    original = getattr(jobs.service, "_" + step)
    def fail(job):
        raise OSError("injected step failure")

    monkeypatch.setattr(jobs.service, "_" + step, fail)
    with pytest.raises((OSError, RuntimeError)):
        complete_deletion(jobs.service, "alice")
        drive(jobs.service.resume_loaded_job(jobs.remote.load(digest("alice"))))
    job = jobs.remote.load(digest("alice"))
    assert job["completed_steps"] == list(STEPS[:STEPS.index(step)])
    assert job["failed_step"] == step
    assert job["retry_at"] > 0
    monkeypatch.setattr(jobs.service, "_" + step, original)
    assert drive(jobs.service.resume_loaded_job(job))["status"] == "deleted"


def test_broker_request_cannot_override_job_box(jobs, admitted, tmp_path):
    with pytest.raises(RuntimeError):
        request(tmp_path, "enqueue", owner="alice", uid=admitted.owner_machine("bob"))
    assert read(admitted, "alice").state == "admitted"


def test_remote_records_and_namespace_erasure_are_idempotent(tmp_path):
    remote = Remote(tmp_path / "remote")
    job = {"id": "a" * 64, "completed_steps": []}
    remote.save(job)
    remote.save(job)
    assert load_unfinished_jobs(remote) == [job]
    for uid in (300001, 300002):
        directory = tmp_path / "remote" / str(uid)
        directory.mkdir()
        (directory / "snapshot.img.gz").write_bytes(b"synthetic opaque snapshot")
    remote.transfer("delete", remote.transport.remote, "--include", "/300001/**")
    remote.transfer("delete", remote.transport.remote, "--include", "/300001/**")
    assert not list((tmp_path / "remote/300001").glob("*"))
    assert (tmp_path / "remote/300002/snapshot.img.gz").read_bytes()
    assert json.loads((tmp_path / "remote/deletion-jobs" / ("a" * 64 + ".json")).read_text()) == job


def test_door_deletion_removes_only_bound_owners_data_and_router_state(
    router, boxes, admitted, jobs, tmp_path, monkeypatch,
):
    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.connection_oauth.pkce import flows_db
    from tinyassets.onboarding import (
        app_config,
        approval_handoff,
        canary_sign_in,
        inline_model_connect,
        owner_sessions,
        session_store,
    )

    # Probe ingress after its secret is revoked but before the real process stops.
    stop = boxes.runtime.stop
    rejected = []

    def checked_stop(uid):
        if uid == admitted.owner_machine("alice") and boxes.runtime.alive(uid):
            path = boxes.run / str(uid) / "app.sock"
            with httpx.Client(transport=httpx.HTTPTransport(uds=str(path)),
                              base_url="http://owner") as client:
                response = client.get("/_box/status", headers={
                    "x-box-secret": boxes.secrets["alice"]})
                rejected.append(response.status_code)
                assert response.status_code == 401
        return stop(uid)

    monkeypatch.setattr(boxes.runtime, "stop", checked_stop)
    admitted.admission("admit", "alice", "cc-alice-extra",
        routing=projection(admitted.path))
    monkeypatch.setenv("TINYASSETS_INBOUND_ENABLED", "1")
    monkeypatch.setenv(canary_sign_in.USER_ENV, "alice")
    with canary_sign_in._limits() as db:
        db.execute("INSERT INTO canary_door_limits VALUES ('ip',1,?)", (time.time() + 600,))
    refresh_handles = {}
    for owner in ("alice", "bob"):
        first = session_store.mint(owner + "-refresh", owner=owner)
        second = session_store.rotate(first, owner + "-rotated")
        third = session_store.mint(owner + "-other-device", owner=owner)
        refresh_handles[owner] = (first, second, third)
    for owner in ("alice", "bob"):
        with owner_sessions.store() as db:
            db.execute("INSERT INTO owner_sessions VALUES (?,?,?)", (
                owner_sessions.hashed(owner + "-cookie"),
                json.dumps(Identity(user_id=owner, username=owner).to_dict()), time.time() + 600))
        with approval_handoff.flows() as db:
            db.execute("INSERT INTO approval_handoffs VALUES (?,?,?,?,?)", (
                owner_sessions.hashed(owner[0] * 43), owner, "request", "web", time.time() + 600))
        with inline_model_connect.flows() as db:
            db.execute("INSERT INTO inline_model_flows VALUES (?,?,?,?,?,?,?)", (
                owner, owner, "home", b"secret", "browser", "ready", time.time() + 600))
        with flows_db(tmp_path) as (db, now):
            db.execute("INSERT INTO hosted_model_flows "
                       "(handle_digest,owner_user_id,bound_home_id,preset_id,preset_digest,"
                       "challenge,callback_origin,created_at,expires_at) "
                       "VALUES (?,?,?,?,?,?,?,?,?)", (
                owner, owner, "home", "preset", "digest", "challenge", "origin", now, now + 500))
            db.execute("INSERT INTO connection_oauth_flows "
                       "(handle_digest,owner_user_id,universe_id,request_id,action_digest,challenge,"
                       "client_id,redirect_uri,created_at,expires_at,approved_owner) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                owner, owner, "home", "request", "digest", "challenge", "client", "redirect",
                now, now + 500, owner))
    # Multiple live keys must all be revoked, including keys beyond the fixture key.
    extra = ApiKeyStore(tmp_path).mint("alice", name="extra", scopes=[], authority=None)
    resource = urlsplit(app_config()["resource"])
    origin = resource.scheme + "://" + resource.netloc

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=router),
                                     base_url="http://test") as client:
            tokens = {}
            for owner in ("alice", "bob"):
                minted = await client.post("/app/test-credentials", headers={
                    "authorization": "Bearer " + owner}, json={"operation": "hook_mint"})
                assert minted.status_code == 200, minted.text
                tokens[owner] = minted.json()["token"]
                assert (await client.post("/hooks/inbound/" + tokens[owner])).status_code == 202
                assert (await client.post("/app/test-session", headers={
                    "cookie": owner_sessions.COOKIE + "=" + owner + "-cookie",
                    "origin": origin}, json={})).status_code == 200
            revoked = await client.post("/app/account/delete", headers={
                "authorization": "Bearer alice", "origin": origin}, json={"confirm": "DELETE"})
            assert revoked.status_code == 202, revoked.text
            await asyncio.to_thread(jobs.service.resume_loaded_job,
                                    jobs.remote.load(digest("alice")))
            assert (await client.get(revoked.json()["status_url"] + "?format=json")).json()[
                "status"] == "deleted"
            assert read(admitted, "alice").state == "erased"
            assert not (boxes.roots["alice"] / "cc-alice").exists()
            assert not boxes.runtime.alive(admitted.owner_machine("alice"))
            assert boxes.runtime.alive(admitted.owner_machine("bob"))
            # C6 erases the owner record; center history is not an authority.
            assert admitted.center_state("cc-alice") == "admitted"
            assert admitted.center_state("cc-alice-extra") == "admitted"
            assert rejected == [401]
            with pytest.raises(LookupError):
                admitted.box_route("alice")
            for owner in ("alice", "bob"):
                session = await client.post("/app/test-session", headers={
                    "cookie": owner_sessions.COOKIE + "=" + owner + "-cookie",
                    "origin": origin}, json={})
                assert session.status_code == (401 if owner == "alice" else 200)
                api = await client.get("/api/command-centers/cc-" + owner + "/status", headers={
                    "authorization": "Bearer " + boxes.keys[owner]})
                assert api.status_code == (401 if owner == "alice" else 200)
                hook = await client.post("/hooks/inbound/" + tokens[owner])
                assert hook.status_code == (404 if owner == "alice" else 202)
            assert (await client.get("/api/command-centers/cc-alice/status", headers={
                "authorization": "Bearer " + extra["key"]})).status_code == 401

    asyncio.run(exercise())
    for owner, handles in refresh_handles.items():
        for handle in handles:
            assert bool(session_store.read(handle)[0]) == (owner == "bob")
    from tests.test_onboarding_session_refresh import _boom, _drive

    for handle in refresh_handles["alice"]:
        status, body, _, calls = _drive(
            {"grant_type": "refresh_token", "session_ref": handle},
            monkeypatch=monkeypatch, upstream=_boom, data_dir=str(tmp_path))
        assert status == 401 and body["error"] == "no_refresh_token"
        assert not calls
    with canary_sign_in._limits() as db:
        assert db.execute("SELECT COUNT(*) FROM canary_door_limits").fetchone()[0] == 0
    assert owner_sessions.lookup("alice-cookie") is None
    assert owner_sessions.lookup("bob-cookie") is not None
    assert approval_handoff.load("a" * 43) is None
    assert approval_handoff.load("b" * 43)["owner"] == "bob"
    with inline_model_connect.flows() as db:
        rows = {row["owner"]: (row["status"], row["sealed"]) for row in
                db.execute("SELECT * FROM inline_model_flows")}
    assert rows == {"alice": ("cancelled", b""), "bob": ("ready", b"secret")}
    with flows_db(tmp_path) as (db, _):
        for table in ("hosted_model_flows", "connection_oauth_flows"):
            assert [r[0] for r in db.execute(f"SELECT owner_user_id FROM {table}")] == ["bob"]
    for root in boxes.roots.values():
        assert not (root / ".runtime" / "owner-sessions.db").exists()


@pytest.mark.parametrize("failure", [None, "stop", "owner_tree", "relay", "socket",
                                     "broker_egress", "billing", "plan", "credentials", "identity"])
def test_paid_deletion_confirms_every_phase_and_retries_after_tombstone(
    router, boxes, admitted, jobs, tmp_path, monkeypatch, failure,
):
    import re
    from contextlib import nullcontext

    from tests.support.admission import Plans
    from tinyassets import router_account_deletion, router_credentials
    from tinyassets.billing import stripe_adapter
    from tinyassets.boxes import destroy
    from tinyassets.broker import account_erasure
    from tinyassets.onboarding import app_config

    plans = Plans(tmp_path)
    plans.set_plan("alice", "paid", event_created=1, customer_id="cus_a", subscription_id="sub_a")
    calls, subscription = [], {"id": "sub_a", "status": "active"}

    def upstream(request, **kwargs):
        if request.full_url == "https://api.stripe.com/v1/subscriptions/sub_a":
            if request.get_method() == "DELETE":
                calls.append("stripe")
                subscription["status"] = "canceled"
            else:
                assert request.get_method() == "GET"
            return nullcontext(SimpleNamespace(
                status=200, read=lambda: json.dumps(subscription).encode()))
        assert request.get_method() == "DELETE"
        assert request.full_url.endswith("/user_management/users/alice")
        calls.append("identity")
        return nullcontext(SimpleNamespace(status=204))

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_deletion")
    monkeypatch.setattr(router_account_deletion, "_WORKOS_USER_ID", re.compile("alice"))
    monkeypatch.setattr(router_account_deletion.urllib.request, "urlopen", upstream)
    targets = {
        "stop": (boxes.runtime, "stop"),
        "owner_tree": (boxes.manager.volumes, "delete"),
        "relay": (boxes.relays, "retire"),
        "socket": (destroy, "socket_tree"),
        "broker_egress": (account_erasure, "local_erase"),
        "billing": (stripe_adapter, "_get"),
        "plan": (Plans, "apply_tier_event"),
        "credentials": (router_credentials, "revoke_account"),
        "identity": (router_account_deletion, "delete_workos_user"),
    }
    attempts = []
    if failure:
        target, name = targets[failure]
        original = getattr(target, name)

        def fail_once(*args, **kwargs):
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError("injected phase failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(target, name, fail_once)
    artifact = boxes.roots["alice"] / ".owner-boxd" / "execs" / "private-output"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("must disappear with the volume")
    uid = admitted.owner_machine("alice")
    resource = urlsplit(app_config()["resource"])

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(router),
                                     base_url="http://test") as client:
            async def delete():
                return await client.post("/app/account/delete", headers={
                    "authorization": "Bearer alice",
                    "origin": resource.scheme + "://" + resource.netloc},
                    json={"confirm": "DELETE"})

            first = await delete()
            if failure == "stop":
                assert first.status_code == 503, first.text
                assert first.headers["location"] == first.json()["status_url"]
                progress = await client.get(first.json()["status_url"] + "?format=json")
                assert progress.json()["failed_step"] == "withdrawal"
            else:
                assert first.status_code == 202, first.text
            if failure:
                job = jobs.remote.load(digest("alice"))
                expected = {"stop": "withdrawal", "owner_tree": "volume", "relay": "volume",
                            "socket": "volume", "broker_egress": "custody", "plan": "billing"
                            }.get(failure, failure)
                assert job["failed_step"] == expected
                assert read(admitted, "alice").state == "erased"
                assert "identity" not in calls
                assert (await delete()).status_code == 401
            result = await asyncio.to_thread(jobs.service.resume_loaded_job,
                                             jobs.remote.load(digest("alice")))
            assert result["status"] == "deleted"
            job = jobs.remote.load(digest("alice"))
            assert job["results"]["billing"] == "cancelled"
            assert job["results"]["identity"] == "deleted"
            assert job["completed_steps"] == list(STEPS)
            assert (await delete()).status_code == 401

    asyncio.run(exercise())
    assert sorted(calls) == ["identity", "stripe"]
    assert calls[-1] == "identity"
    assert plans.read("alice")["tier"] == "free"
    assert not boxes.roots["alice"].exists()
    assert boxes.roots["bob"].exists()
    assert not boxes.runtime.alive(uid)
    assert not (boxes.run / str(uid)).exists()
    assert not (boxes.run / "relay" / f"{uid}.sock").exists()
    assert not (boxes.run / "secrets" / str(uid)).exists()
    assert jobs.remote.load(digest("alice"))["completed_steps"] == list(STEPS)


@pytest.mark.parametrize("asleep", [False, True])
def test_suspended_owner_deletion_sweeps_real_box_and_expires_checkout(
    router, boxes, admitted, jobs, tmp_path, monkeypatch, asleep,
):
    import sqlite3

    from tests.support.admission import Plans
    from tests.test_cutover_billing_deletion import attempt
    from tinyassets import router_account_deletion
    from tinyassets.billing import stripe_adapter
    from tinyassets.onboarding import app_config

    plans = Plans(tmp_path)
    suspend(plans, "alice", reason="billing", suspended=True)
    with sqlite3.connect(plans.path) as db:
        db.execute("UPDATE plans SET attempt=? WHERE owner='alice'", (json.dumps(attempt()),))
    uid = admitted.owner_machine("alice")
    if asleep:
        boxes.manager._stop(boxes.manager.boxes[uid])
    monkeypatch.setattr(router_account_deletion, "delete_workos_user",
                        lambda owner: "not_applicable")
    calls = []
    monkeypatch.setattr(stripe_adapter, "_get", lambda path: {
        "id": "cs_synthetic", "status": "open"})
    monkeypatch.setattr(stripe_adapter, "_post", lambda path, fields: (
        calls.append(path) or {"id": "cs_synthetic", "status": "expired"}))
    resource = urlsplit(app_config()["resource"])

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(router),
                                     base_url="http://test") as client:
            ordinary = await client.get("/app/me", headers={"authorization": "Bearer alice"})
            assert ordinary.status_code == 503
            response = await client.post("/app/account/delete", headers={
                "authorization": "Bearer alice",
                "origin": resource.scheme + "://" + resource.netloc},
                json={"confirm": "DELETE"})
            assert response.status_code == 202, response.text
            await asyncio.to_thread(jobs.service.resume_loaded_job,
                                    jobs.remote.load(digest("alice")))
            assert jobs.remote.load(digest("alice"))["results"]["billing"] == "attempt_expired"
            assert (await client.get(response.json()["status_url"] + "?format=json")).json()[
                "pending_steps"] == []

    asyncio.run(exercise())
    assert calls == ["checkout/sessions/cs_synthetic/expire"]
    assert jobs.remote.load(digest("alice"))["completed_steps"] == list(STEPS)
    assert not boxes.roots["alice"].exists()
    assert boxes.runtime.alive(admitted.owner_machine("bob"))


@pytest.mark.parametrize("timeout", [False, True])
def test_deletion_drains_real_turn_before_sweep_and_destroys_late_writes(
    router, boxes, admitted, jobs, tmp_path, timeout,
):
    from tinyassets.onboarding import app_config

    root = boxes.roots["alice"]
    uid = admitted.owner_machine("alice")
    resource = urlsplit(app_config()["resource"])
    headers = {"authorization": "Bearer alice", "origin": resource.scheme + "://" + resource.netloc}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(router),
                                     base_url="http://test") as client:
            assert (await client.post("/app/test-writing-turn", headers=headers)).status_code == 200
            deleting = asyncio.create_task(client.post("/app/account/delete", headers=headers,
                                                        json={"confirm": "DELETE"}))
            try:
                async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(
                        uds=str(boxes.run / str(uid) / "app.sock")),
                        base_url="http://owner") as box:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        status = await box.get("/_box/status", headers={
                            "x-box-secret": boxes.secrets["alice"]})
                        # The triggering HTTP response can arrive before its
                        # activity context exits. Wait for only the held turn.
                        if status.json()["draining"] and status.json()["active"] == 1:
                            break
                        await asyncio.sleep(.01)
                    assert status.json()["draining"] and status.json()["active"] == 1
                refused = await client.post("/app/api/file", headers=headers,
                                             json={"graph_id": "cc-alice", "path": "hello.txt"})
                assert refused.status_code == 401
                assert not deleting.done() and root.exists()
                if timeout:
                    result = await asyncio.wait_for(deleting, 25)
                    assert result.status_code == 503
                    assert jobs.remote.load(digest("alice"))["failed_step"] == "withdrawal"
                    assert root.exists() and boxes.runtime.alive(uid)
                (root / "release-turn").touch()
                if timeout:
                    await asyncio.to_thread(jobs.service.resume_loaded_job,
                                            jobs.remote.load(digest("alice")))
                    assert jobs.remote.load(digest("alice"))["completed_steps"] == list(STEPS)
                    return
                else:
                    result = await asyncio.wait_for(deleting, 10)
                assert result.status_code == 202, result.text
                await asyncio.to_thread(jobs.service.resume_loaded_job,
                                        jobs.remote.load(digest("alice")))
                assert jobs.remote.load(digest("alice"))["completed_steps"] == list(STEPS)
            finally:
                if root.exists():
                    (root / "release-turn").touch()
                if not deleting.done():
                    await deleting
    asyncio.run(exercise())
    assert not root.exists()
    assert not (root / "late-write").exists()
    assert not boxes.runtime.alive(uid)


def test_worker_restart_retries_from_local_rows_on_existing_waker_tick(jobs, monkeypatch):
    original = jobs.service._volume
    monkeypatch.setattr(jobs.service, "_volume", lambda job: (_ for _ in ()).throw(OSError()))
    complete_deletion(jobs.service, "alice")
    token = jobs.service.records.load(digest("alice"))["status_token"]
    monkeypatch.setattr(jobs.service, "_volume", original)
    restarted = DeletionJobs(jobs.server, records=jobs.remote, clock=lambda: time.time() + 3601)
    monkeypatch.setattr(jobs.remote, "records", lambda: pytest.fail("remote read on restart"))
    monkeypatch.setattr(jobs.remote, "load", lambda key: pytest.fail("remote read on restart"))
    restarted.tick()
    assert restarted.status(digest("alice"), token)["completed_steps"] == list(STEPS)


def test_corrupt_private_metadata_cannot_block_opaque_deletion(jobs, boxes):
    import sqlite3

    from tinyassets.daemon_server import DB_FILENAME

    with sqlite3.connect(boxes.roots["alice"] / DB_FILENAME) as db:
        db.execute("UPDATE founder_home SET universe_id='../escape' WHERE founder_sub='alice'")
    complete_deletion(jobs.service, "alice")
    drive(jobs.service.resume_loaded_job(jobs.remote.load(digest("alice"))))
    assert not boxes.roots["alice"].exists()
    assert boxes.roots["bob"].exists()


def test_session_revocation_failure_retries(jobs, monkeypatch):
    from tinyassets.onboarding import session_store

    handle = session_store.mint("refresh", owner="alice")
    real = session_store.revoke_owner
    with monkeypatch.context() as patch:
        patch.setattr(session_store, "revoke_owner", lambda _: (_ for _ in ()).throw(OSError()))
        complete_deletion(jobs.service, "alice")
    assert jobs.remote.load(digest("alice"))["failed_step"] == "credentials"
    assert session_store.revoke_owner is real
    drive(jobs.service.resume_loaded_job(jobs.remote.load(digest("alice"))))
    assert not session_store.read(handle)[0]


@pytest.mark.parametrize("entry", ["resume", "enqueue"])
def test_historical_platform_backup_resumes_current_offhost_job(jobs, admitted, tmp_path, entry,
                                                               monkeypatch):
    import sqlite3
    from contextlib import closing

    historical = tmp_path / "old-platform.sqlite3"
    with closing(sqlite3.connect(admitted.path)) as source, closing(
            sqlite3.connect(historical)) as backup:
        source.backup(backup)
    with monkeypatch.context() as patch:
        patch.setattr(jobs.service, "_volume", lambda job: (_ for _ in ()).throw(OSError()))
        complete_deletion(jobs.service, "alice")
    # Restore the actual historical shared DB. The newer job exists only remotely.
    with closing(sqlite3.connect(historical)) as backup, closing(
            sqlite3.connect(admitted.path)) as live:
        backup.backup(live)
    assert read(admitted, "alice").state == "admitted"
    restarted = DeletionJobs(jobs.server, records=jobs.remote)
    loaded, = load_unfinished_jobs(jobs.remote)
    assert drive(restarted.resume_loaded_job(loaded))["status"] == "deleted"
    if entry == "enqueue":
        assert complete_deletion(restarted, "alice")["status"] == "deleted"
    assert read(admitted, "alice").state == "erased"
    assert read(admitted, "bob").state == "admitted"


def test_snapshot_cannot_publish_after_erasure(jobs, admitted):
    from tinyassets.broker.owner_volumes import dispatch

    request = {"op": "BOX_VOLUME", "principal": "alice", "action": "sleep", "arguments": {}}
    record = dispatch(admitted, request)
    complete_deletion(jobs.service, "alice")
    with pytest.raises(PermissionError):
        dispatch(admitted, {**request, "action": "publish", "arguments": {
            "snapshot_id": record["work"]["snapshot_id"], "sha256": "a" * 64}})


def test_actual_rclone_local_remote_survives_restart_and_preserves_other_uid(tmp_path):
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "platform").mkdir()
    records = RemoteRecords(":local:" + str(remote))
    job = {"id": "b" * 64, "completed_steps": [], "billing_cleanup": {"sub_x": "cancelled"}}
    assert records.records() == []
    records.save(job)
    records.save(job)
    assert load_unfinished_jobs(RemoteRecords(":local:" + str(remote))) == [job]
    for uid in (300001, 300002):
        (remote / str(uid)).mkdir()
        (remote / str(uid) / ("a" * 32 + ".img.gz")).write_bytes(b"opaque bytes")
    import threading

    from tinyassets.boxes.gvisor_runtime import GvisorRuntime
    from tinyassets.boxes.host_service import HostService
    from tinyassets.boxes.volumes import OwnerVolumes
    # Namespace deletion exercises real rclone without attaching an owner volume.
    volumes = object.__new__(OwnerVolumes)
    volumes.store = volumes.locks = tmp_path
    volumes._held = threading.local()
    volumes.remote = ":local:" + str(remote)
    host = HostService(None, volumes, None, SimpleNamespace(
        runtime=GvisorRuntime, condition=threading.RLock()))
    for _ in range(2):
        assert host.lifecycle({"lifecycle": "delete_snapshots", "uid": 300001,
                               "snapshot_ids": None, "released": []}, 1002) == {
            "deleted": True}
    assert not list((remote / "300001").iterdir())
    assert (remote / "300002" / ("a" * 32 + ".img.gz")).read_bytes() == b"opaque bytes"
    assert records.load(job["id"]) == job


@pytest.mark.parametrize("operation", ["BOX_CHANNEL", "BOX_PUBLIC"])
def test_withdrawal_closes_inflight_open_before_ack(jobs, boxes, monkeypatch, operation):
    import socket
    import threading

    from tinyassets import rpc_frames as rf
    from tinyassets.broker import box_admission, public_egress

    entered, release = threading.Event(), threading.Event()
    module = box_admission if operation == "BOX_CHANNEL" else public_egress
    name = "resolve" if operation == "BOX_CHANNEL" else "resolve_owner"
    original = getattr(module, name)
    def held(*args, **kwargs):
        result = original(*args, **kwargs)
        entered.set()
        assert release.wait(10)
        return result
    monkeypatch.setattr(module, name, held)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(5)
        channel.connect(str(boxes.host_path))
        channel.sendall(rf.control(rf.CONNECTION, {
            "op": operation, "principal": "alice",
            **({"center": "cc-alice"} if operation == "BOX_CHANNEL" else {})}))
        try:
            assert entered.wait(5)
            results = []
            deleting = threading.Thread(
                target=lambda: results.append(complete_deletion(jobs.service, "alice")))
            deleting.start()
            assert read(jobs.server._owner_identities, "alice").state == "admitted"
            assert not results
            release.set()
            assert rf.read_frame_blocking(channel).control()["op"] == "BOX_ADMITTED"
            deleting.join(10)
            assert not deleting.is_alive() and results[0]["status"] == "deleted"
            assert channel.recv(1) == b""
        finally:
            release.set()
        assert channel.recv(1) == b""


def test_withdrawal_closes_existing_channels_and_public_streams_before_ack(jobs, boxes):
    import socket
    from contextlib import ExitStack

    from tinyassets import rpc_frames as rf

    with ExitStack() as stack:
        channels = []
        for operation in ("BOX_CHANNEL", "BOX_PUBLIC"):
            channel = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
            channel.settimeout(5)
            channel.connect(str(boxes.host_path))
            channel.sendall(rf.control(rf.CONNECTION, {
                "op": operation, "principal": "alice",
            **({"center": "cc-alice"} if operation == "BOX_CHANNEL" else {})}))
            assert rf.read_frame_blocking(channel).control()["op"] == "BOX_ADMITTED"
            channels.append(channel)
        complete_deletion(jobs.service, "alice")
        assert all(channel.recv(1) == b"" for channel in channels)


def test_queued_wake_serializes_before_withdrawal(jobs, boxes, admitted, monkeypatch):
    import threading
    manager = boxes.manager
    alice = admitted.owner_machine("alice")
    with manager.condition:
        manager._stop(manager.boxes[alice])
    manager.awake_cap = 1
    manager.wake_timeout = .2
    monkeypatch.setattr(manager, "_sleep", lambda *a, **kw: False)
    errors, results = [], []
    def wake():
        try:
            results.append(drive(jobs.server.routing.wake("alice")))
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=wake)
    thread.start()
    deadline = time.monotonic() + 5
    while not manager.waiting and time.monotonic() < deadline:
        time.sleep(.01)
    assert manager.waiting
    complete_deletion(jobs.service, "alice")
    thread.join(5)
    assert not thread.is_alive() and errors and not results
    assert read(admitted, "alice").state == "erased"
    assert not boxes.runtime.alive(alice)
    with pytest.raises(PermissionError):
        drive(jobs.server.routing.wake("alice"))


def test_custody_step_erases_shared_secrets_without_owner_content_access(jobs, boxes, tmp_path,
                                                                       monkeypatch):
    from contextlib import closing

    from tinyassets.broker.vault import metadata_connection

    vaults = {}
    with closing(metadata_connection(tmp_path)) as db:
        for owner in ("alice", "bob"):
            center = "cc-" + owner
            db.execute("INSERT INTO llm_credential_deposit_owners VALUES (?,?,?)",
                       (center, "http:synthetic", owner))
            vault = tmp_path / ".platform/credential-vaults" / center / ".credential-vault.json"
            vault.parent.mkdir(parents=True, mode=0o700)
            vault.parent.parent.chmod(0o700)
            vault.write_text("synthetic secret " + owner)
            vaults[owner] = vault
    complete_deletion(jobs.service, "alice")
    for method in ("open", "stat", "lstat", "iterdir"):
        original = getattr(Path, method)
        def checked(path, *args, original=original, **kwargs):
            assert not any(path.is_relative_to(root) for root in boxes.roots.values()), path
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, method, checked)
    jobs.service._custody({"owner": "alice"})
    assert not vaults["alice"].exists()
    assert vaults["bob"].read_text() == "synthetic secret bob"


@pytest.mark.parametrize("paid", [False, True])
def test_full_job_including_custody_mutates_admission_exactly_once(jobs, monkeypatch, paid):
    from tinyassets.broker import admission
    if paid:
        jobs.server._owner_identities.plans.set_plan(
            "alice", "paid", event_created=1, customer_id="cus_synthetic",
            subscription_id="sub_synthetic")
        monkeypatch.setattr("tinyassets.billing.stripe_adapter._get", lambda path: {
            "id": "sub_synthetic", "status": "canceled"})
    original = admission.mutate
    mutations = []
    def mutate(db, owner, action, **fields):
        mutations.append((owner, action))
        return original(db, owner, action, **fields)
    monkeypatch.setattr(admission, "mutate", mutate)
    assert complete_deletion(jobs.service, "alice")["status"] == "deleted"
    assert mutations == [("alice", "erase")]
    assert complete_deletion(jobs.service, "alice")["status"] == "deleted"
    assert mutations == [("alice", "erase")]
