"""Real box-only and stale-host coverage for platform owner readers."""

from __future__ import annotations

import os
import sys

import pytest

pytestmark = [pytest.mark.role_split,
              pytest.mark.skipif(os.name != "posix" or sys.platform == "win32",
                                 reason="LocalBoxProvider needs POSIX")]


@pytest.fixture
def home(tmp_path, monkeypatch):
    from tinyassets.boxes.local import LocalBoxProvider

    data = tmp_path / "data"
    data.mkdir()
    center = data / "cc-a"
    center.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data))
    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda root, *, center: "acct-a")
    provider = LocalBoxProvider(boxes_root=tmp_path / "boxes",
                                state_dir=tmp_path / "state",
                                owner_of=lambda _center: "acct-a", allow_unisolated=True)
    handle = provider.bind("cc-a", account_id="acct-a")
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: (provider, None))
    from tinyassets.daemon_server import _connect, initialize_author_server
    from tinyassets.universe_owner import record_creation

    initialize_author_server(data)
    with _connect(data) as conn:
        record_creation(conn, universe_id=center.name, owner_id="acct-a")
    counter = 0

    def put(rel, data):
        nonlocal counter
        counter += 1
        provider.write(handle, f"put-{counter}", f"/cc/{rel}", data,
                       max_bytes=max(1, len(data)))

    yield center, put
    provider.close()


def test_owner_file_browser_reads_box(home, monkeypatch):
    from tinyassets.api import universe_file_reads as reads

    center, put = home
    put("notes/a.txt", b"box copy")
    (center / "notes").mkdir()
    (center / "notes" / "stale.txt").write_bytes(b"stale host copy")
    monkeypatch.setattr(reads, "_owner_universe",
                        lambda _uid: (center.name, center, "acct-a"))
    listing = reads.list_files(universe_id=center.name, path="notes")
    assert [item["name"] for item in listing["entries"]] == ["a.txt"]
    assert reads.read_whole_file(universe_id=center.name, path="notes/a.txt") == b"box copy"
    assert reads.read_file(universe_id=center.name, path="notes/a.txt")["text"] == "box copy"
    assert reads.read_whole_file(universe_id=center.name, path="notes/stale.txt") is None
    assert reads.read_file(universe_id=center.name, path="notes/stale.txt")["error"] == "not_found"


@pytest.mark.parametrize("relpath", ["status.json", "soul.md"])
@pytest.mark.parametrize("session", [False, True])
def test_linked_center_refused_before_any_box_bind_or_read(home, monkeypatch, relpath, session):
    from tinyassets.boxes.provider import BoxHandle, ExecLimits
    from tinyassets.control_plane.binding import SessionBinding, use_binding
    from tinyassets.owner_box_reads import box_exists, box_read

    center, put = home
    put(relpath, b"B secret")
    alias = center.parent / "cc-alias"
    alias.symlink_to(center, target_is_directory=True)
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: pytest.fail("linked center reached provider"))
    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda *a, **k: pytest.fail("linked center reached admission"))
    binding = SessionBinding(None, BoxHandle(alias.name, "acct-a", 1, "test"),
                             ExecLimits(), alias) if session else None
    with use_binding(binding):
        with pytest.raises(OSError):
            box_read(alias, relpath, max_bytes=100)
        with pytest.raises(OSError):
            box_exists(alias, relpath)


@pytest.mark.parametrize("action,text", [("control_daemon", "pause"),
                                        ("control_daemon", "status"),
                                        ("read_premise", ""), ("inspect", "")])
def test_linked_center_control_daemon_never_writes_target(home, monkeypatch, action, text):
    from tinyassets.api import universe as api
    from tinyassets.center_platform_files import set_paused

    center, _put = home
    alias = center.parent / "cc-alias"
    alias.symlink_to(center, target_is_directory=True)
    authorized = []

    def allows(uid, **kwargs):
        authorized.append(uid)
        return uid == alias.name

    monkeypatch.setattr(api.permissions, "universe_access_allows", allows)
    monkeypatch.setattr(api, "_dispatch_scope_error", lambda *a, **k: None)
    monkeypatch.setattr("tinyassets.agent_loop.served_chat.configured_box_provider",
                        lambda: pytest.fail("authorized alias reached B's box"))
    with pytest.raises(ValueError, match="linked center"):
        api._universe_impl(action, universe_id=alias.name, text=text)
    assert authorized == [alias.name]
    with pytest.raises(OSError):
        set_paused(alias, "must not reach B")
    assert not (center / ".pause").exists()
    assert alias.is_symlink()


@pytest.mark.parametrize("present", [False, True])
def test_read_page_without_binding_reads_box_and_never_scaffolds(home, monkeypatch, present):
    import json

    from tinyassets.api import visibility
    from tinyassets.control_plane.binding import current_binding
    from tinyassets.universe_server import read_page

    center, put = home
    if present:
        put("wiki/pages/notes/box.md", b"# Box page\nReadable without a session")
    monkeypatch.setattr(visibility, "visibility_permits", lambda *a: True)
    monkeypatch.setattr("tinyassets.api.wiki._ensure_wiki_scaffold",
                        lambda *a, **k: pytest.fail("read attempted scaffolding"))
    from tinyassets.agent_loop.served_chat import configured_box_provider

    provider, _ = configured_box_provider()
    monkeypatch.setattr(provider, "write", lambda *a, **k: pytest.fail("read wrote box"))
    before = sorted(p.relative_to(center) for p in center.rglob("*"))
    assert current_binding() is None
    result = json.loads(read_page(page="pages/notes/box.md", command_center_id=center.name))
    if present:
        assert "error" not in result
        assert "Readable without a session" in result["content"]
    else:
        assert "not found" in result["error"].lower()
        assert "scaffold" not in result["error"].lower()
    assert sorted(p.relative_to(center) for p in center.rglob("*")) == before


@pytest.mark.parametrize("refused", [False, True])
def test_inspect_notes_use_box_and_isolate_unavailable_field(admitted_home, monkeypatch, refused):
    import json

    from tinyassets.api import universe as api
    from tinyassets.api import visibility
    from tinyassets.control_plane.binding import use_binding
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.owner_box_reads import box_read

    center, put = admitted_home
    set_founder_home(center.parent, founder_sub="acct-a", universe_id=center.name)
    put("notes.json", b'[{"text":"box note"}]')
    (center / "notes.json").write_text('[{"text":"stale host note"}]')
    # The notes reader itself needs no session; inspect also has unrelated
    # legacy reads which still use the admitted session.
    with use_binding(None):
        assert api._box_json(center, "notes.json") == [{"text": "box note"}]
    monkeypatch.setattr(visibility, "visibility_permits", lambda *a: True)
    monkeypatch.setattr(visibility, "declared_level_name", lambda *a: "public")
    if refused:
        def read(root, path, **kwargs):
            if path == "notes.json":
                raise PermissionError("notes box unavailable")
            return box_read(root, path, **kwargs)
        monkeypatch.setattr("tinyassets.owner_box_reads.box_read", read)
        with pytest.raises(OSError, match="notes box unavailable"):
            api._box_json(center, "notes.json")
    result = json.loads(api._action_inspect_universe(center.name))
    assert result.get("universe_id") == center.name, result
    assert "daemon" in result and "cross_surface_hint" in result
    assert "stale host note" not in json.dumps(result)
    if refused:
        assert result["recent_notes"] is None
        assert "notes box unavailable" in result["unavailable_reason"]["recent_notes"]
    else:
        assert result["recent_notes"][0]["text"] == "box note"


def test_automation_brain_reads_box(home):
    from tinyassets.automation_context import _brain

    center, put = home
    put("identity.md", b"box identity")
    (center / "founder.md").write_text("stale host founder")
    result = _brain(center)
    assert result["identity.md"]["text"] == "box identity"
    assert result["founder.md"] == {"available": False}


def test_package_stat_and_publish_walk_read_box(home, monkeypatch):
    from tinyassets.boxes.provider import BoxAuthError
    from tinyassets.command_center_packages import _blocked, _exists, plan_install, walk

    center, put = home
    put("agents/main/identity.md", b"box agent")
    (center / "agents").mkdir()
    (center / "agents" / "stale.md").write_text("stale")
    assert _exists(center, "agents/main/identity.md")
    assert not _exists(center, "agents/stale.md")
    assert not _blocked(center, "agents/main/identity.md")
    assert ("agents/main/identity.md", "file") in set(walk(center))
    assert ("agents/stale.md", "file") not in set(walk(center))
    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda root, *, center: "another-owner")
    with pytest.raises(BoxAuthError):
        plan_install(center, {"name": "package"}, {})


def test_install_plan_refuses_blocked_agent_parent(home):
    from tinyassets.command_center_packages import PackageError, plan_install

    center, put = home
    put("agents", b"occupied by a file")
    with pytest.raises(PackageError, match="plain box directory"):
        plan_install(center, {"name": "package"}, {})


def test_config_presence_and_parse_read_box(home):
    from tinyassets.api.universe import _config_yaml_is_parseable
    from tinyassets.config import load_universe_config
    from tinyassets.owner_box_reads import box_exists

    center, put = home
    (center / "config.yaml").write_text("broken: [")
    assert not box_exists(center, "config.yaml")
    (center / "config.yaml").unlink()
    put("config.yaml", b"engine_source: self_hosted_endpoint\n")
    assert box_exists(center, "config.yaml")
    assert _config_yaml_is_parseable(center)
    assert load_universe_config(center).engine_source == "self_hosted_endpoint"


def test_config_callers_ignore_stale_host_setup(home, monkeypatch):
    from tinyassets.api.universe import universe_has_assigned_engine
    from tinyassets.onboarding.model_setup import model_setup_state

    center, put = home
    monkeypatch.setattr("tinyassets.onboarding.serving._require_current_admin",
                        lambda *a, **k: None)
    monkeypatch.setattr("tinyassets.provider_serving_binding.serving_connection_is_current",
                        lambda *a, **k: False)
    monkeypatch.setattr("tinyassets.providers.connection_lifecycle.intentionally_disconnected",
                        lambda *a, **k: False)
    monkeypatch.setattr("tinyassets.provider_assignment.load_provider_assignment",
                        lambda *a, **k: None)
    monkeypatch.setattr("tinyassets.providers.definition.list_definitions", lambda *a, **k: [])
    monkeypatch.setattr("tinyassets.commons_catalog.list_bindings", lambda *a, **k: [])
    monkeypatch.setattr("tinyassets.credential_vault.load_credential_vault", lambda *a, **k: [])
    (center / "config.yaml").write_text("broken: [")
    assert model_setup_state(center.parent, universe=center, uid=center.name,
                             owner="acct-a") == "empty"
    assert not universe_has_assigned_engine(center)
    (center / "config.yaml").unlink()
    put("config.yaml", b"engine_source: self_hosted_endpoint\n")
    assert model_setup_state(center.parent, universe=center, uid=center.name,
                             owner="acct-a") == "recovery"
    assert universe_has_assigned_engine(center)


def test_output_metrics_and_tree_read_box(home, monkeypatch):
    from tinyassets.api import universe as api
    from tinyassets.owner_box_reads import box_tree_size

    center, put = home
    put("output/book/chapter.md", b"one two three")
    (center / "output").mkdir()
    (center / "output" / "stale.md").write_text("host words")
    count, sample = api._compute_word_count_from_files(center)
    assert count == 3 and sample["source"] == "output_files"
    assert api._list_output_tree(center) == ["book/chapter.md"]
    assert box_tree_size(center, "output") == 13
    assert api._list_output_tree(center, max_depth=1) == []
    assert api._list_output_tree(center, max_depth=2) == ["book/chapter.md"]


def test_prompt_inventory_overlay_stat_reads_box(home):
    from tinyassets.universe_tools import _folder_section

    center, put = home
    put("notes/box.md", b"box note")
    put(".agent-workspace/notes/hidden.md", b"workspace note")
    put(".agent-workspace/report.txt", b"box workspace file")
    (center / "report.txt").write_text("stale host overlay")
    (center / "notes").mkdir()
    (center / "notes" / "stale.md").write_text("host note")
    section = _folder_section(center)
    assert "box.md" in section
    assert "stale.md" not in section
    assert "report.txt" in section
    assert "hidden.md" not in section


@pytest.fixture
def admitted_home(home, monkeypatch):
    """Use real box storage; adapt center-relative session paths as the host channel does."""
    from tinyassets.agent_loop.served_chat import configured_box_provider
    from tinyassets.boxes.provider import ExecLimits
    from tinyassets.control_plane.binding import SessionBinding, use_binding

    center, put = home
    provider, _ = configured_box_provider()
    for operation, path_index in (("read", 1), ("write", 2), ("stat", 1), ("list", 1)):
        original = getattr(provider, operation)

        def call(*args, _original=original, _index=path_index, **kwargs):
            args = list(args)
            if not args[_index].startswith("/cc"):
                args[_index] = "/cc/" + args[_index]
            return _original(*args, **kwargs)

        monkeypatch.setattr(provider, operation, call)
    binding = SessionBinding(provider, provider.bind(center.name, account_id="acct-a",
                                                     turn_id="route-test"), ExecLimits(), center)
    with use_binding(binding):
        yield home


def test_soul_versions_allocate_from_box_only(admitted_home):
    from tinyassets.owner_box_reads import box_read
    from tinyassets.soul_edit import apply_soul_edit

    center, put = admitted_home
    put("soul.edit.md", b"## Governed files\n- `identity.md`\n")
    put("identity.md", b"---\ntype: Universe Identity\n---\n# Identity\nBefore\n")
    put("soul_versions/0007.md", b"box snapshot")
    (center / "soul_versions").mkdir()
    (center / "soul_versions" / "0099.md").write_text("stale host snapshot")
    result = apply_soul_edit(center, changes={"identity.md": "After\n"},
                             agent_id="main", source="founder", context="test edit")
    assert result["snapshot"] == "soul_versions/0008.md"
    assert b"After" in box_read(center, result["snapshot"], max_bytes=4096)
    assert not (center / "identity.md").exists()
    assert not (center / "soul_versions" / "0008.md").exists()


def test_soul_edit_rejects_linked_box_versions_before_writing(admitted_home, tmp_path):
    from tinyassets.owner_box_reads import box_read
    from tinyassets.soul_edit import SoulEditError, apply_soul_edit

    center, put = admitted_home
    before = b"---\ntype: Universe Identity\n---\n# Identity\nBefore\n"
    put("soul.edit.md", b"## Governed files\n- `identity.md`\n")
    put("identity.md", before)
    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / "boxes" / center.name / "soul_versions").symlink_to(external)
    with pytest.raises(SoulEditError, match="plain box directory"):
        apply_soul_edit(center, changes={"identity.md": "After\n"},
                        agent_id="main", source="founder", context="test edit")
    assert box_read(center, "identity.md", max_bytes=4096) == before
    assert not list(external.iterdir())


def test_wiki_destination_and_reconciliation_use_box(admitted_home, monkeypatch):
    from tinyassets.api import wiki as api
    from tinyassets.effectors import wiki_write_back as effector

    center, put = admitted_home
    destination = "pages/notes/box.md"
    put("wiki/" + destination, b"box page")
    stale = center / "wiki" / "pages" / "notes" / "stale.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale host page")
    monkeypatch.setattr(api, "_universe_dir", lambda _uid: center)
    assert api._wiki_root_for_universe(center.name) == center / "wiki"
    target, error = effector._resolve_target_page(center / "wiki", destination)
    assert not error and target == center / "wiki" / destination
    assert effector._resolve_target_page(center / "wiki", "pages/notes/stale.md")[0] is None
    effector._record_destination_marker(universe_dir=center, target=target,
                                       destination=destination, effect_key="box-effect")
    result = effector._reconcile_destination_marker(
        universe_dir=center, target=target, destination=destination,
        effect_key="box-effect")
    assert result["status"] == "succeeded"
    assert effector._reconcile_destination_marker(
        universe_dir=center, target=stale, destination=destination,
        effect_key="box-effect")["status"] == "unknown"


def test_canon_listing_sources_and_work_targets_use_box(home, monkeypatch):
    import json

    from tinyassets.api import universe as api
    from tinyassets.ingestion.canon_io import iter_canon_files, read_canon_bytes
    from tinyassets.work_targets import _manifest_entry_needs_synthesis

    center, put = home
    put("canon/box.md", b"box canon")
    put("canon/sources/box.txt", b"box source")
    (center / "canon" / "sources").mkdir(parents=True)
    (center / "canon" / "stale.md").write_text("stale canon")
    (center / "canon" / "sources" / "stale.txt").write_text("stale source")
    paths = list(iter_canon_files(center / "canon", recursive=True))
    assert [p.relative_to(center / "canon").as_posix() for p in paths] == [
        "box.md", "sources/box.txt"]
    assert read_canon_bytes(center / "canon", "box.md") == b"box canon"
    monkeypatch.setattr(api, "_request_universe", lambda _uid: center.name)
    monkeypatch.setattr(api, "_universe_dir", lambda _uid: center)
    canon = json.loads(api._action_list_canon(center.name))
    assert canon["canon_files"] == [{"filename": "box.md", "size_bytes": 9}]
    sources = json.loads(api._action_list_sources(center.name))["source_files"]
    assert [row["filename"] for row in sources] == ["box.txt"]
    assert _manifest_entry_needs_synthesis(center, "box.txt", {})
    assert not _manifest_entry_needs_synthesis(center, "stale.txt", {})
    assert not _manifest_entry_needs_synthesis(center, "bad", {"source_path": "../stale.md"})


def test_box_pages_and_read_bound(home):
    from tinyassets.owner_box_reads import box_list, box_read

    center, put = home
    for index in range(205):
        put(f"notes/{index:03d}.md", b"data")
    assert len(box_list(center, "notes")) == 205
    assert len(box_list(center, "notes", limit=201)) == 201
    with pytest.raises(OSError, match="bound"):
        box_read(center, "notes/000.md", max_bytes=3)


def test_a_malformed_box_dispatcher_config_falls_back_to_defaults(admitted_home):
    from tinyassets.control_plane.queue_policy import DispatcherConfig, load_dispatcher_config

    center, put = admitted_home
    put("dispatcher_config.yaml", b"a: &anchor 1\nb: *anchor\n")
    assert load_dispatcher_config(center) == DispatcherConfig()


def test_operational_files_read_box_and_ignore_host(admitted_home, monkeypatch):
    import json

    from tinyassets.api import universe as api
    from tinyassets.control_plane.queue_policy import load_dispatcher_config

    center, put = admitted_home
    put("dispatcher_config.yaml", b"allow_opportunistic: false\n")
    put("activity.log", b"box activity\n")
    put("status.json", b'{"current_phase": "box-phase"}')
    (center / "dispatcher_config.yaml").write_text("allow_opportunistic: true\n")
    (center / "activity.log").write_text("stale host activity\n")
    (center / "status.json").write_text('{"current_phase": "host-phase"}')
    monkeypatch.setattr(api, "_request_universe", lambda uid: center.name)
    monkeypatch.setattr(api, "_universe_dir", lambda uid: center)
    assert load_dispatcher_config(center).allow_opportunistic is False
    status = api._box_json(center, "status.json")
    assert status["current_phase"] == "box-phase"
    assert api._daemon_liveness(center, status)["phase"] == "box-phase"
    assert json.loads(api._action_get_activity(center.name))["lines"] == ["box activity"]
    assert api._file_based_last_activity(center, status) is not None


def test_listing_never_contacts_boxes_even_for_unadmitted_centers(home, monkeypatch):
    import json

    from tinyassets.api import universe as api
    from tinyassets.api import visibility
    from tinyassets.daemon_server import set_founder_home

    center, put = home
    other = center.parent / "unadmitted"
    other.mkdir()
    set_founder_home(center.parent, founder_sub="acct-a", universe_id=center.name)
    set_founder_home(center.parent, founder_sub="acct-b", universe_id=other.name)
    put("output/scene.md", b"one two three")
    monkeypatch.setattr(visibility, "visibility_permits", lambda *a: True)
    monkeypatch.setattr(visibility, "declared_level_name", lambda *a: "public")
    monkeypatch.setattr("tinyassets.broker.owner_identities.admitted_owner",
                        lambda *a, **k: pytest.fail("listing queried admission"))
    monkeypatch.setattr("tinyassets.owner_box_reads._access",
                        lambda *a, **k: pytest.fail("listing read a box"))
    rows = json.loads(api._action_list_universes())["universes"]
    assert [r["id"] for r in rows] == [center.name, other.name]
    for row in rows:
        for field in ("has_premise", "has_soul", "word_count", "phase", "phase_human",
                      "staleness", "last_activity_at"):
            assert row[field] is None
        assert "inspect/status" in row["unavailable_reason"]


def test_single_center_operational_refusals_raise(admitted_home, monkeypatch):
    from tinyassets.api import universe as api
    from tinyassets.control_plane.queue_policy import load_dispatcher_config

    center, _put = admitted_home

    def denied(*args, **kwargs):
        raise PermissionError("provider refused")

    monkeypatch.setattr("tinyassets.owner_box_reads._access", denied)
    for operation in (load_dispatcher_config, api._compute_word_count_from_files):
        with pytest.raises(PermissionError, match="provider refused"):
            operation(center)
    with pytest.raises(PermissionError, match="provider refused"):
        api._daemon_liveness(center, None)


def test_branch_dependency_soul_comes_from_box(home):
    from tinyassets.api.branches import _branch_dependents
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.universe_soul import UniverseSoul, render_soul_markdown

    center, put = home
    set_founder_home(center.parent, founder_sub="acct-a", universe_id=center.name)
    put("soul.md", render_soul_markdown(UniverseSoul(loop_branch_def_id="box-loop")).encode())
    (center / "soul.md").write_text(
        render_soul_markdown(UniverseSoul(loop_branch_def_id="stale-host-loop")))
    assert _branch_dependents(str(center.parent), branch_def_id="box-loop", actor="acct-a")[
        "universes"] == [center.name]
    assert _branch_dependents(str(center.parent), branch_def_id="stale-host-loop", actor="acct-a")[
        "universes"] == []
