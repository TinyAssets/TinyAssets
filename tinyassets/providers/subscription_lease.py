"""Transient native HOME; durable state crosses only the admitted broker channel."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

MAX_STATE = 2 * 1024 * 1024


def tmpfs_root():
    # Both production /tmp and the oracle's /dev/shm are kernel tmpfs mounts.
    # Never fall back to a disk-backed temporary directory.
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    for candidate in ("/tmp", "/dev/shm"):
        for line in mounts:
            fields = line.split()
            if fields[4] == candidate and fields[fields.index("-") + 1] == "tmpfs":
                return Path(candidate)
    raise RuntimeError("native credentials require tmpfs")


@contextlib.contextmanager
def source_lock(runtime):
    with named_source_lock(runtime.home, runtime.source.id, runtime._local):
        yield


@contextlib.contextmanager
def named_source_lock(home, source_id, local):
    import fcntl

    if getattr(local, "locked", False):
        yield
        return
    key = hashlib.sha256(str(home).encode() + source_id.encode()).hexdigest()
    path = tmpfs_root() / ("ta-native-lock-" + str(os.getuid()) + "-" + key)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        local.locked = True
        yield
    finally:
        local.locked = False
        os.close(fd)


def custody(runtime, action, **fields):
    from tinyassets.owner_channel import request

    return request(runtime.owner.principal, {"op": "SUBSCRIPTION_CUSTODY", "document": {
        "action": action, "source": runtime.source.id, **fields}})


def state_path(root, name):
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()) or path.resolve() == root.resolve():
        raise PermissionError("native state leaves temporary HOME")
    if path.is_symlink():
        raise PermissionError("native state must be a regular file")
    return path


def capture(runtime, root):
    state, total = {}, 0
    for name in runtime.source.state["paths"]:
        path = state_path(root, name)
        if not path.exists():
            continue
        if not path.is_file() or path.stat().st_size > MAX_STATE:
            raise ValueError("invalid native credential file")
        with path.open("rb") as stream:
            data = stream.read(MAX_STATE + 1)
        total += len(data)
        if total > MAX_STATE:
            raise ValueError("native credential state exceeds bound")
        state[name] = base64.b64encode(data).decode()
    return state


def remove_legacy(runtime):
    for name in runtime.source.state["paths"]:
        state_path(runtime.home, name).unlink(missing_ok=True)


def migrate(runtime, entry):
    """One-shot bridge for existing live logins; remove with cutover code."""
    legacy = capture(runtime, runtime.home)
    if legacy:
        if not entry["state"]:
            entry = custody(runtime, "save", revision=entry["revision"], state=legacy)
        remove_legacy(runtime)  # Only after broker publication has acknowledged.
    declaration = runtime.home / ".ta-cli/sources" / (runtime.source.id + ".json")
    if declaration.is_file():
        document = json.loads(declaration.read_text())
        if document.get("state", {}).get("location") == "owner_box":
            from tinyassets.providers.source_record import SourceRecord

            document["state"]["location"] = "broker"
            if SourceRecord.parse(document) != runtime.source:
                raise PermissionError("source changed during custody migration")
            declaration.write_text(json.dumps(document, indent=2) + "\n")
    return entry


@contextlib.contextmanager
def lease(runtime, *, login=False, cancelled=None):
    if getattr(runtime._local, "home", None) is not None:
        yield
        return
    with runtime.lock():
        if cancelled is not None and cancelled.is_set():
            from tinyassets.providers.subscription_runtime import SubscriptionError

            raise SubscriptionError("native login cancelled")
        entry = custody(runtime, "read")
        if entry["revision"] is None:
            if not login and not capture(runtime, runtime.home):
                from tinyassets.providers.subscription_runtime import SubscriptionError

                raise SubscriptionError("subscription sign-in required")
            entry = custody(runtime, "begin")
        entry = migrate(runtime, entry)
        with tempfile.TemporaryDirectory(prefix="ta-native-", dir=tmpfs_root()) as directory:
            root = Path(directory)
            runtime._local.home = root
            runtime._local.login_committed = False
            runtime._local.revision = entry["revision"]
            ready = False
            try:
                for name, content in runtime.cli_login.get("files", {}).items():
                    target = state_path(root, ".ta-cli/" + runtime.source.id + "/config/" + name)
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    target.write_text(runtime.argv([content])[0])
                    target.chmod(0o600)
                for name, value in entry["state"].items():
                    if name not in runtime.source.state["paths"]:
                        raise PermissionError("native credential layout changed; reconnect")
                    target = state_path(root, name)
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    target.write_bytes(base64.b64decode(value, validate=True))
                    target.chmod(0o600)
                ready = True
                yield
            finally:
                try:
                    if ready and (not login or entry["state"]
                                  and not runtime._local.login_committed):
                        state = capture(runtime, root)
                        if state != entry["state"]:
                            custody(runtime, "save", revision=runtime._local.revision, state=state)
                    elif not runtime._local.login_committed and not entry["state"]:
                        custody(runtime, "delete", revision=entry["revision"])
                finally:
                    runtime._local.home = None
                    # TemporaryDirectory wipes files even when writeback fails.


def commit_login(runtime):
    entry = custody(runtime, "save", revision=runtime._local.revision,
                    state=capture(runtime, runtime._local.home))
    runtime._local.revision = entry["revision"]
    runtime._local.login_committed = True


def disconnect(owner, source_id):
    from tinyassets.owner_channel import request
    from tinyassets.providers.subscription_source import sources

    if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", source_id):
        raise ValueError("invalid subscription source id")
    with named_source_lock(owner.root, source_id, threading.local()):
        request(owner.principal, {"op": "SUBSCRIPTION_CUSTODY", "document": {
            "action": "delete", "source": source_id}})
        source = sources(owner.root).get(source_id)
        if source is not None:
            for name in source.state["paths"]:
                state_path(owner.root, name).unlink(missing_ok=True)
        directory = owner.root / ".ta-cli" / source_id
        if directory.is_symlink():
            directory.unlink()
        elif directory.exists():
            shutil.rmtree(directory)


def upgrade_connection(runtime, marker):
    """Preserve acceptance across the custody move and added dependency digest."""
    from tinyassets.providers.subscription_source import digest

    old = runtime.source.document()
    old["state"]["location"] = "owner_box"
    accepted = {digest(old)}
    old["sign_in"]["cli_login"]["install"].pop("lock_sha256", None)
    accepted.add(digest(old))
    old["state"]["location"] = "broker"
    accepted.add(digest(old))
    if marker.get("source") in accepted:
        marker["source"] = digest(runtime.source)
        (runtime.directory / "connection").write_text(json.dumps(marker))
