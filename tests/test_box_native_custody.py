"""An admitted CLI leases platform credential bytes, never guest files."""

import base64
import hashlib
import json

import pytest

from tinyassets.boxes.provider import BoxAuthError, BoxHandle
from tinyassets.control_plane.binding import SessionBinding
from tinyassets.credential_vault import (
    _canonical_digest,
    _subscription_reference_digest,
    credential_vault_path,
    llm_subscription_credential_record,
)
from tinyassets.external_agents.home import box_custody, compare_and_swap, materialize
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore


class NoVaultBox:
    def read_many(self, *args, **kwargs):
        raise AssertionError("credential bytes must never be read from the guest")

    def write(self, *args, **kwargs):
        raise AssertionError("credential bytes must never be written to the guest")


def _material(token):
    return json.dumps({"token": token}).encode()


def _digest(record, material):
    return _canonical_digest({
        "material_digest": "sha256:" + hashlib.sha256(material).hexdigest(),
        "record": record,
    })


@pytest.mark.parametrize("inline", [True, False])
def test_bound_cli_refresh_keeps_vault_outside_guest(tmp_path, monkeypatch, inline):
    from tinyassets import storage_layout

    monkeypatch.setattr(storage_layout, "ACTIVE_BOX_LAYOUT", "owner-box")
    center = tmp_path / "center"
    old = _material("old")
    record = llm_subscription_credential_record(
        service="codex", auth_json_b64=base64.b64encode(old).decode(),
    )
    if not inline:
        record.pop("auth_json_b64")
    private = credential_vault_path(center)
    private.parent.mkdir(parents=True)
    private.parent.parent.chmod(0o710)
    private.parent.chmod(0o710)
    private.write_text(json.dumps({"schema_version": 1, "credentials": [record]}))
    if not inline:
        sidecar = private.parent / ".credentials" / "codex" / "auth.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_bytes(old)
    binding = SessionBinding(NoVaultBox(), BoxHandle("center", "owner", 1, "session"),
                             None, center)
    pin = _subscription_reference_digest(
        reference_id="ref", owner_user_id="owner", universe_id="center",
        service="codex", generation=1,
    )
    with SQLiteProviderWorkAuthorityStore(tmp_path).connection() as conn:
        conn.execute("CREATE TABLE llm_credential_deposit_owners ("
                     "universe_id TEXT, service TEXT, owner_user_id TEXT, "
                     "UNIQUE (universe_id, service))")
        conn.execute("INSERT INTO llm_credential_deposit_owners VALUES (?,?,?)",
                     ("center", "codex", "owner"))
        conn.execute("CREATE TABLE llm_credential_custody ("
                     "reference_id TEXT, owner_user_id TEXT, universe_id TEXT, "
                     "service TEXT, generation INTEGER, record_digest TEXT, "
                     "reference_digest TEXT)")
        conn.execute("INSERT INTO llm_credential_custody VALUES (?,?,?,?,?,?,?)",
                     ("ref", "owner", "center", "codex", 1, _digest(record, old), pin))
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
    assert not center.exists()
    assert private.is_file()
    assert not (center / ".credential-vault.json").exists()
    record = json.loads(private.read_text())["credentials"][0]
    record["auth_json_b64"] = base64.b64encode(_material("tampered")).decode()
    private.write_text(json.dumps({"schema_version": 1, "credentials": [record]}))
    assert box_custody(center, "owner", "codex", binding) is None


def test_bound_cli_refuses_foreign_owner(tmp_path, monkeypatch):
    from tinyassets import storage_layout

    monkeypatch.setattr(storage_layout, "ACTIVE_BOX_LAYOUT", "owner-box")
    center = tmp_path / "center"
    binding = SessionBinding(NoVaultBox(), BoxHandle("center", "owner", 1, "session"),
                             None, center)
    with pytest.raises(BoxAuthError):
        box_custody(center, "other", "codex", binding)
