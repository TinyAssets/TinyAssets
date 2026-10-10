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


def test_previous_release_upgrades_stock_and_keeps_owner_customizations(center):
    from tinyassets.starter_manifest import SeedFile, SeedManifest
    from tinyassets.starter_release import starter_manifest

    current = starter_manifest()
    old = SeedManifest(current.bundle_id, '1', tuple(
        SeedFile(file.path, b'previous stock', file.predecessors, file.historically_seeded)
        for file in current.files))
    with seed_store(center, owner_id='alice', center_id=center.name) as seeds:
        seeds.install(old, fresh=True)
    customized = center / 'starter/hooks.md'
    customized.write_text('Owner instructions')
    result = prepare_center_starter(center)
    assert result['version'] == '2'
    assert (center / 'AGENTS.md').read_bytes() == next(
        file.content for file in current.files if file.path == 'AGENTS.md')
    assert customized.read_text() == 'Owner instructions'
    assert prepare_center_starter(center)['transaction_id'] == result['transaction_id']


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


def test_busy_owner_controls_defer_the_notice_not_the_turn(center, monkeypatch):
    """A turn's prepare must not fail because the owner's controls are held
    elsewhere at that instant; the outbox replays the notice next time."""
    from tinyassets.owner_control import ControlUnavailable
    from tinyassets.storage import pending_requests

    actual = pending_requests.create_request

    def busy(*args, **kwargs):
        raise ControlUnavailable("Owner controls are busy; retry the operation.")

    monkeypatch.setattr(pending_requests, "create_request", busy)
    assert prepare_center_starter(center)["transaction_id"]
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


@pytest.mark.parametrize("recorded_owner", ["alice", None])
def test_a_co_admin_or_unattributed_turn_is_not_refused_by_the_release(tmp_path, monkeypatch,
                                                                       recorded_owner):
    """The turn's principal is admitted by its own authority check; the release
    installs for the center's RECORDED owner (or nothing, when unattributed)."""
    from types import SimpleNamespace

    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator
    from tinyassets.daemon_server import grant_universe_access, grant_universe_ownership

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    root = tmp_path / "u-shared"
    root.mkdir()
    if recorded_owner:
        grant_universe_ownership(tmp_path, universe_id=root.name, owner_id=recorded_owner)
    grant_universe_access(tmp_path, universe_id=root.name, actor_id="bob",
                          permission="admin", granted_by="bob")
    turn = AgentTurnCoordinator.__new__(AgentTurnCoordinator)
    turn.owner = None
    turn.config = None
    turn.context = SimpleNamespace(universe_dir=root)
    turn.adapter = SimpleNamespace(check=lambda context, config: "bob")

    assert turn._check_scope() == "bob"
    assert (root / "starter/hooks.md").exists() == bool(recorded_owner)
    if recorded_owner:
        assert owner_seed_view(root, owner_id="alice")
