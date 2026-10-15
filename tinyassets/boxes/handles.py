"""Host-owned box handles. No client payload may mint owner authority."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass

from tinyassets.boxes.provider import BoxAuthError, BoxOperationRefused, StaleHandle
from tinyassets.boxes.state import BoxHostState


@dataclass(frozen=True)
class OwnerGrant:
    """An admission returned by the broker's owner-session authority."""

    owner_id: str
    uid: int
    session_id: str


@dataclass(frozen=True)
class OwnerHandle:
    """An opaque host capability, bound to one owner, session and placement epoch."""

    token: str
    owner_id: str
    uid: int
    session_id: str
    epoch: int


class HostHandles:
    """Keep authority in boxhostd; verify every request before dispatch."""

    def __init__(self, state: BoxHostState) -> None:
        self._state = state
        self._lock = threading.Lock()
        self._by_token: dict[str, OwnerHandle] = {}

    def bind(self, grant: OwnerGrant) -> OwnerHandle:
        if (not isinstance(grant, OwnerGrant) or not grant.owner_id or not grant.session_id
                or type(grant.uid) is not int or not 300001 <= grant.uid <= 399999):
            raise BoxAuthError("a broker owner-session grant is required")
        handle = OwnerHandle(
            token=secrets.token_urlsafe(32), owner_id=grant.owner_id, uid=grant.uid,
            session_id=grant.session_id, epoch=self._state.epoch(grant.owner_id),
        )
        with self._lock:
            self._by_token[handle.token] = handle
        return handle

    def check(self, token: str, *, channel_owner: str, session_id: str,
              op_id: str) -> OwnerHandle:
        if not isinstance(token, str) or not token:
            raise BoxAuthError("missing box handle")
        with self._lock:
            handle = self._by_token.get(token)
        if handle is None:
            raise BoxAuthError("unknown box handle")
        if handle.owner_id != channel_owner or handle.session_id != session_id:
            raise BoxAuthError("box handle belongs to another owner or session")
        if not isinstance(op_id, str) or not op_id or len(op_id) > 200:
            raise BoxOperationRefused("a bounded operation id is required")
        if handle.epoch != self._state.epoch(handle.owner_id):
            raise StaleHandle("box handle is from a previous placement epoch")
        return handle

    def destroy(self, owner_id: str) -> int:
        """Call under the owner-wide deletion fence after all work is stopped."""
        return self._state.bump_epoch(owner_id)

    def close_session(self, owner_id: str, session_id: str) -> None:
        with self._lock:
            for token, handle in list(self._by_token.items()):
                if handle.owner_id == owner_id and handle.session_id == session_id:
                    del self._by_token[token]

    def detach(self, token: str, owner_id: str, session_id: str) -> None:
        with self._lock:
            handle = self._by_token.get(token)
            if (handle is not None
                    and (handle.owner_id, handle.session_id) == (owner_id, session_id)):
                del self._by_token[token]
