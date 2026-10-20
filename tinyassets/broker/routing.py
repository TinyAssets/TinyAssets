"""C5: broker publication, cold wake and credential retirement authority.

Only identity metadata is read here. The persistent secret never enters an
owner volume; boxhostd receives a bound launch record through its private RPC.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import threading
from contextlib import closing
from pathlib import Path

from tinyassets.broker.admission import owner_lock, read

log = logging.getLogger(__name__)


class RoutingAuthority:
    def __init__(self, server):
        self.server = server
        self.identities = server._owner_identities
        self.root = server._data_root
        self._publication = threading.Lock()
        self._instance = None

    def _router(self, request):
        from tinyassets.router_credentials import routing_request

        reply = routing_request(self.root, request)
        if reply.get("op") != "ROUTING_SNAPSHOT_IS":
            raise RuntimeError("router snapshot installation pending")
        return reply["result"]

    def publish(self, *, only_restart=False):
        # One sequencer orders full snapshots, including after broker restart.
        with self._publication:
            instance = self._router({"operation": "routing_instance"})["instance"]
            if only_restart and instance == self._instance:
                return
            records = {}
            with closing(self.identities._connect(write=True)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE routing_sequence SET revision=revision+1")
                revision = db.execute("SELECT revision FROM routing_sequence").fetchone()[0]
                from tinyassets.broker.admission import admission

                run = Path(os.environ.get("TINYASSETS_BOXES_RUN", "/run/tinyassets-boxhostd"))
                for owner, uid, secret in db.execute(
                        "SELECT i.principal,i.machine_id,s.secret FROM owner_identities i "
                        "LEFT JOIN owner_box_secrets s ON s.principal=i.principal"):
                    state = admission(db, owner).state
                    if state == "admitted" and not secret:
                        raise RuntimeError("admitted owner is missing its ingress credential")
                    records[owner] = {"state": state,
                                      "socket": str(run / str(uid) / "app.sock"),
                                      "secret": secret if state == "admitted" else None}
            installed = self._router({"operation": "routing_install", "instance": instance,
                                      "revision": revision, "records": records})
            if installed != {"revision": revision}:
                raise RuntimeError("router did not acknowledge current snapshot")
            self._instance = instance

    def changed(self, owner):
        from tinyassets.boxes.daemon_control import request

        with owner_lock(self.identities.path, owner):
            state = read(self.identities, owner).state
            try:
                self.publish()
            except (OSError, RuntimeError):
                self._instance = None
                if state == "admitted":
                    raise
                log.warning("withdrawal publication pending; stopping old ingress")
            if state != "admitted":
                # C5 permits ack after all projections install OR old ingress is
                # unreachable. Stopping it also retires in-flight route copies.
                asyncio.run_coroutine_threadsafe(
                    self.server.withdraw_channels(owner), self.server._loop).result(30)
                operation, expected = ("withdraw", {"withdrawn": True}) if state == "erased" else (
                    "retire", {"stopped": True})
                if request(operation, owner) != expected:
                    raise RuntimeError("admission withdrawal remains pending")

    def wake(self, owner, *, scheduled=False):
        from tinyassets.boxes.daemon_control import request
        from tinyassets.broker.box_admission import owner_record

        with owner_lock(self.identities.path, owner):
            record = owner_record(self.identities, owner, for_wake=True)
            # A new process cannot accept stale ingress after a failed withdrawal
            # delivery. Reinstallation precedes every cold lifecycle operation.
            self.publish()
            result = request("wake", owner, record=record, scheduled=scheduled)
            if result != {"uid": record["uid"]}:
                raise RuntimeError("broker-bound box did not become ready")
            return result["uid"]

    def revoke(self, owner):
        from tinyassets.boxes.daemon_control import request

        with owner_lock(self.identities.path, owner):
            read(self.identities, owner).require_work()
            if request("retire", owner) != {"stopped": True}:
                raise RuntimeError("old box credential retirement remains pending")
            with closing(self.identities._connect(write=True)) as db, db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("UPDATE owner_box_secrets SET secret=? WHERE principal=?",
                           (secrets.token_urlsafe(32), owner))
            self.publish()

    async def run(self, stop):
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.publish, only_restart=True)
            except Exception:
                # Establish/retry delivery only; healthy installed routes never
                # expire and forwarding does not depend on this control loop.
                self._instance = None
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except TimeoutError:
                pass
