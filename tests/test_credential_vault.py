"""Per-universe credential vault tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tinyassets.credential_vault import (
    VAULT_FILENAME,
    load_credential_vault,
    write_credential_vault,
)


@pytest.fixture(autouse=True)
def _admitted_owner_identity(monkeypatch):
    """The broker resolves the owner's uid/gid; a test process has no broker."""
    from tinyassets.broker import owner_identities

    monkeypatch.setattr(
        owner_identities, "owner_identity",
        lambda root, *, principal, allocate=False: owner_identities.OwnerIdentity(
            300001, 300001),
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
                "credential_type": "http",
                "service": "model",
                "token": "model-secret",
            },
        ],
    )

    assert summary["path"].endswith(VAULT_FILENAME)
    assert summary["credential_count"] == 3
    assert summary["credential_types"] == ["http", "social", "vcs"]
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
                "credential_type": "http",
                "service": "anthropic",
                "secret_b64": "b2xkLWtleQ==",
            },
        ],
    )

    summary = write_credential_vault(
        tmp_path,
        [{
            "credential_type": "http",
            "service": "anthropic",
            "secret_b64": "bmV3LWtleQ==",
        }],
    )

    assert load_credential_vault(tmp_path) == [
        github_credential,
        {
            "credential_type": "http",
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
                "credential_type": "http",
                "service": "claude",
                "api_key": "sk-old-first",
            },
            github_credential,
            {
                "credential_type": "http",
                "service": "anthropic",
                "api_key": "sk-old-second",
            },
        ],
    )

    summary = write_credential_vault(tmp_path, [{
        "credential_type": "http",
        "service": "anthropic",
        "api_key": "sk-new",
    }])

    assert load_credential_vault(tmp_path) == [
        {
            "credential_type": "http",
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
            "credential_type": "http",
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
        "credential_type": "http",
        "service": "anthropic",
        "api_key": "sk-to-clear",
    }])

    summary = write_credential_vault(tmp_path, [])

    assert load_credential_vault(tmp_path) == []
    assert summary["credential_count"] == 0




















def test_missing_vault_loads_as_empty(tmp_path: Path):
    assert load_credential_vault(tmp_path) == []










