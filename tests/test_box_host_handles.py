"""The host authenticates owner channel, session and epoch per operation."""

from __future__ import annotations

import os

import pytest

from tinyassets.boxes.handles import HostHandles, OwnerGrant
from tinyassets.boxes.provider import BoxAuthError, BoxOperationRefused, StaleHandle

pytestmark = pytest.mark.skipif(os.name != "posix", reason="box host state is Linux-only")


def test_foreign_channel_session_and_stale_epoch_are_refused(tmp_path):
    from tinyassets.boxes.state import BoxHostState

    state = BoxHostState(tmp_path / "boxhost.db")
    try:
        handles = HostHandles(state)
        a = handles.bind(OwnerGrant("owner-a", 300001, "session-a"))
        b = handles.bind(OwnerGrant("owner-b", 300002, "session-b"))
        assert handles.check(a.token, channel_owner="owner-a", session_id="session-a",
                             op_id="turn:1") == a
        with pytest.raises(BoxAuthError):
            handles.check(b.token, channel_owner="owner-a", session_id="session-b",
                          op_id="turn:2")
        with pytest.raises(BoxAuthError):
            handles.check(a.token, channel_owner="owner-a", session_id="session-b",
                          op_id="turn:3")
        with pytest.raises(BoxOperationRefused):
            handles.check(a.token, channel_owner="owner-a", session_id="session-a",
                          op_id="")
        handles.destroy("owner-a")
        with pytest.raises(StaleHandle):
            handles.check(a.token, channel_owner="owner-a", session_id="session-a",
                          op_id="turn:4")
        assert handles.check(b.token, channel_owner="owner-b", session_id="session-b",
                             op_id="turn:5") == b
    finally:
        state.close()
