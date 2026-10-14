"""Runtime settings cannot turn malformed files or package hints into grants."""

import base64
import hashlib
import json
import os
import subprocess
import time
from dataclasses import FrozenInstanceError

import pytest

from tinyassets.agent_definition import (
    LocalModelBindingRequired,
    SettingsError,
    parse_settings,
    read_settings,
)
from tinyassets.providers.model_preferences import ModelPreferences


def test_absent_sparse_and_empty_are_distinct():
    absent = parse_settings(None)
    sparse = parse_settings(b"schema_version: 1\n")
    empty = parse_settings(b"schema_version: 1\ntools: {allow: []}\n"
                           b"skills: {enabled: []}\nextensions: {enabled: []}\n")
    assert absent.revision == "absent"
    assert absent.tools is sparse.tools is None
    assert empty.tools == empty.skills == empty.extensions == ()
    assert sparse.starter_hooks is True
    assert sparse.model_choice() is None


@pytest.mark.parametrize("raw", [
    b"", b"null", b"[]", b"schema_version: 1\nowner: someone",
    b"schema_version: true", b"schema_version: 2", b"schema_version: 1.0",
    b"schema_version: 1\nschema_version: 1",
    b"schema_version: 1\nmodel: {connection: a, connection: b, id: m}",
    b"schema_version: 1\nmodel: old", b"model: old\ntools: {allow: []}",
    b"schema_version: 1\nmodel: {connection: a, id: m, secret: key}",
    b"schema_version: 1\nstarter: {hooks: 1}",
    b"schema_version: 1\ntools: {allow: null}",
    b"schema_version: 1\nextensions: {enabled: [a, a]}",
    b"schema_version: 1\nskills: {enabled: [../other]}",
    b"schema_version: 1\nskills: {enabled: ['/other']}",
    b"schema_version: 1\nloop: {retry: {attempts: true}}",
    b"schema_version: 1\nloop: {retry: {attempts: -1}}",
    b"schema_version: 1\nloop: {retry: {attempts: 1.5}}",
    b"schema_version: 1\nloop: {retry: {backoff_seconds: .inf}}",
    b"schema_version: 1\nloop: {retry: {backoff_seconds: .nan}}",
    b"schema_version: 1\nloop: {retry: {unknown: 1}}",
    b"schema_version: 1\nloop: &a {}", b"!!python/object:evil {}",
    b"schema_version: [", b"\xff",
    pytest.param(b"#" * (64 * 1024 + 1), id="oversized"),
    pytest.param(b"schema_version: 1\nloop: {retry: {attempts: " + b"9" * 5000 + b"}}",
                 id="oversized-integer"),
    b"schema_version: 1\nmodel: 2026-99-99",
])
def test_invalid_settings_never_fall_back(raw):
    with pytest.raises(SettingsError):
        parse_settings(raw)


def test_snapshot_pins_exact_bytes_and_order():
    raw = (b"schema_version: 1\nextensions: {enabled: [second, first]}\n"
           b"starter: {hooks: false}\nloop: {retry: {attempts: 0, backoff_seconds: 0.5}, "
           b"compaction: {reserve_tokens: 20, trigger_tokens: 100}}\n")
    snap = parse_settings(raw)
    assert snap.revision == hashlib.sha256(raw).hexdigest()
    assert snap.raw == raw
    assert snap.extensions == ("second", "first")
    assert snap.starter_hooks is False
    assert snap.loop.attempts == 0
    assert snap.loop.reserve_tokens == 20
    with pytest.raises(FrozenInstanceError):
        snap.starter_hooks = True


def test_model_is_exact_candidate_without_fallback_or_authority():
    snap = parse_settings(b"schema_version: 1\nmodel: {connection: local, id: m, effort: high}")
    choice = ModelPreferences.from_document(snap.model_choice())
    assert choice.mode == "explicit"
    assert choice.saved_default.connection_id == "local"
    assert choice.saved_default.model_id == "m"
    assert choice.fallbacks == ()
    assert choice.efforts[0].level == "high"
    explicit = {"chosen": "per run"}
    assert snap.model_choice(explicit) is explicit


def test_legacy_model_requires_local_binding_without_rewriting(tmp_path):
    path = tmp_path / "settings.yaml"
    raw = b"model: logical/model\n"
    path.write_bytes(raw)
    snap = read_settings(tmp_path)
    assert snap.legacy_model == "logical/model"
    with pytest.raises(LocalModelBindingRequired):
        snap.model_choice()
    assert path.read_bytes() == raw
    assert snap.model_choice({"explicit": "local"}) == {"explicit": "local"}


@pytest.mark.parametrize("path", ["settings.yaml", "agents/worker/settings.yaml"])
def test_packages_export_model_need_without_source_connection(path):
    from tinyassets.command_center_packages import classify, model_need

    raw = b"schema_version: 1\nmodel: {connection: source-local, id: model}\ntools: {allow: []}"
    exported, reason = classify(path, raw, exclude=[], memory_items={})
    assert not reason
    assert b"source-local" not in exported
    snap = parse_settings(exported)
    assert snap.tools == ()
    assert snap.model.id == "model"
    with pytest.raises(LocalModelBindingRequired):
        snap.model_choice()
    assert model_need({"settings.yaml": exported}) == "model"


def test_package_import_cannot_supply_source_binding():
    from tinyassets.command_center_packages import FORMAT_VERSION, PackageError, check_blob

    raw = b"schema_version: 1\nmodel: {connection: source, id: m}"
    blob = json.dumps({
        "format_version": FORMAT_VERSION,
        "manifest": {"profile": "publish", "files": [{
            "path": "settings.yaml", "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
        }]},
        "files": {"settings.yaml": base64.b64encode(raw).decode()},
    }).encode()
    with pytest.raises(PackageError, match="recipient-local binding"):
        check_blob(blob)


def test_roster_settings_are_independent_and_edits_apply_next_snapshot(tmp_path):
    (tmp_path / "settings.yaml").write_text("schema_version: 1\ntools: {allow: []}")
    root = read_settings(tmp_path)
    assert read_settings(tmp_path, agent_slug="worker").tools is None
    agent = tmp_path / "agents" / "worker"
    agent.mkdir(parents=True)
    (agent / "settings.yaml").write_text("schema_version: 1\ntools: {allow: [read]}")
    assert read_settings(tmp_path, agent_slug="worker").tools == ("read",)
    (tmp_path / "settings.yaml").write_text("schema_version: 1\ntools: {allow: [bash]}")
    assert root.tools == ()
    assert read_settings(tmp_path).tools == ("bash",)


@pytest.mark.parametrize("slug", ["../other", "/other", "a/b", "a\\b", "C:drive", "."])
def test_agent_slug_cannot_escape_bound_root(tmp_path, slug):
    with pytest.raises(SettingsError):
        read_settings(tmp_path, agent_slug=slug)


def test_linked_settings_are_not_missing_defaults(tmp_path):
    target = tmp_path / "other"
    target.mkdir()
    (target / "settings.yaml").write_text("schema_version: 1")
    agents = tmp_path / "agents"
    agents.mkdir()
    link = agents / "worker"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True,
                       capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SettingsError):
        read_settings(tmp_path, agent_slug="worker")


# --------------------------------------------------------------------------- #
# An agent is a directory (C8): the loader, the roster, resolution, migration.
# --------------------------------------------------------------------------- #


def _agent_dir(base, slug, *, instructions="Answer briefly.", settings=None):
    folder = base / "agents" / slug
    folder.mkdir(parents=True)
    if instructions is not None:
        (folder / "AGENTS.md").write_text(instructions, encoding="utf-8")
    if settings is not None:
        (folder / "settings.yaml").write_text(settings, encoding="utf-8")
    return folder


def test_agent_directories_are_the_roster_and_main_is_the_root(tmp_path):
    from tinyassets import addressed_agents
    from tinyassets.agent_definition import list_agent_files, load_agent

    (tmp_path / "u").mkdir()
    base = tmp_path / "u"
    _agent_dir(base, "scout")
    _agent_dir(base, "weaver", settings="schema_version: 1\nagent: {id: agent_binding_01X, "
                                        "name: Evidence Weaver}\n")
    _agent_dir(base, "sleeper", settings="schema_version: 1\nagent: {retired: true}\n")
    # A co-admin's own agent on this shared command center: theirs, not "o"'s.
    _agent_dir(base, "theirs", settings="schema_version: 1\nagent: {id: agent_binding_01P, "
                                        "name: Private, owner: p}\n")
    (base / "agents" / "notes.md").write_text("not an agent", encoding="utf-8")
    (base / "agents" / "empty").mkdir()

    assert [(a.slug, a.agent_id, a.name) for a in list_agent_files(base, owner="o")] == [
        ("scout", "scout", "scout"), ("weaver", "agent_binding_01X", "Evidence Weaver")]
    assert [a.slug for a in list_agent_files(base, owner="o", include_retired=True)] == [
        "scout", "sleeper", "weaver"]
    assert [a.slug for a in list_agent_files(base, owner="p")] == ["scout", "theirs", "weaver"]
    assert load_agent(base, "agent_binding_01X", owner="o").slug == "weaver"
    assert load_agent(base, "weaver", owner="o").agent_id == "agent_binding_01X"
    assert load_agent(base, "scout", owner="o").instructions == "Answer briefly."
    assert load_agent(base, "main", owner="o").slug == "main"
    assert load_agent(base, "theirs", owner="p").owner == "p"
    for missing in ("sleeper", "empty", "nobody", "theirs", "agent_binding_01P"):
        with pytest.raises(LookupError):
            load_agent(base, missing, owner="o")
    assert addressed_agents.roster(tmp_path, universe_id="u", owner="o") == [
        {"agent_id": "main", "name": "Your agent"},
        {"agent_id": "scout", "name": "scout"},
        {"agent_id": "agent_binding_01X", "name": "Evidence Weaver"}]
    assert {"agent_id": "agent_binding_01P", "name": "Private"} in addressed_agents.roster(
        tmp_path, universe_id="u", owner="p")
    resolved = addressed_agents.resolve(tmp_path, universe_id="u", owner="o",
                                        agent_id="agent_binding_01X")
    assert (resolved.agent_id, resolved.name, resolved.agent_slug) == (
        "agent_binding_01X", "Evidence Weaver", "weaver")
    assert addressed_agents.resolve(tmp_path, universe_id="u", owner="o", agent_id="main") is None
    assert addressed_agents.resolve(tmp_path, universe_id="u", owner="p",
                                    agent_id="agent_binding_01P").agent_slug == "theirs"
    for refused in ("sleeper", "theirs", "agent_binding_01P"):
        with pytest.raises(addressed_agents.AgentNotAddressable):
            addressed_agents.resolve(tmp_path, universe_id="u", owner="o", agent_id=refused)


def test_two_directories_claiming_one_id_do_not_answer(tmp_path):
    from tinyassets.agent_definition import load_agent

    _agent_dir(tmp_path, "a", settings="schema_version: 1\nagent: {id: shared}\n")
    _agent_dir(tmp_path, "b", settings="schema_version: 1\nagent: {id: shared}\n")
    with pytest.raises(LookupError, match="more than one"):
        load_agent(tmp_path, "shared", owner="o")
    assert load_agent(tmp_path, "a", owner="o").slug == "a"


@pytest.mark.parametrize("raw", [
    b"schema_version: 1\nagent: {id: main}", b"schema_version: 1\nagent: {id: 'a b'}",
    b"schema_version: 1\nagent: {retired: 1}", b"schema_version: 1\nagent: {creator: x}",
    b"schema_version: 1\nagent: {owner: 1}", b"schema_version: 1\nagent: {owner: ''}",
    b"schema_version: 1\nagent: {configuration: []}", b"schema_version: 1\nagent: text",
])
def test_invalid_agent_block_never_falls_back(raw):
    with pytest.raises(SettingsError):
        parse_settings(raw)


def test_malformed_agent_settings_fail_the_roster_rather_than_answer_with_defaults(tmp_path):
    from tinyassets.agent_definition import list_agent_files

    _agent_dir(tmp_path, "broken", settings="schema_version: 2\n")
    with pytest.raises(SettingsError):
        list_agent_files(tmp_path, owner="o")


def _sql_agent(base, owner, universe, name, *, instructions, author=None, status="configured",
               retired=False, provider_ref=None, **configuration):
    """One binding row of the retired agent store, in any of its kinds."""
    import sqlite3

    from tinyassets.commons_catalog import create_binding, db_path, publish_definition

    definition = publish_definition(base, author_id=author or owner, payload={
        "schema_version": 1, "name": name,
        "components": {"identity": {"kind": "soul", "config": (
            {"instructions": instructions} if instructions else {})}},
    })
    binding_id = create_binding(
        base, universe_id=universe, definition_id=definition["agent_definition_id"],
        created_by=owner, payload={"schema_version": 1, "name": name, **configuration},
    )["agent_binding_id"]
    with sqlite3.connect(db_path(base)) as conn:
        conn.execute("UPDATE agent_bindings SET status = ?, retired = ? WHERE agent_binding_id = ?",
                     (status, int(retired), binding_id))
        if provider_ref is not None:
            stored = json.loads(conn.execute(
                "SELECT configuration_json FROM agent_bindings WHERE agent_binding_id = ?",
                (binding_id,)).fetchone()[0])
            conn.execute("UPDATE agent_bindings SET configuration_json = ? "
                         "WHERE agent_binding_id = ?",
                         (json.dumps({**stored, "provider_ref": provider_ref}), binding_id))
    return binding_id


def _migration_fixture(tmp_path):
    """Every binding kind the retired store could hold, across two owners' homes."""
    from tinyassets.onboarding.serving import PLATFORM_DEFINITION_AUTHOR

    (tmp_path / "u-home").mkdir()
    (tmp_path / "u-bob").mkdir()
    alice, bob = "alice", "bob"
    kinds = {
        # Addressable on main: configured, neither the installation nor a turn consumer.
        "custom": _sql_agent(tmp_path, alice, "u-home", "Evidence Weaver",
                             instructions="Weigh every claim.", channels={"slack": "c1"}),
        "retired": _sql_agent(tmp_path, alice, "u-home", "Evidence Weaver",
                              instructions="Second.", retired=True),
        "no_instructions": _sql_agent(tmp_path, alice, "u-home", "Quiet", instructions=""),
        # A co-admin's agent on alice's home, and bob's agent on his own home.
        "co_admin": _sql_agent(tmp_path, bob, "u-home", "Private", instructions="Bob's."),
        "elsewhere": _sql_agent(tmp_path, bob, "u-bob", "Private", instructions="Bob's own."),
        # Never an agent: the serving provider binding, the conversation-design
        # installation, a turn consumer.
        "serving": _sql_agent(tmp_path, alice, "u-home", "Your agent", instructions="",
                              author=PLATFORM_DEFINITION_AUTHOR, role="writer",
                              status="serving", provider_ref="conn-1"),
        "installation": _sql_agent(tmp_path, alice, "u-home", "App experience",
                                   instructions="x", role="app_experience",
                                   turn_consumer={"version": 1, "state": "disabled"}),
        "consumer": _sql_agent(tmp_path, alice, "u-home", "Consumer", instructions="x",
                               turn_consumer={"version": 1, "state": "enabled"}),
        # Main's is_conversable resolved these configured bindings by id.
        "paused_provider": _sql_agent(tmp_path, alice, "u-home", "Your agent", instructions="",
                                      role="writer", provider_ref="conn-2"),
        "bootstrap": _sql_agent(tmp_path, bob, "u-bob", "Your agent", instructions="",
                                author=PLATFORM_DEFINITION_AUTHOR, role="writer"),
    }
    return alice, bob, kinds


def _backup(tmp_path):
    """A fresh full-volume snapshot with its digest beside it (deploy/backup.sh)."""
    archive = tmp_path / "backups" / "tinyassets-snapshot-2026-10-13T00-00-00Z.tar.gz"
    archive.parent.mkdir(exist_ok=True)
    archive.write_bytes(b"tar")
    archive.with_name(archive.name + ".sha256").write_text(f"{'0' * 64}  {archive.name}\n",
                                                          encoding="utf-8")
    return archive


def _marker_state(tmp_path):
    from tinyassets import storage_layout as layout

    marker = tmp_path / layout.MARKER
    return json.loads(marker.read_text(encoding="utf-8"))["state"] if marker.exists() else None


def _bindings(tmp_path):
    import sqlite3

    from tinyassets.commons_catalog import db_path

    with sqlite3.connect(db_path(tmp_path)) as conn:
        return {row[0] for row in conn.execute("SELECT agent_binding_id FROM agent_bindings")}


def test_migration_lands_every_addressable_binding_as_its_owners_directory(tmp_path):
    from tinyassets import addressed_agents
    from tinyassets import agent_file_migration as m
    from tinyassets.commons_catalog import get_binding, list_bindings
    from tinyassets.conversation_store import load_recent_readonly, record_turn

    alice, bob, kinds = _migration_fixture(tmp_path)
    session = addressed_agents.memory_session(alice, kinds["custom"])
    record_turn(tmp_path / "u-home", session, "founder", "Earlier question", ts=1)
    record_turn(tmp_path / "u-home", session, "universe", "Earlier answer", ts=2)
    backup = _backup(tmp_path)
    assert m.pending(tmp_path) == 7
    assert m.main(["check", str(tmp_path)]) == m.PENDING

    assert m.apply(tmp_path, backup=backup) == {
        "u-bob": ["agents/private", "agents/your-agent"],
        "u-home": ["agents/evidence-weaver", "agents/evidence-weaver-2", "agents/quiet",
                   "agents/private", "agents/your-agent"],
    }

    assert _marker_state(tmp_path) == "stable"
    home = tmp_path / "u-home"
    assert (home / "agents/evidence-weaver/AGENTS.md").read_text(encoding="utf-8") == (
        "Weigh every claim.\n")
    settings = read_settings(home, agent_slug="evidence-weaver")
    assert (settings.agent.id, settings.agent.name, settings.agent.owner,
            settings.agent.retired) == (kinds["custom"], "Evidence Weaver", alice, False)
    assert settings.agent.configuration == {"channels": {"slack": "c1"}}
    assert read_settings(home, agent_slug="evidence-weaver-2").agent.retired is True
    assert not (home / "agents/quiet/AGENTS.md").exists()
    assert read_settings(home, agent_slug="private").agent.owner == bob
    # Ownership is kept both ways on the shared home.
    assert addressed_agents.roster(tmp_path, universe_id="u-home", owner=alice) == [
        {"agent_id": "main", "name": "Your agent"},
        {"agent_id": kinds["custom"], "name": "Evidence Weaver"},
        {"agent_id": kinds["no_instructions"], "name": "Quiet"},
        {"agent_id": kinds["paused_provider"], "name": "Your agent"}]
    assert addressed_agents.roster(tmp_path, universe_id="u-home", owner=bob) == [
        {"agent_id": "main", "name": "Your agent"},
        {"agent_id": kinds["co_admin"], "name": "Private"}]
    for owner, foreign in ((alice, "co_admin"), (bob, "custom")):
        with pytest.raises(addressed_agents.AgentNotAddressable):
            addressed_agents.resolve(tmp_path, universe_id="u-home", owner=owner,
                                     agent_id=kinds[foreign])
    assert addressed_agents.resolve(
        tmp_path, universe_id="u-home", owner=alice, agent_id=kinds["custom"]).instructions == (
        "Weigh every claim.\n")
    assert addressed_agents.resolve(
        tmp_path, universe_id="u-home", owner=bob, agent_id=kinds["co_admin"]).agent_slug == (
        "private")
    assert [turn.text for turn in load_recent_readonly(home, session)] == [
        "Earlier question", "Earlier answer"]
    assert addressed_agents.roster(tmp_path, universe_id="u-bob", owner=bob)[1:] == [
        {"agent_id": kinds["elsewhere"], "name": "Private"},
        {"agent_id": kinds["bootstrap"], "name": "Your agent"}]
    # Only bindings main did not make addressable stay in SQL.
    remaining = {b["agent_binding_id"] for b in list_bindings(tmp_path, universe_id="u-home")}
    assert remaining == {kinds[k] for k in ("serving", "installation", "consumer")}
    assert get_binding(tmp_path, universe_id="u-bob", binding_id=kinds["bootstrap"]) is None
    assert get_binding(tmp_path, universe_id="u-home", binding_id=kinds["custom"]) is None

    # The migrated volume has nothing to move: a rerun is a no-op that touches
    # no file, and the deploy's probe says so.
    (home / "agents/evidence-weaver/AGENTS.md").write_text("Edited.\n", encoding="utf-8")
    assert m.apply(tmp_path, backup=backup) == {}
    assert m.main(["check", str(tmp_path)]) == 0
    assert (home / "agents/evidence-weaver/AGENTS.md").read_text(encoding="utf-8") == "Edited.\n"
    assert sorted(p.name for p in (home / "agents").iterdir()) == [
        "evidence-weaver", "evidence-weaver-2", "private", "quiet", "your-agent"]


def test_migration_keeps_an_unrelated_directory_of_the_same_name(tmp_path):
    from tinyassets.agent_file_migration import apply

    (tmp_path / "u").mkdir()
    _sql_agent(tmp_path, "alice", "u", "Scout", instructions="Mine.")
    _agent_dir(tmp_path / "u", "scout", instructions="Another agent entirely.")
    assert apply(tmp_path, backup=_backup(tmp_path)) == {"u": ["agents/scout-2"]}
    assert (tmp_path / "u" / "agents" / "scout" / "AGENTS.md").read_text(
        encoding="utf-8") == "Another agent entirely."


@pytest.mark.parametrize("failure", ["second_file", "delete", "sidecar", "stable_marker",
                                     "missing_center", "missing_backup", "stale_backup",
                                     "lock_held"])
def test_any_failure_leaves_sql_untouched_and_publishes_no_file(tmp_path, monkeypatch,
                                                                failure):
    """All or nothing: the rows stay, every file this run wrote is gone, and a
    marker left "migrating" sends the deploy to the restore path. The steps
    after the row deletions (the sidecar ownership, the prepared stable
    marker) fail before the commit like every other step."""
    import sqlite3

    from tinyassets import agent_file_migration as m
    from tinyassets import storage_layout as layout
    from tinyassets.commons_catalog import db_path

    _, _, kinds = _migration_fixture(tmp_path)
    before = _bindings(tmp_path)
    backup = _backup(tmp_path)
    expected_marker = "migrating"
    if failure == "second_file":
        real = m.write_universe_file
        calls = []

        def crash(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise OSError("disk full")
            return real(*args, **kwargs)

        monkeypatch.setattr(m, "write_universe_file", crash)
        error = OSError
    elif failure == "delete":
        with sqlite3.connect(db_path(tmp_path)) as conn:
            conn.execute("CREATE TRIGGER refuse BEFORE DELETE ON agent_bindings "
                         "BEGIN SELECT RAISE(ABORT, 'refused'); END")
        error = sqlite3.IntegrityError
    elif failure == "sidecar":
        def refuse(base):
            raise PermissionError("chown refused")

        monkeypatch.setattr(m, "_keep_sidecars_readable", refuse)
        error = PermissionError
    elif failure == "stable_marker":
        real_prepare = m._prepare_marker

        def refuse(base, document, state):
            if state == layout.STABLE:
                raise OSError("disk full")
            return real_prepare(base, document, state)

        monkeypatch.setattr(m, "_prepare_marker", refuse)
        error = OSError
    elif failure == "missing_center":
        _sql_agent(tmp_path, "alice", "u-gone", "Later", instructions="Later.")
        before = _bindings(tmp_path)
        error, expected_marker = FileNotFoundError, None  # refused while planning
    elif failure == "missing_backup":
        backup.unlink()
        error, expected_marker = FileNotFoundError, None
    elif failure == "stale_backup":
        old = time.time() - m.MAX_BACKUP_AGE_S - 1
        os.utime(backup, (old, old))
        error, expected_marker = ValueError, None
    else:
        layout.require_layout(tmp_path)  # the running daemon's lifetime shared lock
        error, expected_marker = RuntimeError, "stable"
    try:
        with pytest.raises(error):
            m.apply(tmp_path, backup=backup)
    finally:
        layout.release_for_tests()
    assert _bindings(tmp_path) == before
    assert not (tmp_path / "u-home" / "agents").exists()
    assert not (tmp_path / "u-bob" / "agents").exists()
    assert _marker_state(tmp_path) == expected_marker
    assert not list(tmp_path.glob(layout.MARKER + ".*.tmp")), "no prepared marker is left"
    if expected_marker == "migrating":
        with pytest.raises(layout.LayoutRefused, match="did not finish"):
            layout.check(tmp_path)


def test_a_fresh_installation_has_nothing_to_migrate(tmp_path):
    from tinyassets import agent_file_migration as m

    assert m.main(["check", str(tmp_path)]) == 0
    assert m.apply(tmp_path, backup=_backup(tmp_path)) == {}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["backups"]


@pytest.mark.skipif(os.name != "posix" or os.geteuid() != 0,
                    reason="owner labels need root and POSIX ACLs")
def test_migration_labels_files_on_an_owner_labelled_center_as_the_owner_cell_would(tmp_path):
    from tinyassets import agent_file_migration as m
    from tinyassets import role_center_admission as admission

    home = tmp_path / "u"
    home.mkdir()
    bid = _sql_agent(tmp_path, "alice", "u", "Scout", instructions="Mine.")
    machine = 300001
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.setxattr(fd, admission.ACCESS, admission.canonical_root_acl(machine))
        os.fchown(fd, 1001, machine)
        os.fchmod(fd, 0o750)
        assert admission.read_label(fd) == admission.canonical_label(machine)
    finally:
        os.close(fd)
    assert m.apply(tmp_path, backup=_backup(tmp_path)) == {"u": ["agents/scout"]}
    content = (machine, machine, 0o660, admission._acl(6, {1001: 7}, mask=6), None)
    for relpath, expected in (("agents", admission.seed_label(machine)),
                              ("agents/scout", admission.seed_label(machine)),
                              ("agents/scout/settings.yaml", content),
                              ("agents/scout/AGENTS.md", content)):
        fd = os.open(home / relpath, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            assert admission.read_label(fd) == expected, relpath
        finally:
            os.close(fd)
    assert read_settings(home, agent_slug="scout").agent.id == bid
    assert (home / "agents/scout/AGENTS.md").read_text(encoding="utf-8") == "Mine.\n"
