"""Committed owner-box admission and deletion never enter the cell path."""

from tinyassets.boxes.lifecycle import admit_center


def test_committed_admission_uses_bound_relay_without_host_provisioning(tmp_path, monkeypatch):
    calls = []
    def request(center, document):
        calls.append((center, document))
        return {"generation": 17, "machine": 300001}
    def forbidden():
        raise AssertionError("an admitted box never provisions a host box")
    monkeypatch.setattr("tinyassets.owner_channel.request", request)
    assert not hasattr(__import__("tinyassets.agent_loop.served_chat", fromlist=["x"]),
                       "configured_box_provider")
    assert admit_center(tmp_path, principal="alice", center="cc-a") == 17
    assert calls == [("", {"op": "ADMIT_CENTER", "center": "cc-a"})]
