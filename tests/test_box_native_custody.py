"""A native CLI resolves and refreshes its pin without a host center tree."""

import base64
import hashlib
import json

import pytest

from tinyassets.boxes.provider import (
    BoxAuthError,
    BoxHandle,
    FileWrite,
    Snapshot,
    WriteConflict,
)
from tinyassets.control_plane.binding import SessionBinding
from tinyassets.credential_vault import (
    _canonical_digest,
    _subscription_reference_digest,
    llm_subscription_credential_record,
)
from tinyassets.external_agents.home import box_custody, compare_and_swap, materialize
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore


class MemoryBox:
    def __init__(self, record, *, sidecar=None):
        self.generation = 1
        self.records = [record]
        self.sidecar = sidecar

    def committed_generation(self, handle):
        return self.generation

    def read_many(self, handle, paths, *, max_total):
        assert handle.account_id == "owner"
        if paths == ["/cc/.credentials/codex/auth.json"]:
            assert self.sidecar is not None and len(self.sidecar) <= max_total
            return Snapshot({paths[0]: self.sidecar}, (), self.generation)
        assert paths == ["/cc/.credential-vault.json"]
        body = json.dumps({"schema_version": 1, "credentials": self.records}).encode()
        assert len(body) <= max_total
        return Snapshot({paths[0]: body}, (), self.generation)

    def write(self, handle, op_id, path, data, *, max_bytes, mode, expect_generation):
        assert handle.account_id == "owner" and path == "/cc/.credential-vault.json"
        assert op_id.startswith("cli-refresh:") and len(data) <= max_bytes
        if expect_generation != self.generation:
            raise WriteConflict("newer owner write")
        self.records = json.loads(data)["credentials"]
        self.generation += 1
        return FileWrite(path, len(data), self.generation)


def _material(token):
    return json.dumps({"token": token}).encode()


def _digest(record, material):
    return _canonical_digest({
        "material_digest": "sha256:" + hashlib.sha256(material).hexdigest(),
        "record": record,
    })


@pytest.mark.parametrize("inline", [True, False])
def test_bound_cli_refresh_carries_pin_without_host_center(tmp_path, inline):
    center = tmp_path / "center"
    old = _material("old")
    record = llm_subscription_credential_record(
        service="codex", auth_json_b64=base64.b64encode(old).decode(),
    )
    if not inline:
        record.pop("auth_json_b64")
    provider = MemoryBox(record, sidecar=None if inline else old)
    binding = SessionBinding(provider, BoxHandle("center", "owner", 1, "session"),
                             None, center)
    digest = _digest(record, old)
    pin = _subscription_reference_digest(
        reference_id="ref", owner_user_id="owner", universe_id="center",
        service="codex", generation=1,
    )
    with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
        conn.execute("CREATE TABLE llm_credential_deposit_owners ("
                     "universe_id TEXT, service TEXT, owner_user_id TEXT)")
        conn.execute("INSERT INTO llm_credential_deposit_owners VALUES (?,?,?)",
                     ("center", "codex", "owner"))
        conn.execute("CREATE TABLE llm_credential_custody ("
                     "reference_id TEXT, owner_user_id TEXT, universe_id TEXT, "
                     "service TEXT, generation INTEGER, record_digest TEXT, "
                     "reference_digest TEXT)")
        conn.execute("INSERT INTO llm_credential_custody VALUES (?,?,?,?,?,?,?)",
                     ("ref", "owner", "center", "codex", 1, digest, pin))
        conn.commit()
    assert not center.exists()
    custody = box_custody(center, "owner", "codex", binding)
    auth = {"service": "codex", "files": ["auth.json"],
            "writeback": ["auth.json"]}
    assert materialize(custody, auth) == {"auth.json": old}
    refreshed = _material("refreshed")
    assert compare_and_swap(custody, auth, {"auth.json": refreshed})
    assert custody.reload().reference_digest == pin
    assert materialize(box_custody(center, "owner", "codex", binding), auth) == {
        "auth.json": refreshed,
    }
    provider.records[0]["auth_json_b64"] = base64.b64encode(_material("tampered")).decode()
    provider.generation += 1
    assert box_custody(center, "owner", "codex", binding) is None
    assert not center.exists()


def test_bound_cli_refuses_foreign_owner(tmp_path):
    center = tmp_path / "center"
    old = _material("old")
    record = llm_subscription_credential_record(
        service="codex", auth_json_b64=base64.b64encode(old).decode(),
    )
    provider = MemoryBox(record)
    binding = SessionBinding(provider, BoxHandle("center", "owner", 1, "session"),
                             None, center)
    assert not center.exists()
    with pytest.raises(BoxAuthError):
        box_custody(center, "other", "codex", binding)
