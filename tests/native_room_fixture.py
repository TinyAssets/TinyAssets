"""Fixture owner room implementing the documented adapter contract, no live data."""

import contextlib
import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def safe(path):
    path = Path(path)
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError("Links refused")
    if path.is_file() and path.stat().st_nlink != 1:
        raise ValueError("Links refused")
    return path


@contextlib.contextmanager
def locked(path):
    safe(path)
    with open(path, "a+b") as guard:
        if os.name == "nt":
            import msvcrt

            if guard.seek(0, 2) == 0:
                guard.write(b"0")
                guard.flush()
            guard.seek(0)
            msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(guard, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                guard.seek(0)
                msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(guard, fcntl.LOCK_UN)


def atomic(path, value):
    safe(path)
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


class Room:
    def __init__(self, home):
        self.home = Path(home)
        self.root = safe(self.home / "fixture-room")
        self.path = self.root / "room.json"

    def init(self):
        self.root.mkdir(parents=True)
        atomic(self.path, {"room_id": "fixture-room", "messages": []})

    def load(self):
        return json.loads(safe(self.path).read_text(encoding="utf-8"))

    def post(self, data, *, participant="you", source="owner_authenticated", reply=False):
        with locked(self.root / "write.lock"):
            doc = self.load()
            digest = hashlib.sha256(canonical(data).encode()).hexdigest()
            for message in doc["messages"]:
                if (
                    message["client_message_id"] == data["client_message_id"]
                    and message["participant"] == participant
                ):
                    if message["digest"] != digest:
                        raise ValueError("Conflicting idempotency key")
                    return self.receipt(message, True)
            parent = next((m for m in doc["messages"] if m["id"] == data.get("reply_to")), None)
            if reply and parent is None:
                raise ValueError("Foreign reply target")
            message = dict(
                data,
                id=uuid.uuid4().hex,
                seq=len(doc["messages"]) + 1,
                participant=participant,
                source=source,
                digest=digest,
                depth=parent["depth"] + 1 if parent else 0,
                kind="reply" if reply else "message",
                attribution_level="owner_relay" if reply else "owner_authenticated",
            )
            message.setdefault("reply_to", None)
            doc["messages"].append(message)
            atomic(self.path, doc)
            return self.receipt(message, False)

    @staticmethod
    def receipt(message, duplicate):
        return {
            "ok": True,
            "room_id": "fixture-room",
            "id": message["id"],
            "saved": not duplicate,
            "duplicate": duplicate,
            "operation": "reply" if message["kind"] == "reply" else "post",
        }


# Load exactly the owner-embedded program, with this fixture adapter.
SCRIPTS = Path(__file__).resolve().parents[1] / "tinyassets/skills/native-room-replies/scripts"


def load_scripts():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "native_reply_builder", SCRIPTS / "build_workflow.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    fixture_source = (
        Path(__file__)
        .read_text(encoding="utf-8")
        .split("# Load exactly the owner-embedded program")[0]
    )
    namespace = {}
    exec(compile(builder.program(fixture_source), "<fixture-native-program>", "exec"), namespace)
    return builder, namespace, fixture_source


builder, runtime, ROOM_SOURCE = load_scripts()
Dispatch = runtime["Dispatch"]
prepare = runtime["prepare"]
reply_payload = runtime["reply_payload"]
