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
    (base / "agents" / "notes.md").write_text("not an agent", encoding="utf-8")
    (base / "agents" / "empty").mkdir()

    assert [(a.slug, a.agent_id, a.name) for a in list_agent_files(base)] == [
        ("scout", "scout", "scout"), ("weaver", "agent_binding_01X", "Evidence Weaver")]
    assert [a.slug for a in list_agent_files(base, include_retired=True)] == [
        "scout", "sleeper", "weaver"]
    assert load_agent(base, "agent_binding_01X").slug == "weaver"
    assert load_agent(base, "weaver").agent_id == "agent_binding_01X"
    assert load_agent(base, "scout").instructions == "Answer briefly."
    assert load_agent(base, "main").slug == "main"
    for missing in ("sleeper", "empty", "nobody"):
        with pytest.raises(LookupError):
            load_agent(base, missing)
    assert addressed_agents.roster(tmp_path, universe_id="u", owner="o") == [
        {"agent_id": "main", "name": "Your agent"},
        {"agent_id": "scout", "name": "scout"},
        {"agent_id": "agent_binding_01X", "name": "Evidence Weaver"}]
    resolved = addressed_agents.resolve(tmp_path, universe_id="u", owner="o",
                                        agent_id="agent_binding_01X")
    assert (resolved.agent_id, resolved.name, resolved.agent_slug) == (
        "agent_binding_01X", "Evidence Weaver", "weaver")
    assert addressed_agents.resolve(tmp_path, universe_id="u", owner="o", agent_id="main") is None
    with pytest.raises(addressed_agents.AgentNotAddressable):
        addressed_agents.resolve(tmp_path, universe_id="u", owner="o", agent_id="sleeper")


def test_two_directories_claiming_one_id_do_not_answer(tmp_path):
    from tinyassets.agent_definition import load_agent

    _agent_dir(tmp_path, "a", settings="schema_version: 1\nagent: {id: shared}\n")
    _agent_dir(tmp_path, "b", settings="schema_version: 1\nagent: {id: shared}\n")
    with pytest.raises(LookupError, match="more than one"):
        load_agent(tmp_path, "shared")
    assert load_agent(tmp_path, "a").slug == "a"


@pytest.mark.parametrize("raw", [
    b"schema_version: 1\nagent: {id: main}", b"schema_version: 1\nagent: {id: 'a b'}",
    b"schema_version: 1\nagent: {retired: 1}", b"schema_version: 1\nagent: {owner: x}",
    b"schema_version: 1\nagent: {configuration: []}", b"schema_version: 1\nagent: text",
])
def test_invalid_agent_block_never_falls_back(raw):
    with pytest.raises(SettingsError):
        parse_settings(raw)


def test_malformed_agent_settings_fail_the_roster_rather_than_answer_with_defaults(tmp_path):
    from tinyassets.agent_definition import list_agent_files

    _agent_dir(tmp_path, "broken", settings="schema_version: 2\n")
    with pytest.raises(SettingsError):
        list_agent_files(tmp_path)


def _sql_agent(base, owner, universe, name, *, instructions, **configuration):
    from tinyassets.commons_catalog import create_binding, publish_definition

    definition = publish_definition(base, author_id=owner, payload={
        "schema_version": 1, "name": name,
        "components": {"identity": {"kind": "soul", "config": {"instructions": instructions}}},
    })
    return create_binding(
        base, universe_id=universe, definition_id=definition["agent_definition_id"],
        created_by=owner, payload={"schema_version": 1, "name": name, **configuration},
    )["agent_binding_id"]


def test_migration_lands_sql_agents_as_directories_and_keeps_their_ids(tmp_path):
    import sqlite3

    from scripts.migrate_agent_files import migrate_all, migrate_universe
    from tinyassets import addressed_agents
    from tinyassets.commons_catalog import db_path, get_binding, list_bindings
    from tinyassets.conversation_store import load_recent_readonly, record_turn

    (tmp_path / "u-home").mkdir()
    (tmp_path / "u-bob").mkdir()
    owner = "alice"
    weaver = _sql_agent(tmp_path, owner, "u-home", "Evidence Weaver",
                        instructions="Weigh every claim.", channels={"slack": "c1"})
    serving = _sql_agent(tmp_path, owner, "u-home", "Main Source", instructions="",
                         role="writer")
    installation = _sql_agent(tmp_path, owner, "u-home", "App experience", instructions="x",
                              role="app_experience", turn_consumer={"version": 1,
                                                                    "state": "disabled"})
    bob = _sql_agent(tmp_path, "bob", "u-bob", "Private", instructions="Bob owns this one.")
    twin = _sql_agent(tmp_path, owner, "u-home", "Evidence Weaver", instructions="Second.")
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("UPDATE agent_bindings SET status = 'serving' WHERE agent_binding_id = ?",
                     (serving,))
        conn.execute("UPDATE agent_bindings SET retired = 1 WHERE agent_binding_id = ?", (twin,))
    session = addressed_agents.memory_session(owner, weaver)
    record_turn(tmp_path / "u-home", session, "founder", "Earlier question", ts=1)
    record_turn(tmp_path / "u-home", session, "universe", "Earlier answer", ts=2)

    assert migrate_all(tmp_path) == {
        "u-bob": ["agents/private"],
        "u-home": ["agents/evidence-weaver", "agents/evidence-weaver-2"],
    }

    home = tmp_path / "u-home"
    assert (home / "agents/evidence-weaver/AGENTS.md").read_text(encoding="utf-8") == (
        "Weigh every claim.\n")
    settings = read_settings(home, agent_slug="evidence-weaver")
    assert (settings.agent.id, settings.agent.name, settings.agent.retired) == (
        weaver, "Evidence Weaver", False)
    assert settings.agent.configuration == {"channels": {"slack": "c1"}}
    assert read_settings(home, agent_slug="evidence-weaver-2").agent.retired is True
    assert addressed_agents.roster(tmp_path, universe_id="u-home", owner=owner) == [
        {"agent_id": "main", "name": "Your agent"},
        {"agent_id": weaver, "name": "Evidence Weaver"}]
    assert addressed_agents.resolve(
        tmp_path, universe_id="u-home", owner=owner, agent_id=weaver).instructions == (
        "Weigh every claim.\n")
    assert [m.text for m in load_recent_readonly(home, session)] == [
        "Earlier question", "Earlier answer"]
    assert addressed_agents.roster(tmp_path, universe_id="u-bob", owner="bob")[1]["agent_id"] == bob
    # The serving binding and the installation stay in SQL; the agents are gone from it.
    remaining = {b["agent_binding_id"] for b in list_bindings(tmp_path, universe_id="u-home")}
    assert remaining == {serving, installation}
    assert get_binding(tmp_path, universe_id="u-home", binding_id=weaver) is None

    # Replay is a no-op and never touches an edited file.
    (home / "agents/evidence-weaver/AGENTS.md").write_text("Edited.\n", encoding="utf-8")
    assert migrate_universe(tmp_path, "u-home") == []
    assert (home / "agents/evidence-weaver/AGENTS.md").read_text(encoding="utf-8") == "Edited.\n"


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
    from scripts.migrate_agent_files import migrate_universe

    (tmp_path / "u").mkdir()
    # The package already landed this agent's harness files; the migration must
    # name the agent inside them rather than beside them.
    _agent_dir(tmp_path / "u", "scout", instructions="Scout ahead.\n",
               settings="schema_version: 1\ntools: {allow: [read]}\n")
    bid = _installed_template_agent(tmp_path, "alice", "u", "Scout",
                                    instructions="Scout ahead.", slug="scout")
    assert migrate_universe(tmp_path, "u") == ["agents/scout"]
    settings = read_settings(tmp_path / "u", agent_slug="scout")
    assert settings.tools == ("read",) and settings.agent.id == bid


def test_migration_refuses_a_conflicting_directory_and_commits_nothing(tmp_path):
    from scripts.migrate_agent_files import migrate_universe
    from tinyassets.commons_catalog import get_binding

    (tmp_path / "u").mkdir()
    _agent_dir(tmp_path / "u", "scout", instructions="Instructions the package wrote.\n")
    bid = _installed_template_agent(tmp_path, "alice", "u", "Scout",
                                    instructions="What the agent actually spoke with.",
                                    slug="scout")
    with pytest.raises(ValueError, match="already edited or conflicts"):
        migrate_universe(tmp_path, "u")
    assert get_binding(tmp_path, universe_id="u", binding_id=bid) is not None
    assert not (tmp_path / "u" / "agents" / "scout" / "settings.yaml").exists()


def test_migration_keeps_an_unrelated_directory_of_the_same_name(tmp_path):
    from scripts.migrate_agent_files import migrate_universe

    (tmp_path / "u").mkdir()
    _sql_agent(tmp_path, "alice", "u", "Scout", instructions="Mine.")
    _agent_dir(tmp_path / "u", "scout", instructions="Another agent entirely.")
    assert migrate_universe(tmp_path, "u") == ["agents/scout-2"]
    assert (tmp_path / "u" / "agents" / "scout" / "AGENTS.md").read_text(
        encoding="utf-8") == "Another agent entirely."
