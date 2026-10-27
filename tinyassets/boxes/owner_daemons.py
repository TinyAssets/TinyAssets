"""boxhostd executes broker-authorized sandbox operations and reports facts."""
from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import logging
import math
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from tinyassets.boxes.provider import BoxAuthError, BoxBusy, BoxError

log = logging.getLogger(__name__)


@dataclass
class Box:
    owner: str
    uid: int
    secret: str = ""
    awake: bool = False
    last: float = 0
    started: float = 0
    failures: int = 0
    error: str = ""
    answered: float = 0.0  # monotonic time of the last status the box's own app gave
    image: str = ""
    launch: dict = field(default_factory=dict)
    #: N10 one-shot: old authority lifted from the volume before this start.
    authority: dict | None = None


class OwnerDaemons:
    def __init__(self, runtime, admission, volumes, relays, directory: Path, *,
                 memory_bytes=1024**3, startup_timeout=60, images=None,
                 answer_probe=4, answer_silence=600, authority_holding=None):
        self.runtime, self.admission = runtime, admission
        self.volumes, self.relays = volumes, relays
        self.directory = Path(directory)
        # N10: lifted authority waits here for the broker. Production passes
        # host-private persistent storage, so a lift survives a host restart.
        self.authority_holding = Path(authority_holding or self.directory / "authority-import")
        self.memory_bytes, self.startup_timeout = memory_bytes, startup_timeout
        # A wake retries a silent box's status for answer_probe seconds. It is
        # replaced only once nothing has answered for answer_silence seconds,
        # counted from the last good status (routine broker checks refresh it), so
        # a turn that stalls the app for less than that is never killed for it.
        self.answer_probe, self.answer_silence = answer_probe, answer_silence
        self.boxes: dict[int, Box] = {}
        self.condition = threading.Condition(threading.RLock())
        self.images = images

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

    def _start(self, box, deadline, *, launch=None):
        with self.volumes.operation(box.uid):
            try:
                return self._launch(box, deadline, launch=launch)
            except Exception:
                self.runtime.stop(box.uid)
                self.volumes.detach(box.uid)
                box.awake = False
                raise

    def _launch(self, box, deadline, *, launch=None):
        record = launch or box.launch
        if not record:
            raise BoxAuthError("broker launch authorization is missing")
        launch_options = {}
        from tinyassets.boxes.images import harness_lease

        lease = (harness_lease(self.images.directory) if self.images is not None
                 else contextlib.nullcontext())
        with lease:
            self._start_sandbox(box, record, launch_options)
        while True:
            if not self.runtime.alive(box.uid):
                raise BoxError("owner daemon exited during startup")
            try:
                status = self._rpc(box)
                if not status["draining"]:
                    box.started = box.last = box.answered = time.monotonic()
                    box.error = ""
                    return
            except (OSError, http.client.HTTPException):
                pass
            if time.monotonic() >= deadline:
                raise BoxError("owner app socket did not become ready")
            time.sleep(.05)

    def _start_sandbox(self, box, record, launch_options):
        """Image selection through the written bundle, under the harness lease."""
        if self.images is not None:
            image = self.images.current
            selected = self.images.directory / image
            current_link = self.images.directory / "harness-current"
            if not selected.is_dir() and current_link.is_symlink():
                image = current_link.resolve(strict=True).name
            if not self.images.compatible(image):
                raise BoxError("cutover boundary: restore with deploy/backup-restore.sh")
            launch_options["rootfs"] = self.images.path(image)
            box.image = image
        self.volumes.detach(box.uid)
        mount = self.volumes.attach(box.uid)
        self.volumes.grow(box.uid, max(record["quota"], self.volumes.size(box.uid)))
        volume_mount = mount.oci_mount()
        if record["uid"] != box.uid:
            raise BoxError("broker owner reservation changed")
        # N10: lifted before the sandbox exists, so the box never sees the files.
        box.authority = (self._lift_authority(box, mount)
                         if record.get("import_authority") else self._settle_authority(box))
        relay = self.relays.bind(box.owner, box.uid)
        box.secret = record["secret"]
        directory, secret = self._paths(box.uid, box.secret)
        # Mark execution started before touching runsc; uncertain cleanup stays visible.
        box.awake = True
        box.started = time.monotonic()
        if record.get("rewind"):
            launch_options["rewind"] = True
        self.runtime.start(box.uid, box.uid, relay=relay, memory_bytes=self.memory_bytes,
                           app_dir=directory, secret_file=secret,
                           volume_mount=volume_mount,
                           volume_generation=record["volume_generation"], **launch_options)

    def _held_authority(self, box):
        """Root-only holding area, one file per (uid, owner): never another owner's rows."""
        held = self.authority_holding
        held.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = held.lstat()
        if (held.is_symlink() or not held.is_dir() or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            raise BoxError("authority import directory is not host-private")
        key = hashlib.sha256(box.owner.encode()).hexdigest()[:32]
        return held, held / f"{box.uid}-{key}.json"

    def _lift_authority(self, box, mount):
        """Extract once; hold the rows until the broker stops asking (it settled).

        The volume's files are removed only after a lift with no error is held,
        so a failed or partial lift never loses the owner's old rules.
        """
        from tinyassets.boxes.authority_import import extract, remove

        held, path = self._held_authority(box)
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            remove(Path(mount.mountpoint))
            return {**payload, "held": True}
        payload = extract(Path(mount.mountpoint), held / "work")
        if payload["errors"]:
            return {**payload, "held": False}
        stage = held / f".{path.name}.stage"
        stage.unlink(missing_ok=True)
        fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage, path)
        remove(Path(mount.mountpoint))
        return {**payload, "held": False}

    def _settle_authority(self, box):
        _held, path = self._held_authority(box)
        path.unlink(missing_ok=True)
        return None

    def _failed(self, box, error):
        box.failures += 1
        box.error = str(error)
        log.error("owner daemon uid=%s failures=%s: %s", box.uid, box.failures, box.error)

    def _stop(self, box, *, retire=False):
        if box.awake:
            if retire:
                try:
                    self._rpc(box, "revoke")
                except (OSError, http.client.HTTPException):
                    pass  # A dead/unreachable ingress is still followed by a proven stop.
                self.runtime.stop(box.uid)
            else:
                self.runtime.shutdown(box.uid)
                self.volumes.detach(box.uid)
            box.awake = False
        box.secret = ""
        box.error, box.failures = "", 0
        (self.directory / "secrets" / str(box.uid)).unlink(missing_ok=True)
        (self.directory / str(box.uid) / "app.sock").unlink(missing_ok=True)

    def _status(self, box):
        status = self._rpc(box)
        box.answered = time.monotonic()
        return status

    def _answer(self, box):
        deadline = time.monotonic() + self.answer_probe
        while True:
            try:
                return self._status(box)
            except (OSError, http.client.HTTPException):
                if time.monotonic() >= deadline or not self.runtime.alive(box.uid):
                    return None
            time.sleep(.5)

    def _activity(self, box):
        try:
            status = self._status(box)
            box.last = max(box.last, time.monotonic() - status["idle_seconds"])
            return status
        except (OSError, http.client.HTTPException):
            pass  # Unknown status is never permission to evict; drain must still succeed.

    def _sleep(self, box, *, idle):
        try:
            status = self._rpc(box, "drain", idle_seconds=idle)
        except (OSError, http.client.HTTPException):
            return False  # Unknown status never authorizes idle eviction.
        if not status["draining"] or status["active"] or status["deleting"]:
            return False
        try:
            self._stop(box)
        except Exception as exc:
            self._failed(box, exc)
            return False  # Keep the uncertain sandbox's capacity reservation.
        return True

    def ensure_awake(self, owner, *, scheduled=False, launch=None):
        if type(scheduled) is not bool:
            raise ValueError("scheduled must be boolean")
        if launch is None:
            raise BoxAuthError("broker launch authorization is missing")
        record = launch
        uid = record["uid"]
        # One budget for the whole wake, a replacement's stop included, so it stays
        # inside the router's 70 s wake socket.
        deadline = time.monotonic() + self.startup_timeout
        with self.volumes.operation(uid, shared=True):
            with self.condition:
                box = self.boxes.get(uid)
            if box is not None and box.awake and self.runtime.alive(uid):
                box.launch = record
                if box.owner != owner:
                    raise BoxError("broker owner reservation changed")
                if box.error:
                    raise BoxError("owner sandbox has an unverified launch or stop")
                try:
                    status = self._status(box)
                except (OSError, http.client.HTTPException):
                    status = None  # Decided under the exclusive lock below.
                if status is not None:
                    if status["draining"]:
                        raise BoxError("owner daemon is still draining")
                    box.last = time.monotonic()
                    return uid
        with self.volumes.operation(uid):
            with self.condition:
                box = self.boxes.setdefault(uid, Box(owner, uid))
            box.launch = record
            if box.owner != owner:
                raise BoxError("broker owner reservation changed")
            if box.awake and self.runtime.alive(uid):
                if box.error:
                    raise BoxError("owner sandbox has an unverified launch or stop")
                status = self._answer(box)
                if status is not None:
                    if status["draining"]:
                        raise BoxError("owner daemon is still draining")
                    box.last = time.monotonic()
                    return uid
                if time.monotonic() - box.answered < self.answer_silence:
                    raise BoxError("owner daemon is not answering")
                # Running, but its own app has answered nothing for answer_silence:
                # it can neither serve nor drain, and nothing else ever replaces it.
                # Replace it like a crash; its volume is reattached as it stands.
                self.runtime.stop(uid)
                box.awake = False
                self._failed(box, BoxError("owner daemon stopped answering"))
            if box.awake:
                self.runtime.stop(uid)
                box.awake = False
                self._failed(box, BoxError("owner daemon crashed"))
            try:
                self._start(box, deadline, launch=record)
                return uid
            except Exception as exc:
                self._failed(box, exc)
                raise BoxError(f"owner daemon startup failed: {exc}") from exc

    def retire(self, owner, uid):
        self.runtime.box_id(uid)
        with self.volumes.operation(uid):
            box = self.boxes.get(uid)
            if box is not None and box.owner != owner:
                raise BoxError("retirement owner differs from broker reservation")
            if box is None:
                self.runtime.stop(uid)
            else:
                self._stop(box, retire=True)

    def withdraw(self, uid):
        """C5/C6: close work and drain before making old ingress unreachable."""
        with self.volumes.operation(uid):
            box = self.boxes.get(uid)
            if box is not None and box.awake and self.runtime.alive(uid):
                deadline = time.monotonic() + 20
                while self._rpc(box, "delete-admission")["active"]:
                    if time.monotonic() >= deadline:
                        raise BoxBusy("owner work did not drain before withdrawal")
                    time.sleep(.02)
            self.retire(box.owner if box else "", uid)
            self.relays.retire(uid)

    def destroy(self, uid):
        """Account retirement, unlike center retirement, erases the entire volume."""
        from tinyassets.boxes.destroy import socket_tree

        with self.volumes.operation(uid):
            box = self.boxes.get(uid)
            self.retire(box.owner if box else "", uid)
            self.volumes.delete(uid)
            self.relays.retire(uid)
            socket_tree(self.directory, uid)
            (self.directory / "secrets" / str(uid)).unlink(missing_ok=True)
            with self.condition:
                self.boxes.pop(uid, None)

    def status(self, uid):
        # A copy can hold this lock for hours. Refuse promptly so the broker
        # retries instead of leaving timed-out status threads queued behind it.
        with self.volumes.operation(uid, blocking=False, shared=True):
            box = self.boxes.get(uid)
            awake = bool(box and box.awake and self.runtime.alive(uid))
            activity = self._activity(box) if awake else None
            return {"uid": uid, "awake": awake,
                    "failures": box.failures if box else 0,
                    "error": box.error if box else "",
                    "idle_seconds": time.monotonic() - box.last if activity else None,
                    "active": activity["active"] if activity else None,
                    "draining": activity["draining"] if activity else None,
                    "uptime": time.monotonic() - box.started if awake else 0,
                    "memory_bytes": self.runtime.memory(uid) if awake else None,
                    "volume_size": self.volumes.size(uid) if awake else None}

    def maintain(self):
        """Observe exited processes; only the broker selects the next transition."""
        with self.condition:
            boxes = [box for box in self.boxes.values() if box.awake]
        for box in boxes:
            try:
                with self.volumes.operation(box.uid, blocking=False, shared=True):
                    crashed = box.awake and not self.runtime.alive(box.uid)
                if not crashed:
                    continue
                with self.volumes.operation(box.uid, blocking=False):
                    if box.awake and not self.runtime.alive(box.uid):
                        self.runtime.stop(box.uid)
                        box.awake = False
                        self._failed(box, BoxError("owner daemon crashed"))
            except BlockingIOError:
                continue

    def close(self):
        with self.condition:
            boxes = list(self.boxes.values())
        for box in boxes:
            with self.volumes.operation(box.uid):
                if box.awake:
                    self._stop(box)
