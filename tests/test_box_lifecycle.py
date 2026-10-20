"""Center membership is written inside the startup-admitted owner box."""
import os
from types import SimpleNamespace

from tinyassets.boxes.lifecycle import admit_center, retire_center
from tinyassets.owner_centers import Centers


def test_local_membership_never_calls_shared_admission(tmp_path, monkeypatch):
    from tinyassets import owner_runtime

    def forbidden(*args, **kwargs):
        raise AssertionError("local center operations must not call shared admission")

    monkeypatch.setattr("tinyassets.owner_channel.request", forbidden)
    monkeypatch.setattr(owner_runtime, "_runtime", SimpleNamespace(
        root=tmp_path, principal="alice", pid=os.getpid()))
    assert not hasattr(__import__("tinyassets.agent_loop.served_chat", fromlist=["x"]),
                       "configured_box_provider")
    assert admit_center(tmp_path, principal="alice", center="cc-a") is None
    assert Centers(tmp_path).active() == ["cc-a"]
    retire_center(tmp_path, center="cc-a")
    assert Centers(tmp_path).active() == []
    assert {p.name for p in tmp_path.iterdir()} == {".centers.db"}
