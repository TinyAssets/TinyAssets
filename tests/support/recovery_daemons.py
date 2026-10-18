"""Real destruction orchestration with disposable control/quota command peers."""
from types import SimpleNamespace

from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.owner_daemons import OwnerDaemons
from tinyassets.boxes.quota import OwnerQuota


def recovery_daemons(store, monkeypatch):
    from tinyassets.boxes import quota

    monkeypatch.setattr(OwnerQuota, "_mounted", lambda self: None)
    monkeypatch.setattr(quota, "subprocess", SimpleNamespace(run=lambda *a, **kw: None))
    directory = store.parent / "sockets"
    directory.mkdir(exist_ok=True)
    return OwnerDaemons(
        SimpleNamespace(box_id=GvisorRuntime.box_id, stop=lambda uid: None),
        SimpleNamespace(retire=lambda owner, uid: None), OwnerQuota(store),
        SimpleNamespace(retire=lambda uid: None), directory)
