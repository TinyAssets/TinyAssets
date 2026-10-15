"""Host-side read-only broker admission and the daemon's permanent tombstone."""

from __future__ import annotations

import hashlib
import socket
import sqlite3
import struct
from pathlib import Path

from tinyassets import rpc_frames as rf
from tinyassets.boxes.handles import OwnerGrant
from tinyassets.boxes.provider import BoxAuthError
from tinyassets.broker.owner_identities import validate_center, validate_principal


class OwnerRegistry:
    def __init__(self, data_root: Path, broker_socket: Path) -> None:
        self.data_root, self.broker_socket = data_root, broker_socket

    def record(self, owner: str, center: str) -> dict:
        validate_principal(owner)
        validate_center(center)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect(str(self.broker_socket))
            _, uid, gid = struct.unpack("3i", sock.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if (uid, gid) != (1002, 1002):
                raise BoxAuthError("box admission peer is not the credential broker")
            sock.sendall(rf.control(rf.CONNECTION, {
                "op": "BOX_ADMISSION", "principal": owner, "center": center}))
            frame = rf.read_frame_blocking(sock)
            if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
                raise BoxAuthError("invalid box admission response")
            result = frame.control()
        if (set(result) != {"op", "uid", "quota"} or result["op"] != "BOX_ADMITTED"
                or type(result["uid"]) is not int or not 300001 <= result["uid"] <= 399999
                or type(result["quota"]) is not int or result["quota"] <= 0):
            raise BoxAuthError("broker refused owner box admission")
        return result

    def tombstoned(self, owner: str) -> bool:
        # Only this fixed platform table is read on the host. Inability to read
        # it is an error, never an empty tombstone set or permission to erase.
        path = self.data_root / ".tinyassets.db"
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            return db.execute("SELECT 1 FROM deleted_principals WHERE founder_sub=?",
                              (hashlib.sha256(owner.strip().encode()).hexdigest(),)).fetchone() \
                is not None

    def grant(self, owner: str, center: str, session: str, *, destroying=False) -> OwnerGrant:
        if not isinstance(session, str) or not 1 <= len(session) <= 200:
            raise BoxAuthError("a bounded admitted session is required")
        record = self.record(owner, center)
        if self.tombstoned(owner) != destroying:
            raise BoxAuthError("owner deletion admission does not match the tombstone")
        return OwnerGrant(owner, record["uid"], session)

    def authorize(self, grant: OwnerGrant, center: str, *, destroying=False) -> bool:
        current = self.grant(grant.owner_id, center, grant.session_id, destroying=destroying)
        return current == grant
