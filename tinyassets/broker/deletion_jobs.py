"""C3/C14: local broker jobs, with every change written through off-host.

No owner-volume paths enter this module. The host performs opaque volume
operations; only this job decides the next step.
"""
from __future__ import annotations

import hmac
import inspect
import json
import logging
import os
import secrets
import threading
import time
from contextlib import closing

from tinyassets.broker.admission import digest, owner_call, read, thread_work
from tinyassets.broker.deletion_records import RemoteRecords

STEPS = ("withdrawal", "volume", "snapshots", "custody", "billing",
         "credentials", "identity", "receipt")
log = logging.getLogger(__name__)


class WithdrawalPending(RuntimeError):
    """Durable job exists, but closure has not been acknowledged."""
    def __init__(self, progress):
        super().__init__("owner withdrawal remains pending")
        self.progress = progress


def new_job(owner, uid, *, key=None, clock=time.time):
    """One producer for runtime jobs and one-shot cutover receipts."""
    return {"id": key or digest(owner), "owner": owner, "uid": uid,
            "status_token": secrets.token_urlsafe(32), "created_at": clock(),
            "completed_steps": [], "attempts": {}, "retry_at": 0,
            "failed_step": None, "billing_cleanup": {}, "results": {}}


def schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS deletion_jobs ("
               "id TEXT PRIMARY KEY NOT NULL, record TEXT NOT NULL)")


class JobRows:
    """C14: status/enqueue/restart read the identity DB, never the remote."""
    def __init__(self, identities, remote):
        self.identities, self.remote = identities, remote

    def load(self, key):
        with closing(self.identities._connect(write=False)) as db:
            row = db.execute("SELECT record FROM deletion_jobs WHERE id=?", (key,)).fetchone()
        if row is None:
            raise LookupError("deletion job is missing")
        return json.loads(row[0])

    def save(self, job):
        # A successful transport acknowledgement precedes local progress/effects.
        # A failed local commit can only cause an idempotent step to be replayed.
        self.remote.save(job)
        with closing(self.identities._connect(write=True)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("INSERT INTO deletion_jobs VALUES (?,?) ON CONFLICT(id) "
                       "DO UPDATE SET record=excluded.record",
                       (job["id"], json.dumps(job, sort_keys=True)))

    def records(self):
        with closing(self.identities._connect(write=False)) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT record FROM deletion_jobs")]


def load_unfinished_jobs(records=None):
    """b5 API: read current off-host authority, never a platform-backup copy.

    Missing/unreadable records raise. Loading never changes admission or starts
    recovery; the running broker's resume_loaded_job executes the normal path.
    """
    return [job for job in (records or RemoteRecords(os.environ["BACKUP_DEST"])).records()
            if job["completed_steps"] != list(STEPS)]


class DeletionJobs:
    def __init__(self, server, *, records=None, clock=time.time):
        self.server = server
        self.records = JobRows(server._owner_identities, records or RemoteRecords(
            os.environ["BACKUP_DEST"]))
        self.clock = clock
        # Serializes local/off-host job-row updates with billing webhook receipts.
        # Never held during a host operation or billing cancellation.
        self.record_lock = threading.RLock()

    def _load_or_create(self, owner):
        from tinyassets.broker.owner_volumes import binding

        key = digest(owner)
        try:
            job = self.records.load(key)
        except LookupError:
            record = binding(self.server._owner_identities, owner)
            job = new_job(owner, record["uid"], clock=self.clock)
            self.records.save(job)
        return job

    async def _enqueue(self, owner):
        job = await thread_work(self._load_or_create, owner)
        try:
            await self._step(job, "withdrawal", force=True)
        except Exception as error:
            raise WithdrawalPending(self.public(job, include_url=True)) from error
        return self.public(job, include_url=True)

    @staticmethod
    def public(job, *, include_url=False):
        complete = job["completed_steps"] == list(STEPS)
        result = {"status": "deleted" if complete else "deletion_in_progress",
                  "completed_steps": job["completed_steps"],
                  "pending_steps": [step for step in STEPS if step not in job["completed_steps"]],
                  "failed_step": job["failed_step"], "retry_at": job["retry_at"]}
        if include_url:
            result["status_url"] = ("/app/account/deletion/" + job["id"] + "/"
                                    + job["status_token"])
        return result

    def status(self, key, token):
        with self.record_lock:
            job = self.records.load(key)
            if not isinstance(token, str) or not hmac.compare_digest(token, job["status_token"]):
                raise PermissionError("deletion status capability refused")
            return self.public(job)

    def record_billing(self, owner, key, value):
        # Webhooks may hold billing serialization while a job waits for billing.
        # Receipt publication never waits for the whole job's execution lock.
        with self.record_lock:
            job = self.records.load(digest(owner))
            job["billing_cleanup"][key] = value
            self.records.save(job)

    def _begin_step(self, job, step, force):
        with self.record_lock:
            job.update(self.records.load(job["id"]))
            if step in job["completed_steps"] and not force:
                return False
            job["attempts"][step] = job["attempts"].get(step, 0) + 1
            self.records.save(job)
        return True

    def _finish_step(self, job, step, result=None, *, failed=False):
        with self.record_lock:
            job.update(self.records.load(job["id"]))
            if failed:
                job.update(failed_step=step,
                           retry_at=self.clock() + min(3600, 2 ** min(job["attempts"][step], 11)))
            else:
                job["results"][step] = result
                if step not in job["completed_steps"]:
                    job["completed_steps"].append(step)
                job.update(failed_step=None, retry_at=0)
            self.records.save(job)

    async def _step(self, job, step, *, force=False):
        if not await thread_work(self._begin_step, job, step, force):
            return
        try:
            operation = getattr(self, "_" + step)
            result = (await operation(job) if inspect.iscoroutinefunction(operation)
                      else await thread_work(operation, job))
            await thread_work(self._finish_step, job, step, result)
        except Exception:
            await thread_work(self._finish_step, job, step, failed=True)
            raise

    async def _resume_loaded_job(self, loaded):
        """b5 imports trusted off-host authority, then uses the normal job path.

        Ordinary restart/tick supplies local rows. Recovery supplies the newer
        off-host record while admission is closed; startup ordering is b5.
        """
        from tinyassets.broker.owner_volumes import binding

        owner = loaded["owner"]
        record = binding(self.server._owner_identities, owner)
        if loaded["id"] != digest(owner) or loaded["uid"] != record["uid"]:
            raise PermissionError("deletion job must match the permanent owner binding")
        def imported():
            with self.record_lock:
                self.records.save(loaded)
        await thread_work(imported)
        return await self._resume_owner(owner)

    async def _resume_owner(self, owner):
        job = self.records.load(digest(owner))
        try:
            installed = await thread_work(self.server.routing.withdrawal_installed,
                                          job["results"].get("withdrawal"))
        except (OSError, RuntimeError):
            installed = False
        if read(self.server._owner_identities, owner).state != "erased" or not installed:
            await self._step(job, "withdrawal", force=True)
        for step in STEPS:
            await self._step(job, step)
        return self.public(job)

    async def enqueue(self, owner):
        result = await owner_call(self.server._owner_identities.path, owner, self._enqueue, owner)
        self.server.routing.start(owner, self._resume_owner)
        return result

    async def resume_loaded_job(self, loaded):
        return await owner_call(self.server._owner_identities.path, loaded["owner"],
                                self._resume_loaded_job, loaded)

    def tick(self):
        for job in load_unfinished_jobs(self.records):
            if job["retry_at"] <= self.clock():
                self.server.routing.start(job["owner"], self._resume_owner)

    async def _withdrawal(self, job):
        from tinyassets.broker.admission import local_mutate

        routing = self.server.routing
        # The admission row is the durable receipt for this mutation even if
        # the process died before writing the step's completion receipt.
        if read(self.server._owner_identities, job["owner"]).state != "erased":
            receipt = await local_mutate(
                self.server._owner_identities.path, job["owner"], "erase", routing=routing)
        else:
            receipt = await routing.changed(job["owner"])
        if "snapshots" in job["completed_steps"]:
            # Recovery can restore an older map after the off-host job finished.
            # Reassert its map effect without repeating completed host deletion.
            await thread_work(self._erase_snapshot_map, job)
        return receipt

    async def _volume(self, job):
        if await self.server.routing.volumes.host(job["owner"], "destroy") != {"destroyed": True}:
            raise RuntimeError("owner volume deletion remains pending")

    def _erase_snapshot_map(self, job):
        from tinyassets.broker.owner_volumes import dispatch

        return dispatch(self.server._owner_identities, {
            "op": "BOX_VOLUME", "principal": job["owner"], "action": "erase", "arguments": {}})

    async def _snapshots(self, job):
        record = self._erase_snapshot_map(job)
        if await self.server.routing.volumes.host(job["owner"], "delete_snapshots",
                snapshot_ids=None, released=record["work"]["released"]) != {"deleted": True}:
            raise RuntimeError("owner snapshot deletion remains pending")

    def _custody(self, job):
        from tinyassets.broker.account_erasure import ACCOUNT_SCOPE, local_erase

        owner = job["owner"]
        result = local_erase(self.server._ledger_for(owner), principal=owner,
                             command_center=ACCOUNT_SCOPE)
        self.server._policy_store.erase(owner)
        return result

    def _billing(self, job):
        from tinyassets.billing.stripe_adapter import cancel_account_billing
        from tinyassets.broker.owner_reads import Plans

        result = cancel_account_billing(
            self.server._data_root, job["owner"], plans=Plans(self.server._data_root),
            record_cleanup=lambda key, value: self.record_billing(job["owner"], key, value))
        if result not in {"cancelled", "none", "attempt_expired"}:
            raise RuntimeError("billing cancellation remains pending")
        return result

    def _credentials(self, job):
        from tinyassets.router_credentials import revoke_request

        if not self.server.routing.withdrawal_installed(job["results"].get("withdrawal")):
            raise RuntimeError("router withdrawal installation remains pending")
        if revoke_request(self.server._data_root, job["owner"]) != {
                              "op": "ROUTER_CREDENTIAL_IS", "result": {"revoked": True}}:
            raise RuntimeError("router credential revocation remains pending")

    def _identity(self, job):
        from tinyassets.router_account_deletion import delete_workos_user

        result = delete_workos_user(job["owner"])
        if result not in {"deleted", "not_applicable"}:
            raise RuntimeError("identity deletion remains pending")
        return result

    def _receipt(self, job):
        return {"deleted": True, "completed_at": self.clock()}


def request(root, operation, **arguments):
    """Router's authenticated broker seam; never an owner-volume operation."""
    from tinyassets.broker.owner_identities import _owner_request

    answer = _owner_request(root, {"op": "DELETION_JOB", "operation": operation,
                                   "arguments": arguments})
    if set(answer) == {"op", "result"} and answer["op"] == "DELETION_JOB_PENDING":
        raise WithdrawalPending(answer["result"])
    if set(answer) != {"op", "result"} or answer["op"] != "DELETION_JOB_IS":
        raise RuntimeError("deletion job unavailable")
    return answer["result"]
