"""A universe provider child must receive only its own authority.

The legacy boundary copied the host environment and subtracted known auth
variables. Direct tokens, default homes, cloud activation, helper failures, and
future names could therefore retain maintainer authority. The repaired boundary
starts universe children from an explicit runtime allowlist, physically
contains discovery/auth paths, and applies only a validated selected-universe
overlay. Production mounts host auth homes, so these are billing boundaries,
not environment-cleanliness preferences.

Each test below states the mutation that must make it fail. A test that cannot
go red is not evidence.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tinyassets.providers.base import (
    API_KEY_PROVIDER_ENV_VARS,
    subprocess_env_without_api_keys,
)

AMBIENT_CLOUD_AUTH_VARS = (
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ROLE_ARN",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "CLAUDE_CODE_USE_VERTEX",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "CLOUD_ML_REGION",
    "ANTHROPIC_VERTEX_PROJECT_ID",
)

AMBIENT_ROUTING_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def _make_directory_link(link: Path, target: Path) -> None:
    symlink_failure = ""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError as symlink_error:
        symlink_failure = str(symlink_error)
        if os.name != "nt":
            pytest.skip(f"directory symlinks unavailable: {symlink_error}")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        pytest.skip(
            "directory symlinks/junctions unavailable: "
            f"{symlink_failure}; {result.stderr.strip()}"
        )


def _make_file_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")


def _write_outside_vault(path: Path, token: str) -> None:
    path.write_text(
        (
            '{"schema_version": 1, "credentials": [{'
            '"credential_type": "llm_api_key", '
            '"service": "claude-code", '
            f'"api_key": "{token}"'
            "}]}\n"
        ),
        encoding="utf-8",
    )


@pytest.fixture
def host_auth(monkeypatch):
    """Simulate the prod container: host subscription auth present in the env."""
    for name in ("UNRELATED_SETTING",):
        monkeypatch.setenv(name, f"host-value-for-{name}")
    monkeypatch.delenv("TINYASSETS_UNIVERSE", raising=False)
    monkeypatch.setenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", "0")






def test_host_local_api_key_policy_strips_all_six_variables(host_auth, monkeypatch):
    """The MODIFIED spec must preserve the canonical host-local key policy."""
    for name in API_KEY_PROVIDER_ENV_VARS:
        monkeypatch.setenv(name, f"host-value-for-{name}")

    env = subprocess_env_without_api_keys()

    assert env is not None
    assert set(API_KEY_PROVIDER_ENV_VARS).isdisjoint(env)
    for name in ("UNRELATED_SETTING",):
        assert env.get(name) == f"host-value-for-{name}"


















































