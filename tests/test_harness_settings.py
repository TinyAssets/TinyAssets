"""Runtime settings cannot turn malformed files or package hints into grants."""

import base64
import hashlib
import json
import os
import subprocess
from dataclasses import FrozenInstanceError

import pytest

from tinyassets.harness_settings import (
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
