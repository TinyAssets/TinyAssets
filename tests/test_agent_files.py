"""Runtime settings cannot turn malformed files or package hints into grants."""

import base64
import hashlib
import json
import os
import subprocess
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
        # The main agent's provider binding while paused (``_reconnect_manifest``
        # and ``reconnect_owned_binding`` resume it by provider_ref) and before
        # model consent (``ensure_bootstrap_binding`` resumes the untouched
        # platform binding at revision 1): main let the owner address both as
        # an instruction-less "Your agent"; neither is an agent.
        "paused_provider": _sql_agent(tmp_path, alice, "u-home", "Your agent", instructions="",
                                      role="writer", provider_ref="conn-2"),
        "bootstrap": _sql_agent(tmp_path, bob, "u-bob", "Your agent", instructions="",
                                author=PLATFORM_DEFINITION_AUTHOR, role="writer"),
    }
    return alice, bob, kinds


def test_migration_lands_every_addressable_binding_as_its_owners_directory(tmp_path):
    from tinyassets import addressed_agents
    from tinyassets.agent_file_migration import LEDGER_NAME, migrate
    from tinyassets.commons_catalog import _agent_connect, get_binding, list_bindings
    from tinyassets.conversation_store import load_recent_readonly, record_turn

    alice, bob, kinds = _migration_fixture(tmp_path)
    session = addressed_agents.memory_session(alice, kinds["custom"])
    record_turn(tmp_path / "u-home", session, "founder", "Earlier question", ts=1)
    record_turn(tmp_path / "u-home", session, "universe", "Earlier answer", ts=2)

    assert migrate(tmp_path) == {
        "u-bob": ["agents/private"],
        "u-home": ["agents/evidence-weaver", "agents/evidence-weaver-2", "agents/quiet",
                   "agents/private"],
    }

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
        {"agent_id": kinds["no_instructions"], "name": "Quiet"}]
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
    assert [m.text for m in load_recent_readonly(home, session)] == [
        "Earlier question", "Earlier answer"]
    assert addressed_agents.roster(tmp_path, universe_id="u-bob", owner=bob)[1:] == [
        {"agent_id": kinds["elsewhere"], "name": "Private"}]
    # What is not an agent stays in SQL, where the provider paths still find it.
    remaining = {b["agent_binding_id"] for b in list_bindings(tmp_path, universe_id="u-home")}
    assert remaining == {kinds[k] for k in ("serving", "installation", "consumer",
                                            "paused_provider")}
    assert get_binding(tmp_path, universe_id="u-bob", binding_id=kinds["bootstrap"]) is not None
    assert get_binding(tmp_path, universe_id="u-home", binding_id=kinds["custom"]) is None
    # Recorded as done, with its backup: a later start reads nothing else.
    with _agent_connect(tmp_path) as conn:
        ledger = conn.execute("SELECT backup, detail_json FROM agent_file_migration "
                              "WHERE name = ?", (LEDGER_NAME,)).fetchone()
    backup = json.loads((tmp_path / ledger["backup"]).read_text(encoding="utf-8"))
    assert {b["agent_binding_id"] for b in backup["bindings"]} == {
        kinds[k] for k in ("custom", "retired", "no_instructions", "co_admin", "elsewhere")}
    assert json.loads(ledger["detail_json"])["u-bob"] == ["agents/private"]

    # Replay is a no-op and never touches an edited file.
    (home / "agents/evidence-weaver/AGENTS.md").write_text("Edited.\n", encoding="utf-8")
    assert migrate(tmp_path) == {}
    assert (home / "agents/evidence-weaver/AGENTS.md").read_text(encoding="utf-8") == "Edited.\n"


def test_an_interrupted_migration_resumes_by_id_with_one_directory_per_agent(tmp_path,
                                                                           monkeypatch):
    from tinyassets import agent_file_migration as m
    from tinyassets.commons_catalog import list_bindings

    alice, bob, kinds = _migration_fixture(tmp_path)
    home = tmp_path / "u-home"

    # Crash 1: mid-way through an agent's files (its claim written, its text not).
    writes = []
    real_write = m.write_universe_file

    def _failing_write(universe_dir, relpath, data, **kw):
        writes.append(relpath)
        # The backup, u-bob's agent (two files), evidence-weaver's settings, then...
        if len(writes) == 5:
            raise OSError("disk went away")
        return real_write(universe_dir, relpath, data, **kw)

    monkeypatch.setattr(m, "write_universe_file", _failing_write)
    with pytest.raises(OSError):
        m.migrate(tmp_path)
    monkeypatch.undo()
    assert writes[-1] == "agents/evidence-weaver/AGENTS.md"
    assert (home / "agents/evidence-weaver/settings.yaml").exists()
    assert not (home / "agents/evidence-weaver/AGENTS.md").exists()
    assert not (home / "agents/evidence-weaver-2").exists()
    assert len(list_bindings(tmp_path, universe_id="u-home", limit=None)) == 8

    # Crash 2: every file written, the commit never reached.
    monkeypatch.setattr(m, "_delete", lambda conn, ids: (_ for _ in ()).throw(
        RuntimeError("power cut before commit")))
    with pytest.raises(RuntimeError):
        m.migrate(tmp_path)
    monkeypatch.undo()
    assert len(list_bindings(tmp_path, universe_id="u-home", limit=None)) == 8

    # The next start finds every directory by its id: no second slug, no duplicate id.
    assert m.migrate(tmp_path) == {
        "u-bob": ["agents/private"],
        "u-home": ["agents/evidence-weaver", "agents/evidence-weaver-2", "agents/quiet",
                   "agents/private"],
    }
    assert sorted(p.name for p in (home / "agents").iterdir()) == [
        "evidence-weaver", "evidence-weaver-2", "private", "quiet"]
    ids = [read_settings(home, agent_slug=p.name).agent.id for p in (home / "agents").iterdir()]
    assert len(set(ids)) == 4
    assert {b["agent_binding_id"] for b in list_bindings(tmp_path, universe_id="u-home")} == {
        kinds[k] for k in ("serving", "installation", "consumer", "paused_provider")}
    assert m.migrate(tmp_path) == {}


def test_a_file_edited_after_an_interrupted_run_refuses_rather_than_overwrites(tmp_path,
                                                                            monkeypatch):
    from tinyassets import agent_file_migration as m
    from tinyassets.commons_catalog import get_binding

    (tmp_path / "u").mkdir()
    bid = _sql_agent(tmp_path, "alice", "u", "Scout", instructions="Mine.")
    monkeypatch.setattr(m, "_delete", lambda conn, ids: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(RuntimeError):
        m.migrate(tmp_path)
    monkeypatch.undo()
    (tmp_path / "u/agents/scout/AGENTS.md").write_text("Edited meanwhile.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="already edited or conflicts"):
        m.migrate(tmp_path)
    assert get_binding(tmp_path, universe_id="u", binding_id=bid) is not None
    assert (tmp_path / "u/agents/scout/AGENTS.md").read_text(encoding="utf-8") == (
        "Edited meanwhile.\n")


def test_the_daemon_runs_the_migration_before_it_serves(tmp_path):
    """``consumer_runtime.initialize`` is every serving path's storage setup."""
    from tinyassets.consumer_runtime import initialize

    (tmp_path / "u").mkdir()
    bid = _sql_agent(tmp_path, "alice", "u", "Scout", instructions="Mine.")
    initialize(tmp_path)
    assert read_settings(tmp_path / "u", agent_slug="scout").agent.id == bid
    initialize(tmp_path)  # once: the ledger row, then the process guard


def _installed_template_agent(base, owner, universe, name, *, instructions, slug):
    """A chat agent a package install bound from a public template (the old path).

    Its binding id is derived from the install pin, exactly as the retired
    template installer minted it, and the pin records which package directory
    the install landed.
    """
    import time

    from tinyassets.command_center_packages import _db, pin
    from tinyassets.commons_catalog import _agent_connect, publish_definition

    definition = publish_definition(base, author_id="publisher", payload={
        "schema_version": 1, "name": name,
        "components": {"identity": {"kind": "soul", "config": {"instructions": instructions}}},
    })
    key = "scout"
    record = {"action": {"plan": {
        "agent_templates": [{"key": key, "name": name,
                             "agent_definition_id": definition["agent_definition_id"]}],
        "placement": {"agent_slug": slug, "land": [{"path": "AGENTS.md",
                                                     "to": f"agents/{slug}/AGENTS.md"}]},
    }}}
    pin(base, universe_id=universe, kind="install", agent="main", digest="d", record=record,
        owner_id=owner)
    with _db(base) as conn:
        pin_id = conn.execute("SELECT pin_id FROM pins").fetchone()[0]
    encoded = json.dumps([universe, owner, pin_id, key], separators=(",", ":")).encode()
    bid = "agent_binding_" + hashlib.sha256(encoded).hexdigest()
    with _db(base) as conn:
        conn.execute("UPDATE pins SET state = 'activated', progress_json = ? WHERE pin_id = ?",
                     (json.dumps({"agents": {key: bid}, "files": [f"agents/{slug}/AGENTS.md"]}),
                      pin_id))
    with _agent_connect(base) as conn:
        conn.execute(
            "INSERT INTO agent_bindings (agent_binding_id, universe_id, agent_definition_id, "
            "configuration_json, revision, status, created_by, updated_by, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, 1, 'configured', ?, ?, ?, ?)",
            (bid, universe, definition["agent_definition_id"],
             json.dumps({"schema_version": 1, "name": name}), owner, owner, time.time(),
             time.time()))
    return bid


def test_migration_adds_the_identity_to_an_installed_package_directory(tmp_path):
    from tinyassets.agent_file_migration import migrate

    (tmp_path / "u").mkdir()
    # The package already landed this agent's harness files; the migration must
    # name the agent inside them rather than beside them.
    _agent_dir(tmp_path / "u", "scout", instructions="Scout ahead.\n",
               settings="schema_version: 1\ntools: {allow: [read]}\n")
    bid = _installed_template_agent(tmp_path, "alice", "u", "Scout",
                                    instructions="Scout ahead.", slug="scout")
    assert migrate(tmp_path) == {"u": ["agents/scout"]}
    settings = read_settings(tmp_path / "u", agent_slug="scout")
    assert settings.tools == ("read",) and settings.agent.id == bid


def test_migration_gives_an_installed_directory_the_text_the_agent_spoke_with(tmp_path):
    """The package landed the publisher's root AGENTS.md there, inert for this
    agent on main (its turns used the definition); the live text replaces it
    and the backup keeps the old bytes."""
    from tinyassets.agent_file_migration import LEDGER_NAME, migrate
    from tinyassets.commons_catalog import _agent_connect, get_binding

    (tmp_path / "u").mkdir()
    _agent_dir(tmp_path / "u", "scout", instructions="Instructions the package wrote.\n")
    bid = _installed_template_agent(tmp_path, "alice", "u", "Scout",
                                    instructions="What the agent actually spoke with.",
                                    slug="scout")
    assert migrate(tmp_path) == {"u": ["agents/scout"]}
    assert get_binding(tmp_path, universe_id="u", binding_id=bid) is None
    assert (tmp_path / "u/agents/scout/AGENTS.md").read_text(encoding="utf-8") == (
        "What the agent actually spoke with.\n")
    with _agent_connect(tmp_path) as conn:
        backup = conn.execute("SELECT backup FROM agent_file_migration WHERE name = ?",
                              (LEDGER_NAME,)).fetchone()[0]
    replaced = json.loads((tmp_path / backup).read_text(encoding="utf-8"))["replaced_files"]
    assert [(f["universe_id"], f["path"]) for f in replaced] == [("u", "agents/scout/AGENTS.md")]
    assert base64.b64decode(replaced[0]["base64"]).replace(b"\r\n", b"\n") == (
        b"Instructions the package wrote.\n")


def test_migration_keeps_an_unrelated_directory_of_the_same_name(tmp_path):
    from tinyassets.agent_file_migration import migrate

    (tmp_path / "u").mkdir()
    _sql_agent(tmp_path, "alice", "u", "Scout", instructions="Mine.")
    _agent_dir(tmp_path / "u", "scout", instructions="Another agent entirely.")
    assert migrate(tmp_path) == {"u": ["agents/scout-2"]}
    assert (tmp_path / "u" / "agents" / "scout" / "AGENTS.md").read_text(
        encoding="utf-8") == "Another agent entirely."
