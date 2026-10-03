"""Provider authority lives in a platform record, never in the agent-editable config.

``config.yaml`` is the agent's own (founder-approved self-improving harness), so
the fields the router and provider binding trust -- ``allowed_providers``,
``engine_assignment_*``, ``provider_authority_bindings`` -- are read only from
``tinyassets.provider_authority``'s record (command-center-cutover E6,
target-architecture D8a). Writing them into config.yaml must change nothing.
"""

from __future__ import annotations

import json

import pytest
import yaml

from tinyassets import provider_authority as pa
from tinyassets.config import (
    UniverseConfig,
    load_universe_config,
    write_provider_assignment_projection,
    write_universe_config_fields,
)


def _home(tmp_path):
    home = tmp_path / "u-home"
    home.mkdir()
    return home


def _config(home, text: str) -> None:
    (home / "config.yaml").write_text(text, encoding="utf-8")


def test_the_first_read_migrates_todays_values_out_of_config_yaml(tmp_path):
    home = _home(tmp_path)
    _config(home, "preferred_writer: codex\nallowed_providers:\n  - codex\n"
                  "engine_assignment_state: ready\nengine_assignment_generation: 3\n")

    loaded = load_universe_config(home)

    assert loaded.allowed_providers == ["codex"]
    assert loaded.engine_assignment_state == "ready"
    assert loaded.engine_assignment_generation == 3
    assert loaded.preferred_writer == "codex"
    record = json.loads(pa.record_path(home).read_text(encoding="utf-8"))
    assert record["allowed_providers"] == ["codex"] and record["engine_assignment_generation"] == 3


def test_writing_authority_into_config_yaml_changes_nothing(tmp_path, caplog):
    """The escalation this closes: the agent widens its own routing ceiling."""
    home = _home(tmp_path)
    _config(home, "allowed_providers:\n  - codex\n")
    assert load_universe_config(home).allowed_providers == ["codex"]  # migrated once

    _config(home, "allowed_providers:\n  - codex\n  - claude-code\n  - api_key_http:evil\n"
                  "engine_assignment_state: ready\nengine_assignment_generation: 99\n"
                  "provider_authority_bindings:\n  claude-code: {binding_id: forged}\n")
    loaded = load_universe_config(home)

    assert loaded.allowed_providers == ["codex"]
    assert loaded.engine_assignment_state == "unassigned"
    assert loaded.engine_assignment_generation == 0
    assert loaded.provider_authority_bindings == {}
    assert "ignored" in caplog.text


def test_a_never_assigned_home_gets_a_default_record_so_the_hole_never_opens(tmp_path):
    home = _home(tmp_path)
    assert load_universe_config(home).allowed_providers is None
    _config(home, "allowed_providers: [api_key_http:evil]\n")
    assert load_universe_config(home).allowed_providers is None


def test_an_unreadable_record_fails_closed(tmp_path):
    home = _home(tmp_path)
    pa.record_path(home).parent.mkdir()
    pa.record_path(home).write_text("{not json", encoding="utf-8")
    assert load_universe_config(home).allowed_providers == []


def test_a_missing_home_gets_defaults_and_is_never_created(tmp_path):
    ghost = tmp_path / "u-gone"
    assert load_universe_config(ghost).allowed_providers is None
    assert not ghost.exists()


def test_the_assignment_writer_puts_authority_in_the_record_and_strips_config(tmp_path):
    home = _home(tmp_path)
    _config(home, "allowed_providers: [codex]\nstyle: terse\n")
    write_provider_assignment_projection(
        home, state="ready", generation=2, provider="codex",
        binding={"binding_id": "b1"},
    )
    config_text = (home / "config.yaml").read_text(encoding="utf-8")
    for name in pa.AUTHORITY_FIELDS:
        assert name not in config_text
    assert "style: terse" in config_text and "preferred_writer: codex" in config_text
    loaded = load_universe_config(home)
    assert loaded.allowed_providers == ["codex"]
    assert loaded.provider_authority_bindings == {"codex": {"binding_id": "b1"}}


def test_the_generic_config_writer_refuses_authority(tmp_path):
    home = _home(tmp_path)
    with pytest.raises(ValueError, match="platform record"):
        write_universe_config_fields(home, allowed_providers=["claude-code"])
    write_universe_config_fields(home, preferred_writer="codex")  # preferences still fine


def test_the_record_is_invisible_to_the_agents_tool_jail():
    """Hidden home entries are never bound into the tool jail (universe_tools)."""
    assert pa.record_path("/h").parent.name.startswith(".")


def test_an_unreadable_config_never_records_defaults_over_a_real_assignment(tmp_path):
    home = _home(tmp_path)
    _config(home, "allowed_providers: [codex\n")  # unparseable
    assert load_universe_config(home).allowed_providers == []  # nothing routes
    assert not pa.record_path(home).exists(), "migration waits for a readable file"
    _config(home, "allowed_providers: [codex]\n")
    assert load_universe_config(home).allowed_providers == ["codex"]


def test_concurrent_migration_returns_the_winning_record(tmp_path, monkeypatch):
    home = _home(tmp_path)
    real_link = pa.os.link
    winner = {**pa.DEFAULTS, "allowed_providers": ["codex"], "engine_assignment_generation": 9}

    def publish_before_link(source, destination):
        with monkeypatch.context() as patch:
            patch.setattr(pa.os, "link", real_link)
            assert pa.authority_for(home, winner) == winner
        real_link(source, destination)

    monkeypatch.setattr(pa.os, "link", publish_before_link)
    assert pa.authority_for(home, {"allowed_providers": ["claude-code"]}) == winner
    assert json.loads(pa.record_path(home).read_text(encoding="utf-8")) == winner
    assert list(pa.record_path(home).parent.glob("*.tmp")) == []


def test_read_only_home_uses_readable_config_authority(tmp_path, monkeypatch, caplog):
    home = _home(tmp_path)
    _config(home, "allowed_providers: [codex]\nengine_assignment_state: ready\n"
                  "engine_assignment_generation: 4\n"
                  "provider_authority_bindings: {codex: {binding_id: old}}\n")

    def refuse_write(*args, **kwargs):
        raise OSError("read-only home")

    monkeypatch.setattr(pa.tempfile, "mkstemp", refuse_write)
    loaded = load_universe_config(home)
    assert loaded.allowed_providers == ["codex"]
    assert loaded.engine_assignment_state == "ready"
    assert loaded.engine_assignment_generation == 4
    assert loaded.provider_authority_bindings == {"codex": {"binding_id": "old"}}
    assert "could not be written" in caplog.text
    assert not pa.record_path(home).exists()


def test_preference_write_migrates_and_strips_legacy_authority(tmp_path):
    home = _home(tmp_path)
    _config(home, "allowed_providers: [claude-code]\nengine_assignment_state: ready\n"
                  "engine_assignment_generation: 4\n"
                  "provider_authority_bindings: {claude-code: {binding_id: old}}\n")
    write_universe_config_fields(home, preferred_writer="codex")
    data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert not set(pa.AUTHORITY_FIELDS).intersection(data)
    assert data["preferred_writer"] == "codex"
    record = json.loads(pa.record_path(home).read_text(encoding="utf-8"))
    assert record["allowed_providers"] == ["claude-code"]
    assert record["engine_assignment_generation"] == 4


@pytest.mark.parametrize("carrier_armed", [False, True])
def test_router_ceiling_refreshes_captured_authority(tmp_path, carrier_armed):
    from tinyassets.providers.base import UniverseContext
    from tinyassets.providers.router import _effective_universe_provider_ceiling

    home = _home(tmp_path)
    captured = UniverseConfig(allowed_providers=["claude-code"])
    pa.write_record(home, {"allowed_providers": ["codex"]})
    context = UniverseContext(universe_dir=home, config=captured)
    assert _effective_universe_provider_ceiling(
        context, captured, carrier_armed=carrier_armed,
    ) == ["codex"]
    assert captured.allowed_providers == ["claude-code"]


def test_current_refreshes_all_authority_and_preserves_preferences(tmp_path):
    home = _home(tmp_path)
    captured = UniverseConfig(allowed_providers=["claude-code"], preferred_writer="claude-code")
    record = {
        "allowed_providers": ["codex"],
        "engine_assignment_state": "ready",
        "engine_assignment_generation": 7,
        "provider_authority_bindings": {"codex": {"binding_id": "current"}},
    }
    pa.write_record(home, record)
    refreshed = pa.current(home, captured)
    assert {name: getattr(refreshed, name) for name in pa.AUTHORITY_FIELDS} == record
    assert refreshed.preferred_writer == captured.preferred_writer
    pa.write_record(home, {**record, "allowed_providers": []})
    assert pa.current(home, captured).allowed_providers == []
    assert pa.current(None, captured) is captured
    assert pa.current(home / "missing", captured) is captured


def test_preference_writer_keeps_authority_when_migration_cannot_write(tmp_path, monkeypatch):
    home = _home(tmp_path)
    original = "allowed_providers: [codex]\n"
    _config(home, original)

    def refuse_write(*args, **kwargs):
        raise OSError("read-only record directory")

    monkeypatch.setattr(pa.tempfile, "mkstemp", refuse_write)
    with pytest.raises(OSError, match="cannot strip legacy authority"):
        write_universe_config_fields(home, preferred_writer="claude-code")
    assert (home / "config.yaml").read_text(encoding="utf-8") == original
