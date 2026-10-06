"""Release consumer, owner notice and repeat-turn preservation on real stores."""
import pytest

from tinyassets.starter_release import owner_seed_view, prepare_center_starter, prepare_starter
from tinyassets.starter_seeds import seed_store


@pytest.fixture
def center(tmp_path, monkeypatch):
    from tinyassets.daemon_server import grant_universe_ownership

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    root = tmp_path / "u-starter"
    root.mkdir()
    grant_universe_ownership(tmp_path, universe_id=root.name, owner_id="alice")
    return root


def test_fresh_provision_delivers_once_and_never_reseeds_deletions(center):
    from tinyassets.storage.pending_requests import list_pending

    first = prepare_starter(center, owner_id="alice", center_id=center.name, fresh=True)
    assert (center / "AGENTS.md").exists()
    assert len(list_pending(center)) == 1
    (center / "AGENTS.md").unlink()
    (center / "starter/hooks.md").write_text("My replacement", encoding="utf-8")
    assert prepare_center_starter(center)["transaction_id"] == first["transaction_id"]
    assert not (center / "AGENTS.md").exists()
    assert (center / "starter/hooks.md").read_text() == "My replacement"
    assert len(list_pending(center)) == 1


def test_notice_survives_delivery_failure_and_replays(center, monkeypatch):
    from tinyassets.storage import pending_requests

    actual = pending_requests.create_request
    monkeypatch.setattr(pending_requests, "create_request", lambda *a, **kw: {"error": "offline"})
    with pytest.raises(OSError, match="notice"):
        prepare_center_starter(center)
    assert (center / "starter/hooks.md").exists()
    assert not owner_seed_view(center, owner_id="alice")[0]["delivered"]
    monkeypatch.setattr(pending_requests, "create_request", actual)
    prepare_center_starter(center)
    assert owner_seed_view(center, owner_id="alice")[0]["delivered"] == 1
    assert len(pending_requests.list_pending(center)) == 1


@pytest.mark.parametrize("body", ["", "My own operating instructions"])
def test_dormant_owner_files_preserved_and_candidates_adoptable(center, body):
    (center / "AGENTS.md").write_text(body, encoding="utf-8")
    prepare_center_starter(center)
    notice, = owner_seed_view(center, owner_id="alice")
    agents = next(p for p in notice["payload"]["paths"] if p["path"] == "AGENTS.md")
    assert (center / "AGENTS.md").read_text() == body
    assert agents["adoptable"] and "Finish authorized work" in agents["candidate_text"]
    assert bool(notice["payload"]["diagnostics"]) == (body == "")
    with seed_store(center, owner_id="alice", center_id=center.name) as seeds:
        seeds.adopt(notice["payload"]["transaction_id"], "AGENTS.md",
                    expected_hash=agents["current_hash"], request_key="use-stock")
    assert (center / "AGENTS.md").read_text() == agents["candidate_text"]


def test_owner_view_has_no_creation_side_effect_and_wrong_owner_refuses(center):
    assert owner_seed_view(center, owner_id="alice") == []
    assert not (center.parent / ".universe-sidecars").exists()
    for action in (lambda: owner_seed_view(center, owner_id="bob"),
                   lambda: prepare_starter(center, owner_id="bob", center_id=center.name)):
        with pytest.raises(PermissionError):
            action()
    assert not list(center.iterdir())


def test_failed_creation_archives_receipt_so_reused_id_can_be_provisioned(center):
    from tinyassets.starter_release import abandon_new_provision

    first = prepare_starter(center, owner_id="alice", center_id=center.name, fresh=True)
    with pytest.raises(PermissionError):
        abandon_new_provision(center, owner_id="bob")
    abandon_new_provision(center, owner_id="alice")
    assert owner_seed_view(center, owner_id="alice") == []
    archived = list((center.parent / ".universe-sidecars").glob(".failed-provision-*"))
    assert len(archived) == 1
    assert (archived[0] / "starter-seeds.sqlite3").is_file()
    (center / "AGENTS.md").unlink()  # The creation handler rolls back the root.
    second = prepare_starter(center, owner_id="alice", center_id=center.name, fresh=True)
    assert second["transaction_id"] != first["transaction_id"]
    assert (center / "AGENTS.md").exists()
