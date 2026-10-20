"""C3/C6: the broker owns deletion progress; off-host records are authoritative.

No owner-volume paths enter this module. The host transports opaque metadata
and performs volume operations; only this job decides which step comes next.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import threading
import time
from contextlib import closing

from tinyassets.broker.admission import digest

STEPS = ("withdrawal", "volume", "snapshots", "custody", "billing",
         "credentials", "identity", "receipt")
log = logging.getLogger(__name__)


class WithdrawalPending(RuntimeError):
    """Durable job exists, but closure has not been acknowledged."""
    def __init__(self, progress):
        super().__init__("owner withdrawal remains pending")
        self.progress = progress


class HostRecords:
    """C3: backup transport stays with the host's existing backup credentials."""

    def call(self, action, **arguments):
        from tinyassets.boxes.daemon_control import request

        return request("deletion_records", action=action, arguments=arguments)["result"]

    def load(self, key):
        return self.call("read", key=key)

    def save(self, job):
        self.call("write", key=job["id"], record=job)

    def records(self):
        for key in self.call("list"):
            yield self.load(key)


def load_unfinished_jobs(records=None):
    """b5 API: read current off-host authority, never a platform-backup copy.

    Missing/unreadable records raise. Loading never changes admission or starts
    recovery; the running broker's resume_loaded_job executes the normal path.
    """
    return [job for job in (records or HostRecords()).records()
            if job["completed_steps"] != list(STEPS)]


class DeletionJobs:
    def __init__(self, server, *, records=None, clock=time.time):
        self.server = server
        self.records = records or HostRecords()
        self.clock = clock
        self.lock = threading.RLock()
        self.record_lock = threading.RLock()

    def enqueue(self, owner):
        from tinyassets.broker.owner_volumes import binding

        with self.lock:
            key = digest(owner)
            # Listing must succeed: an unavailable remote is never "no job".
            jobs = {job["id"]: job for job in self.records.records()}
            job = jobs.get(key)
            if job is None:
                record = binding(self.server._owner_identities, owner)
                job = {"id": key, "owner": owner, "uid": record["uid"],
                       "status_token": secrets.token_urlsafe(32), "created_at": self.clock(),
                       "completed_steps": [], "attempts": {}, "retry_at": 0,
                       "failed_step": None, "billing_cleanup": {}, "results": {}}
                self.records.save(job)  # C6 step 1: off-host BEFORE withdrawing admission.
            try:
                self._step(job, "withdrawal", force=True)
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

    def _step(self, job, step, *, force=False):
        with self.record_lock:
            job.update(self.records.load(job["id"]))
            if step in job["completed_steps"] and not force:
                return
            job["attempts"][step] = job["attempts"].get(step, 0) + 1
            self.records.save(job)
        try:
            result = getattr(self, "_" + step)(job)
            with self.record_lock:
                job.update(self.records.load(job["id"]))
                job["results"][step] = result
                if step not in job["completed_steps"]:
                    job["completed_steps"].append(step)
                job.update(failed_step=None, retry_at=0)
                self.records.save(job)
        except Exception:
            with self.record_lock:
                job.update(self.records.load(job["id"]))
                job.update(failed_step=step,
                           retry_at=self.clock() + min(3600, 2 ** min(job["attempts"][step], 11)))
                self.records.save(job)
            raise

    def resume_loaded_job(self, loaded):
        """b5 API: resume through the same broker steps, under its single writer.

        Reload off-host so a stale caller cannot rewind progress or supply an
        owner/UID. Withdrawal is re-established even after a historical restore.
        """
        with self.lock:
            job = self.records.load(loaded["id"])
            self._step(job, "withdrawal", force=True)
            for step in STEPS:
                self._step(job, step)
            return self.public(job)

    async def run(self, stop):
        while not stop.is_set():
            try:
                jobs = await asyncio.to_thread(load_unfinished_jobs, self.records)
                for job in jobs:
                    if stop.is_set():
                        break
                    if job["retry_at"] <= self.clock():
                        try:
                            await asyncio.to_thread(self.resume_loaded_job, job)
                        except Exception:
                            log.exception("deletion job remains pending")
            except Exception:
                log.exception("deletion job authority unavailable")
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except TimeoutError:
                pass

    def _withdrawal(self, job):
        from tinyassets.boxes.daemon_control import request

        if request("withdraw", job["owner"]) != {"withdrawn": True}:
            raise RuntimeError("owner withdrawal remains pending")

    def _volume(self, job):
        from tinyassets.boxes.daemon_control import request

        if request("destroy", job["owner"]) != {"destroyed": True}:
            raise RuntimeError("owner volume deletion remains pending")

    def _snapshots(self, job):
        from tinyassets.boxes.daemon_control import request

        if request("delete_snapshots", job["owner"]) != {"deleted": True}:
            raise RuntimeError("owner snapshot deletion remains pending")
        with closing(self.server._owner_identities._connect(write=True)) as db, db:
            db.execute("DELETE FROM owner_snapshots WHERE principal=?", (job["owner"],))
            db.execute("UPDATE owner_volumes SET snapshot_id=NULL,snapshot_sha256=NULL,"
                       "work=NULL,local=0 WHERE principal=?", (job["owner"],))

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
        from tinyassets.router_credentials import broker_request

        if broker_request(self.server._data_root, job["owner"],
                          {"operation": "revoke_account"}) != {
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
