"""Owner definition files stay private through atomic publication and failure."""
import os
import stat

import pytest

from tinyassets.providers import definition

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX broker metadata permissions")


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets.broker.supervisor import get_supervisor
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path)
    ledger.create_connection(
        connection_id="connection", owner_user_id="alice", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST",),
        provider="http", destination="service", credential_ref="vault://http/test",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/chat",
                            "methods": ["POST"]}])
    ledger.grant_connection(grant_id="grant", connection_id="connection",
                            owner_user_id="alice")
    get_supervisor(tmp_path)  # Bootstrap before observing metadata-only replacements.
    return tmp_path


def register(model):
    return definition.register_definition(universe_id="alice", owner_user_id="alice",
                                          access_method="api_key_http", protocol="chat_messages",
                                          ref="grant", model=model)


def test_every_replacement_has_read_group_before_atomic_publication(base, monkeypatch):
    """The former shared-read boundary is now owner-only, before every replace."""
    original = os.replace
    published = []
    def replace(source, destination):
        info = os.stat(source)
        assert (info.st_uid, stat.S_IMODE(info.st_mode)) == (os.geteuid(), 0o600)
        published.append(info.st_ino)
        return original(source, destination)
    monkeypatch.setattr(os, "replace", replace)
    def refuse_group_assignment(*args):
        pytest.fail("owner definitions must never assign a broker-reader group")
    monkeypatch.setattr(os, "fchown", refuse_group_assignment)
    first, second = register("first"), register("second")
    assert len(published) == 2 and published[0] != published[1]
    assert {d.id for d in definition.list_definitions("alice")} == {first.id, second.id}
    assert stat.S_IMODE((base / "alice/provider_definitions.json").stat().st_mode) == 0o600
    from tinyassets.storage.outbound_connections import ConnectionLedger

    assert ConnectionLedger(base / ".broker/outbound.db").grant_requires_inference_usage("grant")


def test_failed_group_assignment_preserves_existing_metadata_and_removes_only_temp(
        base, monkeypatch):
    """The remaining metadata assignment is private mode; failure stays atomic."""
    register("retained")
    path = base / "alice/provider_definitions.json"
    before = path.read_bytes(), path.stat()
    def refuse(*args):
        raise PermissionError("synthetic private mode assignment refusal")
    monkeypatch.setattr(os, "fchmod", refuse)
    with pytest.raises(PermissionError, match="synthetic private mode assignment refusal"):
        register("not-published")
    assert path.read_bytes() == before[0]
    assert os.path.samestat(path.stat(), before[1])
    assert list(path.parent.iterdir()) == [path]
