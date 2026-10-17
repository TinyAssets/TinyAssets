"""boxhostd owns the persistent sandbox, its capacity and its restart clock."""
from __future__ import annotations

import http.client
import json
import logging
import math
import os
import socket
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from tinyassets.boxes.provider import BoxBusy, BoxError

log = logging.getLogger(__name__)


@dataclass
class Box:
    owner: str
    uid: int
    secret: str = ""
    awake: bool = False
    wanted: bool = False
    last: float = 0
    started: float = 0
    failures: int = 0
    retry_at: float = 0
    error: str = ""


class OwnerDaemons:
    def __init__(self, runtime, admission, quota, relays, directory: Path, *,
                 idle_seconds=900, awake_cap=6, memory_bytes=1024**3,
                 wake_timeout=60, backoff_cap=60, failure_limit=5):
        if (not 0 < idle_seconds <= 86400 or awake_cap < 1 or wake_timeout <= 0
                or backoff_cap < 1 or failure_limit < 1):
            raise ValueError("positive owner lifecycle limits required")
        self.runtime, self.admission, self.quota, self.relays = runtime, admission, quota, relays
        self.directory = Path(directory)
        self.idle_seconds, self.awake_cap = idle_seconds, awake_cap
        self.memory_bytes, self.wake_timeout = memory_bytes, wake_timeout
        self.backoff_cap, self.failure_limit = backoff_cap, failure_limit
        self.boxes: dict[int, Box] = {}
        self.condition = threading.Condition(threading.RLock())
        self.waiting: list[tuple[bool, object]] = []
        self.idle_guard = lambda uid: nullcontext(True)

    def _rpc(self, box, operation="status", **body):
        client = http.client.HTTPConnection("owner", timeout=2)
        try:
            client.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.sock.settimeout(2)
            client.sock.connect(str(self.directory / str(box.uid) / "app.sock"))
            client.request("GET" if operation == "status" else "POST", "/_box/" + operation,
                           body=json.dumps(body), headers={"x-box-secret": box.secret,
                                                         "content-type": "application/json"})
            response = client.getresponse()
            if response.status != 200:
                raise OSError("owner lifecycle endpoint refused")
            try:
                result = json.loads(response.read(4096))
            except ValueError as exc:
                raise OSError("invalid owner lifecycle status") from exc
            if (not isinstance(result, dict)
                    or set(result) != {"active", "idle_seconds", "draining", "revoked"}
                    or type(result["active"]) is not int or result["active"] < 0
                    or type(result["idle_seconds"]) not in (int, float)
                    or not math.isfinite(result["idle_seconds"]) or result["idle_seconds"] < 0
                    or type(result["draining"]) is not bool or type(result["revoked"]) is not bool):
                raise OSError("invalid owner lifecycle status")
            return result
        finally:
            client.close()

    def _paths(self, uid, secret):
        directory = self.directory / str(uid)
        directory.mkdir(mode=0o710, exist_ok=True)
        if directory.is_symlink():
            raise BoxError("owner socket directory is a link")
        os.chown(directory, uid, 1001)
        os.chmod(directory, 0o710)
        (directory / "app.sock").unlink(missing_ok=True)
        secrets = self.directory / "secrets"
        secrets.mkdir(mode=0o700, exist_ok=True)
        path = secrets / str(uid)
        path.unlink(missing_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400)
        with os.fdopen(fd, "w") as stream:
            stream.write(secret)
            stream.flush()
            os.fchown(stream.fileno(), uid, uid)
        return directory, path

    def _start(self, box, deadline):
        record = self.admission.owner(box.owner, rotate=True)
        if record["uid"] != box.uid:
            raise BoxError("broker owner reservation changed")
        self.quota.provision(box.uid, record["quota"], record["centers"][0])
        relay = self.relays.bind(box.owner, box.uid)
        box.secret = record["secret"]
        directory, secret = self._paths(box.uid, box.secret)
        # Reserve capacity before touching runsc; failed cleanup keeps the slot.
        box.awake = True
        try:
            self.runtime.start(box.uid, box.uid, relay=relay, memory_bytes=self.memory_bytes,
                               app_dir=directory, secret_file=secret)
            while True:
                if not self.runtime.alive(box.uid):
                    raise BoxError("owner daemon exited during startup")
                try:
                    status = self._rpc(box)
                    if not status["draining"]:
                        box.started = box.last = time.monotonic()
                        box.error = ""
                        return
                except (OSError, http.client.HTTPException):
                    pass
                if time.monotonic() >= deadline:
                    raise BoxError("owner app socket did not become ready")
                time.sleep(.05)
        except Exception:
            self.runtime.stop(box.uid)
            box.awake = False
            raise

    def _failed(self, box, error):
        box.failures += 1
        delay = min(self.backoff_cap, 2 ** min(box.failures - 1, 16))
        box.retry_at = time.monotonic() + delay
        box.error = ("owner daemon failed repeatedly" if box.failures >= self.failure_limit
                     else str(error))
        log.error("owner daemon uid=%s failures=%s retry_in=%s: %s",
                  box.uid, box.failures, delay, box.error)

    def _stop(self, box, *, retire=False):
        box.wanted = False
        if box.awake:
            if retire:
                try:
                    self._rpc(box, "revoke")
                except (OSError, http.client.HTTPException):
                    pass  # A dead/unreachable ingress is still followed by a proven stop.
                self.runtime.stop(box.uid)
            else:
                self.runtime.shutdown(box.uid)
            box.awake = False
        box.secret = ""
        box.error, box.failures, box.retry_at = "", 0, 0
        (self.directory / "secrets" / str(box.uid)).unlink(missing_ok=True)
        (self.directory / str(box.uid) / "app.sock").unlink(missing_ok=True)
        self.condition.notify_all()

    def _sleep(self, box, *, idle):
        with self.idle_guard(box.uid) as allowed:
            return self._drain(box, idle=idle) if allowed else False

    def _activity(self, box):
        try:
            status = self._rpc(box)
            box.last = max(box.last, time.monotonic() - status["idle_seconds"])
            return status
        except (OSError, http.client.HTTPException):
            pass  # Unknown status is never permission to evict; drain must still succeed.

    def _drain(self, box, *, idle):
        try:
            status = self._rpc(box, "drain", idle_seconds=idle)
        except (OSError, http.client.HTTPException):
            return False  # Unknown status never authorizes idle eviction.
        if not status["draining"]:
            return False
        try:
            self._stop(box)
        except Exception as exc:
            self._failed(box, exc)
            return False  # Keep the uncertain sandbox's capacity reservation.
        return True

    def ensure_awake(self, owner, *, scheduled=False):
        if type(scheduled) is not bool:
            raise ValueError("scheduled must be boolean")
        deadline = time.monotonic() + self.wake_timeout
        with self.condition:
            record = self.admission.owner(owner)
            uid = record["uid"]
            box = self.boxes.setdefault(uid, Box(owner, uid))
            if box.owner != owner:
                raise BoxError("broker owner reservation changed")
            ticket = (scheduled, object())
            self.waiting.append(ticket)
            try:
                while True:
                    if box.awake and self.runtime.alive(uid):
                        if box.error:
                            raise BoxError("owner sandbox has an unverified launch or stop")
                        if self._rpc(box)["draining"]:
                            raise BoxError("owner daemon is still draining")
                        box.last = time.monotonic()
                        return uid
                    if box.awake:
                        self.runtime.stop(uid)
                        box.awake = False
                        self._failed(box, BoxError("owner daemon crashed"))
                    if box.failures >= self.failure_limit:
                        raise BoxError(box.error)
                    first = min(self.waiting, key=lambda item: item[0])
                    ready = first is ticket and time.monotonic() >= box.retry_at
                    if ready:
                        awake = [item for item in self.boxes.values() if item.awake]
                        if len(awake) >= self.awake_cap and not scheduled:
                            for candidate in awake:
                                self._activity(candidate)
                            for victim in sorted(awake, key=lambda item: item.last):
                                if self._sleep(victim, idle=0):
                                    break
                        if sum(item.awake for item in self.boxes.values()) < self.awake_cap:
                            box.wanted = True
                            try:
                                self._start(box, deadline)
                                return uid
                            except Exception as exc:
                                self._failed(box, exc)
                                raise BoxError("owner daemon startup failed") from exc
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise BoxBusy("owner wake capacity/backoff deadline exceeded")
                    self.condition.wait(min(left, 1))
                    # A queued wake never keeps an admission across retirement.
                    self.admission.owner(owner)
            finally:
                self.waiting.remove(ticket)
                self.condition.notify_all()

    def retire(self, owner, uid):
        self.runtime.box_id(uid)
        with self.condition:
            box = self.boxes.get(uid)
            if box is not None and box.owner != owner:
                raise BoxError("retirement owner differs from broker reservation")
            if box is None:
                self.runtime.stop(uid)
            else:
                self._stop(box, retire=True)
            self.condition.notify_all()

    def status(self, owner):
        with self.condition:
            uid = self.admission.owner(owner)["uid"]
            box = self.boxes.get(uid)
            return {"uid": uid, "awake": bool(box and box.awake),
                    "failures": box.failures if box else 0,
                    "error": box.error if box else ""}

    def maintain(self):
        """One host clock handles idle sleep and bounded crash restart."""
        with self.condition:
            for box in self.boxes.values():
                try:
                    self.admission.owner(box.owner)
                except LookupError:
                    self._stop(box, retire=True)
                    continue
                if box.awake and box.error:
                    # A failed launch/cleanup retains capacity, never readiness.
                    # Prove it absent before retrying; do not advertise an
                    # app whose resource-floor verification failed.
                    self.runtime.stop(box.uid)
                    box.awake = False
                if box.awake:
                    if not self.runtime.alive(box.uid):
                        self.runtime.stop(box.uid)
                        box.awake = False
                        self._failed(box, BoxError("owner daemon crashed"))
                    else:
                        status = self._activity(box)
                        if (status and not status["draining"]
                                and time.monotonic() - box.started >= self.idle_seconds):
                            box.failures = 0
                        if (time.monotonic() - box.last >= self.idle_seconds
                                and time.monotonic() >= box.retry_at
                                and box.failures < self.failure_limit):
                            self._sleep(box, idle=self.idle_seconds)
                if (box.wanted and not box.awake
                        and time.monotonic() >= box.retry_at and not self.waiting
                        and sum(item.awake for item in self.boxes.values()) < self.awake_cap):
                    try:
                        self._start(box, time.monotonic() + self.wake_timeout)
                    except Exception as exc:
                        self._failed(box, exc)
            self.condition.notify_all()

    def close(self):
        with self.condition:
            for box in self.boxes.values():
                if box.awake:
                    self.runtime.shutdown(box.uid)
                    box.awake = False
