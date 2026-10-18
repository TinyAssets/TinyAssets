"""boxhostd owns the persistent sandbox, its capacity and its restart clock."""
from __future__ import annotations

import http.client
import json
import logging
import math
import os
import secrets
import socket
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from tinyassets.boxes.admission import BoxSuspended
from tinyassets.boxes.provider import BoxAuthError, BoxBusy, BoxError
from tinyassets.broker.box_admission import OwnerSuspended

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
    image: str = ""
    restarting: bool = False
    roll_failed: bool = False
    deletion_only: bool = False
    leases: dict[str, float] = field(default_factory=dict)


class OwnerDaemons:
    def __init__(self, runtime, admission, quota, relays, directory: Path, *,
                 idle_seconds=900, awake_cap=6, memory_bytes=1024**3,
                 wake_timeout=60, backoff_cap=60, failure_limit=5, images=None,
                 roll_wait=60):
        if (not 0 < idle_seconds <= 86400 or awake_cap < 1 or wake_timeout <= 0
                or backoff_cap < 1 or failure_limit < 1 or not 0 < roll_wait <= 60):
            raise ValueError("positive owner lifecycle limits required")
        self.runtime, self.admission, self.quota, self.relays = runtime, admission, quota, relays
        self.directory = Path(directory)
        self.idle_seconds, self.awake_cap = idle_seconds, awake_cap
        self.memory_bytes, self.wake_timeout = memory_bytes, wake_timeout
        self.backoff_cap, self.failure_limit = backoff_cap, failure_limit
        self.boxes: dict[int, Box] = {}
        self.condition = threading.Condition(threading.RLock())
        self.waiting: list[tuple[bool, object]] = []
        self.images, self.roll_wait = images, roll_wait
        self.rolling = set()
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
                    or set(result) != {"active", "idle_seconds", "draining", "revoked", "deleting"}
                    or type(result["active"]) is not int or result["active"] < 0
                    or type(result["idle_seconds"]) not in (int, float)
                    or not math.isfinite(result["idle_seconds"]) or result["idle_seconds"] < 0
                    or type(result["draining"]) is not bool or type(result["revoked"]) is not bool
                    or type(result["deleting"]) is not bool):
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

    def _start(self, box, deadline, *, image=None, cleanup=True):
        admission = {"deletion_only": True} if box.deletion_only else {}
        record = self.admission.owner(box.owner, rotate=True, **admission)
        if record["uid"] != box.uid:
            raise BoxError("broker owner reservation changed")
        self.quota.provision(box.uid, record["quota"], record["centers"][0])
        relay = self.relays.bind(box.owner, box.uid)
        box.secret = record["secret"]
        directory, secret = self._paths(box.uid, box.secret)
        # Reserve capacity before touching runsc; failed cleanup keeps the slot.
        box.awake = True
        try:
            launch = {}
            if box.deletion_only:
                launch["deletion_only"] = True
            if self.images is not None:
                image = image or self.images.current
                if not self.images.compatible(image):
                    raise BoxError("cutover boundary: restore with deploy/backup-restore.sh")
                launch["rootfs"] = self.images.path(image)
                # Persist before launch: a process can write before readiness.
                self.images.ran(box.uid, image)
                box.image = image
            self.runtime.start(box.uid, box.uid, relay=relay, memory_bytes=self.memory_bytes,
                               app_dir=directory, secret_file=secret, **launch)
            while True:
                if not self.runtime.alive(box.uid):
                    raise BoxError("owner daemon exited during startup")
                try:
                    status = self._rpc(box)
                    if not status["draining"] or (box.deletion_only and status["deleting"]):
                        box.started = box.last = time.monotonic()
                        box.error = ""
                        box.roll_failed = False
                        return
                except (OSError, http.client.HTTPException):
                    pass
                if time.monotonic() >= deadline:
                    raise BoxError("owner app socket did not become ready")
                time.sleep(.05)
        except Exception:
            if cleanup:
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
        with self.condition:
            self.condition.notify_all()

    def _sleep(self, box, *, idle, stop=True):
        if box.restarting:
            return False
        now = time.monotonic()
        box.leases = {key: end for key, end in box.leases.items() if end > now}
        if box.leases:
            return False
        with self.idle_guard(box.uid) as allowed:
            return self._drain(box, idle=idle, stop=stop) if allowed else False

    def _activity(self, box):
        try:
            status = self._rpc(box)
            box.last = max(box.last, time.monotonic() - status["idle_seconds"])
            return status
        except (OSError, http.client.HTTPException):
            pass  # Unknown status is never permission to evict; drain must still succeed.

    def _drain(self, box, *, idle, stop=True):
        try:
            status = self._rpc(box, "drain", idle_seconds=idle)
        except (OSError, http.client.HTTPException):
            return False  # Unknown status never authorizes idle eviction.
        if not status["draining"] or status["active"] or status["deleting"]:
            return False
        if not stop:
            return True
        try:
            self._stop(box)
        except Exception as exc:
            self._failed(box, exc)
            return False  # Keep the uncertain sandbox's capacity reservation.
        return True

    def resolve(self, owner, *, cleanup=False):
        # The same lock covers readiness and the lease: no eviction window.
        with self.condition:
            if cleanup:
                # Cleanup may address an existing process but never readmit or
                # wake a fenced owner. Cold/missing boxes go straight to destroy.
                box = next((box for box in self.boxes.values() if box.owner == owner
                            and box.awake and self.runtime.alive(box.uid)), None)
                if box is None:
                    raise LookupError("owner has no live cleanup box")
                box.deletion_only = True
                self._rpc(box, "delete-admission")
                uid = box.uid
            else:
                uid = self.ensure_awake(owner)
            token = secrets.token_urlsafe(32)
            self.boxes[uid].leases[token] = time.monotonic() + 10
            result = {"uid": uid, "lease": token}
            if cleanup:
                result["secret"] = self.boxes[uid].secret
            return result

    def release(self, owner, token):
        with self.condition:
            for box in self.boxes.values():
                if box.owner == owner:
                    box.leases.pop(token, None)
            self.condition.notify_all()

    def ensure_awake(self, owner, *, scheduled=False, cleanup=False):
        if type(scheduled) is not bool or type(cleanup) is not bool or (scheduled and cleanup):
            raise ValueError("scheduled must be boolean")
        deadline = time.monotonic() + self.wake_timeout
        with self.condition:
            admission = {"deletion_only": True} if cleanup else {}
            record = self.admission.owner(owner, **admission)
            uid = record["uid"]
            box = self.boxes.setdefault(uid, Box(owner, uid))
            if box.owner != owner:
                raise BoxError("broker owner reservation changed")
            ticket = (scheduled, object())
            self.waiting.append(ticket)
            try:
                while True:
                    if box.restarting:
                        left = deadline - time.monotonic()
                        if left <= 0:
                            raise BoxBusy("owner roll readiness deadline exceeded")
                        self.condition.wait(min(left, .1))
                        continue
                    if box.awake and self.runtime.alive(uid):
                        if box.error:
                            raise BoxError("owner sandbox has an unverified launch or stop")
                        if self._rpc(box)["draining"] and not cleanup:
                            raise BoxError("owner daemon is still draining")
                        if admission:
                            self._rpc(box, "delete-admission")
                            box.deletion_only = True
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
                        awake = [item for item in self.boxes.values()
                                 if item.awake or item.restarting]
                        if len(awake) >= self.awake_cap and not scheduled:
                            for candidate in awake:
                                self._activity(candidate)
                            for victim in sorted(awake, key=lambda item: item.last):
                                if self._sleep(victim, idle=0):
                                    break
                        if sum(item.awake or item.restarting for item in
                               self.boxes.values()) < self.awake_cap:
                            box.wanted = True
                            box.deletion_only = bool(admission)
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
                    self.admission.owner(owner, **admission)
            finally:
                self.waiting.remove(ticket)
                self.condition.notify_all()

    def retire(self, owner, uid):
        self.runtime.box_id(uid)
        with self.condition:
            box = self.boxes.get(uid)
            deadline = time.monotonic() + 600
            while box is not None and box.restarting:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise BoxBusy("owner restart did not settle before retirement deadline")
                self.condition.wait(min(left, .1))
            if box is not None and box.owner != owner:
                raise BoxError("retirement owner differs from broker reservation")
            if box is None:
                self.runtime.stop(uid)
            else:
                self._stop(box, retire=True)
            self.condition.notify_all()

    def destroy(self, owner, uid):
        """Account retirement, unlike center retirement, erases the entire volume."""
        from tinyassets.boxes.destroy import socket_tree

        with self.condition:
            self.retire(owner, uid)
            self.quota.destroy(uid)
            self.relays.retire(uid)
            socket_tree(self.directory, uid)
            (self.directory / "secrets" / str(uid)).unlink(missing_ok=True)
            self.boxes.pop(uid, None)

    def status(self, owner):
        with self.condition:
            try:
                uid = self.admission.owner(owner)["uid"]
            except (BoxAuthError, PermissionError):
                box = next((b for b in self.boxes.values() if b.owner == owner), None)
                if box is None:
                    raise
                uid = box.uid
            box = self.boxes.get(uid)
            return {"uid": uid, "awake": bool(box and box.awake),
                    "failures": box.failures if box else 0,
                    "error": box.error if box else "",
                    "image": (box.image if box else
                              self.images.last.get(str(uid), "") if self.images else "")}

    def roll(self, image, uid=None):
        """Publish the wake image, then drain/restart one box without killing work."""
        with self.condition:
            if self.images is None:
                raise BoxError("image registry unavailable")
            if uid is None:
                if self.rolling:
                    raise BoxError("another roll is in progress")
                self.images.select(image)
                known = set(map(int, self.images.last)) | set(self.boxes)
                awake = {box.uid for box in self.boxes.values() if box.awake}
                return {"uids": sorted(awake), "asleep": len(known - awake)}
            self.runtime.box_id(uid)
            if image != self.images.current:
                raise BoxError("roll image superseded")
            box = self.boxes.get(uid)
            if box is None or not box.awake:
                return {"uid": uid, "outcome": "asleep"}
            if uid in self.rolling:
                return {"uid": uid, "outcome": "deferred_busy"}
            self.rolling.add(uid)
            deadline = time.monotonic() + self.roll_wait
            try:
                while box.awake:
                    if box.image == image:
                        if (box.error or box.roll_failed or not self.runtime.alive(uid)
                                or self._rpc(box)["draining"]):
                            raise BoxError("current box image is not healthy")
                        return {"uid": uid, "outcome": "rolled"}
                    # No admission gap: drain closes the same gate held by HTTP
                    # and detached turns. Unknown state never permits a stop.
                    if self._sleep(box, idle=0, stop=False):
                        break
                    left = deadline - time.monotonic()
                    if left <= 0:
                        return {"uid": uid, "outcome": "deferred_busy"}
                    self.condition.wait(min(left, .1))
                else:
                    return {"uid": uid, "outcome": "asleep"}
                previous = box.image
                try:
                    self._roll_start(box, image)
                    return {"uid": uid, "outcome": "rolled"}
                except Exception:
                    # Never restore an image that cannot understand split data.
                    # Fail closed even if candidate cleanup or restore fails.
                    box.error = "candidate health failed"
                    try:
                        compatible = bool(previous and self.images.compatible(previous))
                    except OSError:
                        compatible = False  # An absent image cannot prove the cutover floor.
                    try:
                        # Startup can already have scheduled work. A failed
                        # health probe never authorizes killing that work.
                        drain_deadline = time.monotonic() + self.roll_wait
                        while self.runtime.alive(uid):
                            if self._sleep(box, idle=0, stop=False):
                                break
                            left = drain_deadline - time.monotonic()
                            if left <= 0:
                                raise BoxError("candidate cannot safely drain")
                            self.condition.wait(min(left, .1))
                        if not compatible:
                            self._stop(box)
                            raise BoxError("cutover rollback refused")
                        self._roll_start(box, previous)
                    except Exception:
                        box.wanted = False
                        box.roll_failed = True
                        box.error = ("box rollback failed" if compatible else
                                     "cutover boundary: restore with deploy/backup-restore.sh")
                        raise BoxError(box.error) from None
                    return {"uid": uid, "outcome": "rolled_back", "reason": "health_failed"}
            finally:
                self.rolling.remove(uid)
                self.condition.notify_all()

    def _roll_start(self, box, image):
        # Keep a capacity reservation while other boxes wake or roll. The
        # lifecycle lock is not held during candidate readiness polling.
        box.awake = True
        box.restarting = True
        self.condition.release()
        try:
            self._stop(box)
            box.wanted = True
            self._start(box, time.monotonic() + self.wake_timeout, image=image, cleanup=False)
        finally:
            self.condition.acquire()
            box.restarting = False
            self.condition.notify_all()

    def maintain(self):
        """One host clock handles idle sleep and bounded crash restart."""
        with self.condition:
            for box in self.boxes.values():
                if box.uid in self.rolling:
                    continue
                try:
                    admission = {"deletion_only": True} if box.deletion_only else {}
                    self.admission.owner(box.owner, **admission)
                except (LookupError, BoxAuthError, PermissionError, OSError) as exc:
                    outcome = ("suspended" if isinstance(exc, (BoxSuspended, OwnerSuspended))
                               else "admission_refused")
                    try:
                        self._stop(box, retire=True)
                    except Exception as stop_error:
                        self._failed(box, stop_error)
                    box.wanted = False
                    box.error = outcome
                    continue
                if box.roll_failed:
                    if box.awake:
                        self._sleep(box, idle=0)
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
                        and sum(item.awake or item.restarting for item in
                                self.boxes.values()) < self.awake_cap):
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
