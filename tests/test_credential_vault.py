"""Per-universe credential vault tests."""

from __future__ import annotations

import base64
import os
import sqlite3
import stat
import subprocess
from pathlib import Path

import pytest

from tinyassets.credential_vault import (
    VAULT_FILENAME,
    apply_provider_auth_env,
    claude_subscription_auth_available,
    codex_subscription_auth_available,
    ensure_claude_config_dir_from_vault,
    ensure_codex_home_from_vault,
    load_credential_vault,
    provider_auth_env_overrides,
    resolve_claude_config_dir,
    resolve_claude_oauth_token,
    resolve_codex_home,
    write_credential_vault,
)


def _vcs_slot(universe_dir, destination: str, purpose: str) -> str:
    """The token a ``vcs`` record holds for one exact destination + purpose slot.

    Reads the stored records directly: these tests pin how the vault MERGES
    purpose slots on write. There is no GitHub-specific resolver any more -- a
    universe reaches a forge through its owner's connection.
    """
    for record in load_credential_vault(universe_dir):
        if record.get("credential_type") != "vcs":
            continue
        if record.get("destination") != destination:
            continue
        purposes = record.get("purposes") or [record.get("purpose")]
        if purpose in purposes:
            return str(record.get("token") or "")
    return ""


def _make_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as symlink_error:
        if os.name != "nt":
            pytest.skip(f"directory symlinks unavailable: {symlink_error}")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        pytest.skip(f"directory junctions unavailable: {result.stderr.strip()}")


def _credential_snapshot_fixture(tmp_path):
    from tinyassets.credential_vault import adopt_llm_subscription_custody
    from tinyassets.storage import db_path

    universe_dir = tmp_path / "u-owner"
    universe_dir.mkdir()
    write_credential_vault(
        universe_dir,
        [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "auth_json_b64": "eyJ0b2tlbiI6InNuYXBzaG90LXNlY3JldCJ9",
        }],
        owner_user_id="owner-1",
        universe_id="u-owner",
    )
    conn = sqlite3.connect(db_path(tmp_path), isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        custody = adopt_llm_subscription_custody(
            conn,
            universe_dir=universe_dir,
            owner_user_id="owner-1",
            universe_id="u-owner",
            service="codex",
        )
        conn.commit()
    finally:
        conn.close()
    return universe_dir, custody


def test_vault_round_trips_typed_credentials_without_secret_summary(tmp_path):
    summary = write_credential_vault(
        tmp_path,
        [
            {
                "credential_type": "vcs",
                "service": "github",
                "destination": "TinyAssets/TinyAssets",
                "purpose": "write",
                "token": "ghs_secret",
            },
            {
                "credential_type": "social",
                "service": "twitter",
                "handle": "@workflow",
                "token": "social_secret",
            },
            {
                "credential_type": "llm_subscription",
                "service": "claude",
                "claude_config_dir": ".credentials/claude",
            },
        ],
    )

    assert summary["path"].endswith(VAULT_FILENAME)
    assert summary["credential_count"] == 3
    assert summary["credential_types"] == ["llm_subscription", "social", "vcs"]
    assert "ghs_secret" not in str(summary)
    loaded = load_credential_vault(tmp_path)
    assert loaded[0]["token"] == "ghs_secret"


def test_vault_rejects_unknown_credential_type(tmp_path):
    with pytest.raises(ValueError, match="unknown credential_type"):
        write_credential_vault(
            tmp_path,
            [{"credential_type": "database", "service": "postgres"}],
        )


def test_single_record_write_updates_only_matching_credential(tmp_path):
    github_credential = {
        "credential_type": "vcs",
        "service": "github",
        "destination": "TinyAssets/TinyAssets",
        "purpose": "write",
        "token": "ghs-existing",
    }
    write_credential_vault(
        tmp_path,
        [
            github_credential,
            {
                "credential_type": "llm_api_key",
                "service": "anthropic",
                "secret_b64": "b2xkLWtleQ==",
            },
        ],
    )

    summary = write_credential_vault(
        tmp_path,
        [{
            "credential_type": "llm_api_key",
            "service": "anthropic",
            "secret_b64": "bmV3LWtleQ==",
        }],
    )

    assert load_credential_vault(tmp_path) == [
        github_credential,
        {
            "credential_type": "llm_api_key",
            "service": "anthropic",
            "secret_b64": "bmV3LWtleQ==",
        },
    ]
    assert summary["credential_count"] == 2


def test_single_social_write_uses_service_slot_not_unread_identity_fields(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "social",
        "service": "twitter",
        "handle": "@one",
        "token": "first-token",
    }])

    write_credential_vault(tmp_path, [{
        "credential_type": "social",
        "service": "twitter",
        "handle": "@two",
        "token": "second-token",
    }])

    assert load_credential_vault(tmp_path) == [{
        "credential_type": "social",
        "service": "twitter",
        "handle": "@two",
        "token": "second-token",
    }]


def test_single_record_write_updates_equivalent_llm_api_key_alias(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "llm_api_key",
        "service": "claude",
        "secret_b64": "b2xkLWtleQ==",
    }])

    write_credential_vault(tmp_path, [{
        "credential_type": "llm_api_key",
        "service": "anthropic",
        "secret_b64": "bmV3LWtleQ==",
    }])

    assert load_credential_vault(tmp_path) == [{
        "credential_type": "llm_api_key",
        "service": "anthropic",
        "secret_b64": "bmV3LWtleQ==",
    }]


def test_single_record_write_normalizes_vcs_purpose_selector(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "vcs",
        "service": "github",
        "destination": "TinyAssets/TinyAssets",
        "purposes": ["write"],
        "token": "old-token",
    }])

    write_credential_vault(tmp_path, [{
        "credential_type": "vcs",
        "service": "github",
        "destination": "TinyAssets/TinyAssets",
        "purpose": "write",
        "token": "new-token",
    }])

    assert load_credential_vault(tmp_path) == [{
        "credential_type": "vcs",
        "service": "github",
        "destination": "TinyAssets/TinyAssets",
        "purpose": "write",
        "token": "new-token",
    }]


def test_single_record_write_rotates_matching_multi_purpose_vcs_token(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "vcs",
        "service": "github",
        "destination": "TinyAssets/TinyAssets",
        "purposes": ["write", "read"],
        "token": "ghs-OLD",
    }])

    summary = write_credential_vault(tmp_path, [{
        "credential_type": "vcs",
        "service": "github",
        "destination": "TinyAssets/TinyAssets",
        "purpose": "write",
        "token": "ghs-NEW-ROTATED",
    }])

    assert _vcs_slot(
        tmp_path, "TinyAssets/TinyAssets", purpose="write"
    ) == "ghs-NEW-ROTATED"
    assert summary["credential_count"] == 1
    assert "ghs-OLD" not in str(load_credential_vault(tmp_path))


def test_single_vcs_write_reports_dropped_purpose_slots(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "vcs",
        "service": "github",
        "destination": "TinyAssets/TinyAssets",
        "purposes": ["write", "read"],
        "token": "ghs-BOTH",
    }])

    summary = write_credential_vault(tmp_path, [{
        "credential_type": "vcs",
        "service": "github",
        "destination": "TinyAssets/TinyAssets",
        "purpose": "read",
        "token": "ghs-READONLY",
    }])

    assert summary["collapsed_credential_count"] == 0
    assert summary["dropped_credential_slots"] == [{
        "credential_type": "vcs",
        "service": "github",
        "destination": "TinyAssets/TinyAssets",
        "purposes": ["write"],
    }]
    assert "ghs-BOTH" not in str(summary)
    assert "ghs-READONLY" not in str(summary)
    assert _vcs_slot(
        tmp_path, "TinyAssets/TinyAssets", purpose="read"
    ) == "ghs-READONLY"
    assert _vcs_slot(
        tmp_path, "TinyAssets/TinyAssets", purpose="write"
    ) == ""


def test_single_subscription_write_preserves_sibling_fields(tmp_path):
    configured = tmp_path / "claude_cfg"
    write_credential_vault(tmp_path, [{
        "credential_type": "llm_subscription",
        "service": "claude",
        "claude_config_dir": str(configured),
        "oauth_token": "tok-ORIGINAL",
    }])

    write_credential_vault(tmp_path, [{
        "credential_type": "llm_subscription",
        "service": "claude",
        "oauth_token": "tok-ROTATED",
    }])

    assert load_credential_vault(tmp_path) == [{
        "credential_type": "llm_subscription",
        "service": "claude",
        "claude_config_dir": str(configured),
        "oauth_token": "tok-ROTATED",
    }]
    assert resolve_claude_config_dir(tmp_path) == configured
    assert resolve_claude_oauth_token(tmp_path) == "tok-ROTATED"


def test_single_subscription_write_replaces_reader_alias_slots(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "llm_subscription",
        "service": "claude",
        "claude_config_dir": "old-config",
        "oauth_token": "tok-OLD",
    }])

    write_credential_vault(tmp_path, [{
        "credential_type": "llm_subscription",
        "service": "claude",
        "config_dir": "new-config",
        "claude_code_oauth_token": "tok-NEW",
    }])

    assert resolve_claude_config_dir(tmp_path) == tmp_path / "new-config"
    assert resolve_claude_oauth_token(tmp_path) == "tok-NEW"
    assert load_credential_vault(tmp_path) == [{
        "credential_type": "llm_subscription",
        "service": "claude",
        "config_dir": "new-config",
        "claude_code_oauth_token": "tok-NEW",
    }]


def test_single_record_write_collapses_all_matching_duplicates(tmp_path):
    github_credential = {
        "credential_type": "vcs",
        "service": "github",
        "destination": "TinyAssets/TinyAssets",
        "purpose": "write",
        "token": "ghs-existing",
    }
    write_credential_vault(
        tmp_path,
        [
            {
                "credential_type": "llm_api_key",
                "service": "claude",
                "api_key": "sk-old-first",
            },
            github_credential,
            {
                "credential_type": "llm_api_key",
                "service": "anthropic",
                "api_key": "sk-old-second",
            },
        ],
    )

    summary = write_credential_vault(tmp_path, [{
        "credential_type": "llm_api_key",
        "service": "anthropic",
        "api_key": "sk-new",
    }])

    assert load_credential_vault(tmp_path) == [
        {
            "credential_type": "llm_api_key",
            "service": "anthropic",
            "api_key": "sk-new",
        },
        github_credential,
    ]
    assert summary["credential_count"] == 2
    assert summary["collapsed_credential_count"] == 1
    assert summary["dropped_credential_slots"] == []


def test_two_record_write_replaces_existing_vault_exactly(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "social",
        "service": "twitter",
        "token": "old-social-token",
    }])
    replacement = [
        {
            "credential_type": "llm_api_key",
            "service": "anthropic",
            "api_key": "sk-replacement",
        },
        {
            "credential_type": "vcs",
            "service": "github",
            "destination": "TinyAssets/TinyAssets",
            "purpose": "write",
            "token": "ghs-replacement",
        },
    ]

    summary = write_credential_vault(tmp_path, replacement)

    assert load_credential_vault(tmp_path) == replacement
    assert summary["credential_count"] == 2


def test_empty_write_clears_existing_vault(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "llm_api_key",
        "service": "anthropic",
        "api_key": "sk-to-clear",
    }])

    summary = write_credential_vault(tmp_path, [])

    assert load_credential_vault(tmp_path) == []
    assert summary["credential_count"] == 0


def test_codex_subscription_auth_can_materialize_from_vault(tmp_path):
    write_credential_vault(
        tmp_path,
        [
            {
                "credential_type": "llm_subscription",
                "service": "codex",
                "auth_json_b64": "e30=",
            }
        ],
    )

    codex_home = ensure_codex_home_from_vault(tmp_path)
    assert codex_home == tmp_path / ".credentials" / "codex"
    assert (codex_home / "auth.json").read_text(encoding="utf-8") == "{}"
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == (
        'cli_auth_credentials_store = "file"\n'
    )
    assert resolve_codex_home(tmp_path) == codex_home
    assert codex_subscription_auth_available(tmp_path) is True


def test_codex_subscription_auth_rotation_replaces_materialized_auth(tmp_path):
    configured = tmp_path / "codex-home"
    write_credential_vault(tmp_path, [{
        "credential_type": "llm_subscription",
        "service": "codex",
        "codex_home": str(configured),
        "auth_json_b64": "eyJ0b2tlbiI6Ik9MRC1DT0RFWC1BVVRIIn0=",
    }])
    assert ensure_codex_home_from_vault(tmp_path) == configured
    assert (configured / "auth.json").read_text(encoding="utf-8") == (
        '{"token":"OLD-CODEX-AUTH"}'
    )

    write_credential_vault(tmp_path, [{
        "credential_type": "llm_subscription",
        "service": "codex",
        "auth_json_b64": "eyJ0b2tlbiI6Ik5FVy1ST1RBVEVELUFVVEgifQ==",
    }])

    assert load_credential_vault(tmp_path) == [{
        "credential_type": "llm_subscription",
        "service": "codex",
        "codex_home": str(configured),
        "auth_json_b64": "eyJ0b2tlbiI6Ik5FVy1ST1RBVEVELUFVVEgifQ==",
    }]
    assert ensure_codex_home_from_vault(tmp_path) == configured
    assert (configured / "auth.json").read_text(encoding="utf-8") == (
        '{"token":"NEW-ROTATED-AUTH"}'
    )


@pytest.mark.parametrize(
    "malformed_auth_b64",
    ["!!!!", "e30=!!!!", "e30=\u00a0", ""],
)
def test_codex_materialization_rejects_malformed_blob_and_preserves_auth(
    tmp_path,
    malformed_auth_b64,
):
    configured = tmp_path / "codex-home"
    write_credential_vault(tmp_path, [{
        "credential_type": "llm_subscription",
        "service": "codex",
        "codex_home": "codex-home",
        "auth_json_b64": "eyJ0b2tlbiI6IldPUktJTkMifQ==",
    }])
    assert ensure_codex_home_from_vault(tmp_path) == configured
    working_auth = (configured / "auth.json").read_bytes()

    (tmp_path / VAULT_FILENAME).write_text(
        '{"schema_version":1,"credentials":[{"credential_type":'
        '"llm_subscription","service":"codex","codex_home":"codex-home",'
        f'"auth_json_b64":"{malformed_auth_b64}"}}]}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="auth_json_b64"):
        ensure_codex_home_from_vault(tmp_path)

    assert (configured / "auth.json").read_bytes() == working_auth


def test_codex_auth_write_rejects_non_json_blob_and_preserves_vault(tmp_path):
    configured = tmp_path / "codex-home"
    existing = {
        "credential_type": "llm_subscription",
        "service": "codex",
        "codex_home": "codex-home",
        "auth_json_b64": "eyJ0b2tlbiI6IldPUktJTkMifQ==",
    }
    write_credential_vault(tmp_path, [existing])
    assert ensure_codex_home_from_vault(tmp_path) == configured
    working_auth = (configured / "auth.json").read_bytes()

    with pytest.raises(ValueError, match="auth_json_b64"):
        write_credential_vault(tmp_path, [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "auth_json_b64": "bm90LWpzb24=",
        }])

    assert load_credential_vault(tmp_path) == [existing]
    assert (configured / "auth.json").read_bytes() == working_auth


def test_codex_auth_write_rejects_utf8_bom_and_preserves_vault(tmp_path):
    configured = tmp_path / "codex-home"
    existing = {
        "credential_type": "llm_subscription",
        "service": "codex",
        "codex_home": "codex-home",
        "auth_json_b64": "eyJ0b2tlbiI6IldPUktJTkMifQ==",
    }
    write_credential_vault(tmp_path, [existing])
    assert ensure_codex_home_from_vault(tmp_path) == configured
    working_auth = (configured / "auth.json").read_bytes()
    bom_auth_b64 = base64.b64encode(
        b'\xef\xbb\xbf{"token":"REJECTED"}'
    ).decode("ascii")

    with pytest.raises(ValueError, match="UTF-8 BOM"):
        write_credential_vault(tmp_path, [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "auth_json_b64": bom_auth_b64,
        }])

    assert load_credential_vault(tmp_path) == [existing]
    assert (configured / "auth.json").read_bytes() == working_auth


def test_codex_auth_accepts_line_wrapped_base64(tmp_path):
    write_credential_vault(tmp_path, [{
        "credential_type": "llm_subscription",
        "service": "codex",
        "auth_json_b64": "eyJ0b2tlbiI6\nIldSQVBQRUQifQ==",
    }])

    codex_home = ensure_codex_home_from_vault(tmp_path)

    assert (codex_home / "auth.json").read_bytes() == b'{"token":"WRAPPED"}'


def test_codex_home_path_from_vault_is_resolved_without_env(tmp_path):
    configured = tmp_path / "durable-codex"
    write_credential_vault(
        tmp_path,
        [
            {
                "credential_type": "llm_subscription",
                "service": "codex",
                "codex_home": str(configured),
            }
        ],
    )

    assert ensure_codex_home_from_vault(tmp_path) == configured
    assert resolve_codex_home(tmp_path) == configured


def test_claude_config_dir_from_vault_sets_provider_env(tmp_path):
    configured = tmp_path / "durable-claude"
    write_credential_vault(
        tmp_path,
        [
            {
                "credential_type": "llm_subscription",
                "service": "claude",
                "claude_config_dir": str(configured),
            }
        ],
    )

    assert ensure_claude_config_dir_from_vault(tmp_path) == configured
    assert resolve_claude_config_dir(tmp_path) == configured
    assert claude_subscription_auth_available(tmp_path) is True
    assert provider_auth_env_overrides(tmp_path, "claude-code") == {
        "CLAUDE_CONFIG_DIR": str(configured)
    }


def test_apply_provider_auth_env_uses_workflow_universe(tmp_path):
    configured = tmp_path / "claude-dir"
    write_credential_vault(
        tmp_path,
        [
            {
                "credential_type": "llm_subscription",
                "service": "claude",
                "claude_config_dir": str(configured),
            }
        ],
    )
    env = {"TINYASSETS_UNIVERSE": str(tmp_path)}

    apply_provider_auth_env(env, "claude-code")

    assert env["CLAUDE_CONFIG_DIR"] == str(configured)


def test_missing_vault_loads_as_empty(tmp_path: Path):
    assert load_credential_vault(tmp_path) == []


@pytest.mark.parametrize("link_level", ["runtime", "snapshot_root"])
def test_snapshot_refuses_symlinked_root_before_writing_secret(tmp_path, link_level):
    from tinyassets.credential_vault import snapshot_llm_subscription_credential

    universe_dir, custody = _credential_snapshot_fixture(tmp_path)
    runtime_dir = universe_dir / ".runtime"
    outside = tmp_path / "outside-snapshot-root"
    outside.mkdir()
    if link_level == "runtime":
        _make_directory_link(runtime_dir, outside)
    else:
        runtime_dir.mkdir()
        _make_directory_link(runtime_dir / "provider-launch-credentials", outside)

    with pytest.raises(PermissionError, match="snapshot directory"):
        snapshot_llm_subscription_credential(
            universe_dir=universe_dir,
            custody=custody,
        )

    assert list(outside.iterdir()) == []


def test_snapshot_cleanup_preserves_decoy_and_removes_renamed_plaintext(
    tmp_path,
    caplog,
):
    from tinyassets.credential_vault import (
        cleanup_llm_credential_snapshot,
        snapshot_llm_subscription_credential,
    )

    universe_dir, custody = _credential_snapshot_fixture(tmp_path)
    snapshot = snapshot_llm_subscription_credential(
        universe_dir=universe_dir,
        custody=custody,
    )
    outside = tmp_path / "outside-cleanup"
    outside.mkdir()
    outside_marker = outside / "keep.txt"
    outside_marker.write_text("outside the snapshot", encoding="utf-8")
    _make_directory_link(snapshot.directory / "outside-link", outside)
    relocated = snapshot.directory.with_name(snapshot.directory.name + "-relocated")
    snapshot.directory.rename(relocated)
    snapshot.directory.mkdir()
    decoy = snapshot.directory / "keep.txt"
    decoy.write_text("not the tracked snapshot", encoding="utf-8")

    cleanup_llm_credential_snapshot(snapshot)

    assert snapshot.directory.is_dir()
    assert decoy.read_text(encoding="utf-8") == "not the tracked snapshot"
    assert not relocated.exists()
    assert list(snapshot.directory.parent.glob("*/auth.json")) == []
    assert outside_marker.read_text(encoding="utf-8") == "outside the snapshot"
    assert "credential snapshot path identity changed" in caplog.text


@pytest.mark.skipif(os.name == "nt", reason="Windows does not enforce POSIX modes")
def test_snapshot_directory_and_files_are_owner_only(tmp_path):
    from tinyassets.credential_vault import (
        cleanup_llm_credential_snapshot,
        snapshot_llm_subscription_credential,
    )

    universe_dir, custody = _credential_snapshot_fixture(tmp_path)
    snapshot = snapshot_llm_subscription_credential(
        universe_dir=universe_dir,
        custody=custody,
    )
    try:
        assert stat.S_IMODE(snapshot.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(snapshot.directory.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(snapshot.directory.parent.parent.stat().st_mode) == 0o700
        assert {
            path.name: stat.S_IMODE(path.stat().st_mode)
            for path in snapshot.directory.iterdir()
        } == {".lock": 0o400, "auth.json": 0o400, "config.toml": 0o400}
    finally:
        cleanup_llm_credential_snapshot(snapshot)


@pytest.mark.skipif(os.name == "nt", reason="Windows does not enforce POSIX roles")
def test_role_snapshot_group_is_explicit_and_survives_reprepare(tmp_path, monkeypatch):
    from tinyassets import credential_vault as vault
    from tinyassets import role_modes

    universe, custody = _credential_snapshot_fixture(tmp_path)
    monkeypatch.setenv("TINYASSETS_CREDENTIAL_BROKER", "process")
    monkeypatch.setattr(role_modes, "WORK_GID", os.getgid())
    snapshot = vault.snapshot_llm_subscription_credential(universe_dir=universe, custody=custody)
    try:
        vault._prepare_snapshot_root(universe)
        for path in (snapshot.directory, snapshot.directory.parent,
                     snapshot.directory.parent.parent):
            assert (path.stat().st_gid, stat.S_IMODE(path.stat().st_mode)) == (os.getgid(), 0o2750)
        for path in snapshot.directory.iterdir():
            assert (path.stat().st_gid, stat.S_IMODE(path.stat().st_mode)) == (os.getgid(), 0o440)
        assert (snapshot.directory / "auth.json").read_bytes()
    finally:
        vault.cleanup_llm_credential_snapshot(snapshot)


@pytest.mark.skipif(os.name == "nt", reason="Windows does not enforce POSIX roles")
@pytest.mark.parametrize("operation", ["fchown", "fchmod", "fsync"])
def test_role_snapshot_permission_or_sync_failure_never_returns_credentials(
    tmp_path, monkeypatch, operation,
):
    from tinyassets import credential_vault as vault
    from tinyassets import role_modes

    universe, custody = _credential_snapshot_fixture(tmp_path)
    monkeypatch.setenv("TINYASSETS_CREDENTIAL_BROKER", "process")
    monkeypatch.setattr(role_modes, "WORK_GID", os.getgid())
    original = getattr(os, operation)

    def fail_file(fd, *args):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("snapshot publication fixture failure")
        return original(fd, *args)

    monkeypatch.setattr(os, operation, fail_file)
    with pytest.raises(OSError, match="publication fixture"):
        vault.snapshot_llm_subscription_credential(universe_dir=universe, custody=custody)
    assert not list((universe / ".runtime/provider-launch-credentials").glob("*/auth.json"))
