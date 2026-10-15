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

from tinyassets.credential_vault import (
    claude_subscription_auth_available,
    credential_vault_path,
    write_credential_vault,
)
from tinyassets.exceptions import ProviderUnavailableError
from tinyassets.providers.base import (
    API_KEY_PROVIDER_ENV_VARS,
    HOST_SUBSCRIPTION_ENV_VARS,
    subprocess_env_for_provider,
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
    for name in HOST_SUBSCRIPTION_ENV_VARS:
        monkeypatch.setenv(name, f"host-value-for-{name}")
    monkeypatch.delenv("TINYASSETS_UNIVERSE", raising=False)
    monkeypatch.setenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", "0")


def test_universe_without_credential_does_not_inherit_host_auth(host_auth, tmp_path):
    """MUTATION: use host/default credential homes for an empty universe -> RED."""
    universe = tmp_path / "u-newborn"
    universe.mkdir()

    env = subprocess_env_for_provider("claude-code", universe_dir=universe)

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert Path(env["CLAUDE_CONFIG_DIR"]).is_relative_to(universe)
    assert "CODEX_HOME" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert ".runtime/provider-child/claude-code/auth-empty" in (
        Path(env["CLAUDE_CONFIG_DIR"]).as_posix()
    )
    assert claude_subscription_auth_available(universe) is False
    assert not (universe / ".credentials").exists()


def test_host_local_daemon_keeps_its_own_auth(host_auth):
    """The guard must not break the host's own flows.

    MUTATION: apply the universe empty-base environment without a universe
    binding -> RED, because the host daemon would lose its own credentials.

    Keystone slice 1 (requester-owned execution) deliberately does NOT strip
    this no-universe path: the founder fail-closed guarantee covers USER
    universes (which carry a universe_dir and hit the router-level ceiling),
    and host-local no-universe fail-close is deferred to the
    TINYASSETS_PROVIDER_AUTHORITY_V2-gated successor (constrain-set-engine
    Decision 5). So this asserts host-env RETENTION, not the strip.
    """
    env = subprocess_env_for_provider("claude-code", universe_dir=None)

    for name in HOST_SUBSCRIPTION_ENV_VARS:
        assert env.get(name) == f"host-value-for-{name}", (
            f"{name} was stripped for a host-local call with no universe in "
            "play; that breaks the daemon and dev loop"
        )


def test_host_local_api_key_policy_strips_all_six_variables(host_auth, monkeypatch):
    """The MODIFIED spec must preserve the canonical host-local key policy."""
    for name in API_KEY_PROVIDER_ENV_VARS:
        monkeypatch.setenv(name, f"host-value-for-{name}")

    env = subprocess_env_without_api_keys()

    assert env is not None
    assert set(API_KEY_PROVIDER_ENV_VARS).isdisjoint(env)
    for name in HOST_SUBSCRIPTION_ENV_VARS:
        assert env.get(name) == f"host-value-for-{name}"


def test_api_keys_are_still_stripped_when_not_opted_in(host_auth, tmp_path):
    """Pre-existing protection must survive the change.

    MUTATION: remove the API-key stripping -> RED.
    """
    os.environ["OPENAI_API_KEY"] = "sk-should-not-survive"
    try:
        universe = tmp_path / "u-newborn"
        universe.mkdir()
        env = subprocess_env_for_provider("claude-code", universe_dir=universe)
        assert "OPENAI_API_KEY" not in env
    finally:
        os.environ.pop("OPENAI_API_KEY", None)


def test_selected_universe_vault_auth_survives_empty_base(
    host_auth, tmp_path, monkeypatch
):
    """When the vault supplies auth, that universe-owned value must survive.

    MUTATION: discard the recognized selected-universe overlay -> RED.
    """
    universe = tmp_path / "u-with-vault"
    universe.mkdir()
    vault_config_dir = universe / ".credentials" / "claude-custom"

    def fake_apply(env, provider_name, *, universe_dir=None):
        env["CLAUDE_CONFIG_DIR"] = str(vault_config_dir)
        return env

    monkeypatch.setattr(
        "tinyassets.credential_vault.apply_provider_auth_env", fake_apply
    )

    env = subprocess_env_for_provider("claude-code", universe_dir=universe)
    assert env.get("CLAUDE_CONFIG_DIR") == str(vault_config_dir), (
        "the vault supplied a credential for this universe and the guard "
        "discarded it; apply the validated overlay after empty-base isolation"
    )


def test_partial_vault_overlay_cannot_retain_alternate_host_auth(
    host_auth, tmp_path, monkeypatch
):
    """MUTATION: seed the selected overlay from the host environment -> RED."""
    universe = tmp_path / "u-partial-vault"
    universe.mkdir()
    vault_config_dir = universe / ".credentials" / "claude-custom"

    def fake_apply(env, provider_name, *, universe_dir=None):
        env["CLAUDE_CONFIG_DIR"] = str(vault_config_dir)
        return env

    monkeypatch.setattr(
        "tinyassets.credential_vault.apply_provider_auth_env", fake_apply
    )

    env = subprocess_env_for_provider("claude-code", universe_dir=universe)

    assert env.get("CLAUDE_CONFIG_DIR") == str(vault_config_dir)
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "CODEX_HOME" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_universe_credential_resolution_failure_is_explicit(
    host_auth, tmp_path, monkeypatch
):
    """MUTATION: swallow a universe-scoped overlay exception -> RED."""
    universe = tmp_path / "u-broken-vault"
    universe.mkdir()

    def broken_apply(env, provider_name, *, universe_dir=None):
        raise RuntimeError("synthetic vault failure secret=do-not-leak")

    monkeypatch.setattr(
        "tinyassets.credential_vault.apply_provider_auth_env", broken_apply
    )

    with pytest.raises(ProviderUnavailableError, match="credential resolution") as exc:
        subprocess_env_for_provider("claude-code", universe_dir=universe)
    assert "do-not-leak" not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert claude_subscription_auth_available(universe) is False
    assert not (universe / ".credentials").exists()


def test_malformed_real_vault_failure_is_sanitized_without_artifact_creation(
    tmp_path,
):
    universe = tmp_path / "u-malformed-vault"
    universe.mkdir()
    # The vault lives in platform custody beside the center, never in its box.
    credential_vault_path(universe).parent.mkdir(parents=True)
    credential_vault_path(universe).write_text(
        '{"credentials": [not-json]}',
        encoding="utf-8",
    )

    with pytest.raises(ProviderUnavailableError, match="credential resolution") as exc:
        subprocess_env_for_provider("claude-code", universe_dir=universe)

    assert "not valid JSON" not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert not (universe / ".credentials").exists()
    assert not (universe / ".runtime").exists()


def test_environment_bound_universe_does_not_inherit_host_auth(
    host_auth, tmp_path, monkeypatch
):
    """Environment binding is universe scope even without an explicit argument."""
    universe = tmp_path / "u-env-bound"
    universe.mkdir()
    monkeypatch.setenv("TINYASSETS_UNIVERSE", str(universe))

    env = subprocess_env_for_provider("claude-code", universe_dir=None)

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env.get("CLAUDE_CONFIG_DIR") != "host-value-for-CLAUDE_CONFIG_DIR"
    assert "CODEX_HOME" not in env
    assert Path(env["CLAUDE_CONFIG_DIR"]).is_relative_to(universe)
    assert "ANTHROPIC_API_KEY" not in env


def test_nonexistent_environment_binding_is_still_universe_scope(
    host_auth, tmp_path, monkeypatch
):
    """MUTATION: require the bound universe path to exist before isolation -> RED."""
    universe = tmp_path / "not-created" / "u-bound"
    monkeypatch.setenv("TINYASSETS_UNIVERSE", str(universe))

    env = subprocess_env_for_provider("claude-code", universe_dir=None)

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert Path(env["CLAUDE_CONFIG_DIR"]).is_relative_to(universe)
    assert "CODEX_HOME" not in env
    assert Path(env["CLAUDE_CONFIG_DIR"]).is_absolute()
    assert "ANTHROPIC_API_KEY" not in env


def test_relative_universe_binding_emits_absolute_child_paths(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    universe = Path("relative-universe")

    env = subprocess_env_for_provider("claude-code", universe_dir=universe)

    canonical_universe = (tmp_path / universe).resolve()
    for name in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "TMPDIR",
        "TMP",
        "TEMP",
        "CLAUDE_CONFIG_DIR",
    ):
        assert Path(env[name]).is_absolute()
        assert Path(env[name]).is_relative_to(canonical_universe)


def test_explicit_universe_overrides_environment_bound_universe(
    tmp_path, monkeypatch
):
    universe_a = tmp_path / "u-env-a"
    universe_b = tmp_path / "u-explicit-b"
    write_credential_vault(
        universe_a,
        [{
            "credential_type": "llm_subscription",
            "service": "claude",
            "claude_config_dir": "auth-a",
        }],
    )
    write_credential_vault(
        universe_b,
        [{
            "credential_type": "llm_subscription",
            "service": "claude",
            "claude_config_dir": "auth-b",
        }],
    )
    monkeypatch.setenv("TINYASSETS_UNIVERSE", str(universe_a))

    env = subprocess_env_for_provider("claude-code", universe_dir=universe_b)

    # A relative config dir resolves inside the center's platform custody.
    assert Path(env["CLAUDE_CONFIG_DIR"]).resolve() == (
        credential_vault_path(universe_b).parent / "auth-b"
    ).resolve()
    assert not (credential_vault_path(universe_a).parent / "auth-a").exists()


def test_host_api_provider_opt_in_does_not_leak_into_universe_cli(
    host_auth, tmp_path, monkeypatch
):
    """MUTATION: retain process-global API auth for a universe -> RED."""
    universe = tmp_path / "u-api-opt-in"
    universe.mkdir()
    monkeypatch.setenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", "1")
    for name in API_KEY_PROVIDER_ENV_VARS:
        monkeypatch.setenv(name, f"host-value-for-{name}")

    env = subprocess_env_for_provider("claude-code", universe_dir=universe)

    leaked = [name for name in API_KEY_PROVIDER_ENV_VARS if name in env]
    assert not leaked, f"universe inherited host API-provider authority: {leaked}"


def test_default_cli_homes_cannot_recover_host_auth(tmp_path, monkeypatch):
    """MUTATION: delete auth-home pinning and let CLIs fall back to HOME -> RED."""
    host_home = tmp_path / "host-home"
    (host_home / ".codex").mkdir(parents=True)
    (host_home / ".claude").mkdir(parents=True)
    (host_home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    (host_home / ".claude" / ".credentials.json").write_text(
        "{}", encoding="utf-8"
    )
    universe = tmp_path / "u-default-home"
    universe.mkdir()

    monkeypatch.setenv("HOME", str(host_home))
    monkeypatch.setenv("USERPROFILE", str(host_home))
    monkeypatch.delenv("TINYASSETS_UNIVERSE", raising=False)
    monkeypatch.setenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", "0")
    for name in HOST_SUBSCRIPTION_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    env = subprocess_env_for_provider("claude-code", universe_dir=universe)
    effective_claude_home = Path(
        env.get("CLAUDE_CONFIG_DIR", host_home / ".claude")
    )

    assert effective_claude_home.is_relative_to(universe)
    assert "CODEX_HOME" not in env
    assert effective_claude_home != host_home / ".claude"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_host_local_execution_does_not_invoke_vault_helpers(host_auth, monkeypatch):
    """A host-local call must not depend on universe vault helpers."""

    def broken_apply(env, provider_name, *, universe_dir=None):
        raise RuntimeError("vault helper must not run for host-local execution")

    monkeypatch.setattr(
        "tinyassets.credential_vault.apply_provider_auth_env", broken_apply
    )

    env = subprocess_env_for_provider("claude-code", universe_dir=None)

    for name in HOST_SUBSCRIPTION_ENV_VARS:
        assert env.get(name) == f"host-value-for-{name}"


def test_universe_child_environment_is_default_deny_with_safe_runtime_basics(
    tmp_path, monkeypatch
):
    """MUTATION: copy the host env before filtering it -> RED."""
    universe = tmp_path / "u-default-deny"
    ca_bundle = tmp_path / "enterprise-ca.pem"
    ca_bundle.write_text("test-ca", encoding="utf-8")
    host_tmp = tmp_path / "host-tmp"
    host_tmp.mkdir()
    host_home = tmp_path / "host-home"
    host_home.mkdir()
    ambient = {
        "PATH": os.environ.get("PATH", ""),
        "LANG": "en_US.UTF-8",
        "LC_CTYPE": "en_US.UTF-8",
        "LC_MONETARY": "en_US.UTF-8",
        "LC_FUTURE_PROVIDER_MASTER_TOKEN": "must-not-enter-child",
        "TZ": "UTC",
        "TERM": "xterm-256color",
        "NO_COLOR": "1",
        "PYTHONUTF8": "1",
        "SSL_CERT_FILE": str(ca_bundle),
        "HOME": str(host_home),
        "USERPROFILE": str(host_home),
        "APPDATA": str(host_home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(host_home / "AppData" / "Local"),
        "XDG_CONFIG_HOME": str(host_home / ".config"),
        "TMPDIR": str(host_tmp),
        "TMP": str(host_tmp),
        "TEMP": str(host_tmp),
        "FUTURE_PROVIDER_MASTER_TOKEN": "future-host-secret",
        "NODE_OPTIONS": "--require=/host/credential-loader.js",
        "SSL_CERT_DIR": str(tmp_path),
    }
    ambient.update({name: f"host-{name}" for name in AMBIENT_CLOUD_AUTH_VARS})
    ambient.update({name: f"http://host-secret@proxy/{name}" for name in AMBIENT_ROUTING_VARS})
    monkeypatch.setattr(os, "environ", ambient)

    env = subprocess_env_for_provider("claude-code", universe_dir=universe)

    inherited_runtime = {
        "PATH",
        "LANG",
        "LC_CTYPE",
        "LC_MONETARY",
        "TZ",
        "TERM",
        "NO_COLOR",
        "PYTHONUTF8",
        "SSL_CERT_FILE",
    }
    isolated_runtime = {
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "TMPDIR",
        "TMP",
        "TEMP",
        "CLAUDE_CONFIG_DIR",
        "AWS_EC2_METADATA_DISABLED",
    }
    windows_runtime = {
        name
        for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE")
        if name in ambient
    }
    windows_home = {"HOMEDRIVE", "HOMEPATH"} if os.name == "nt" else set()
    assert set(env) == (
        inherited_runtime | isolated_runtime | windows_runtime | windows_home
    )
    assert env["SSL_CERT_FILE"] == str(ca_bundle)
    assert env["AWS_EC2_METADATA_DISABLED"] == "true"
    assert not (set(AMBIENT_CLOUD_AUTH_VARS) & set(env))
    assert not (set(AMBIENT_ROUTING_VARS) & set(env))
    assert "FUTURE_PROVIDER_MASTER_TOKEN" not in env
    assert "LC_FUTURE_PROVIDER_MASTER_TOKEN" not in env
    assert "NODE_OPTIONS" not in env
    assert "SSL_CERT_DIR" not in env
    for name in ("TMPDIR", "TMP", "TEMP"):
        assert Path(env[name]).is_relative_to(universe)
        assert Path(env[name]).is_dir()
        assert Path(env[name]) != host_tmp


def test_universe_child_replaces_every_ambient_home_and_profile_root(
    tmp_path, monkeypatch
):
    """MUTATION: retain any host discovery root -> RED."""
    universe = tmp_path / "u-isolated-roots"
    host_root = tmp_path / "host-root"
    host_root.mkdir()
    for name in (
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
    ):
        monkeypatch.setenv(name, str(host_root / name))

    env = subprocess_env_for_provider("claude-code", universe_dir=universe)

    for name in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "CLAUDE_CONFIG_DIR",
    ):
        assert Path(env[name]).is_relative_to(universe)
        assert not Path(env[name]).is_relative_to(host_root)
    if os.name == "nt":
        isolated_profile = Path(env["USERPROFILE"])
        assert env["HOMEDRIVE"].casefold() == isolated_profile.drive.casefold()
        assert env["HOMEPATH"] == str(isolated_profile)[len(isolated_profile.drive):]
    else:
        assert "HOMEDRIVE" not in env
        assert "HOMEPATH" not in env


def test_universe_child_rejects_runtime_root_symlink_escape_before_writes(
    tmp_path,
):
    universe = tmp_path / "u-linked-runtime"
    runtime_root = universe / ".runtime" / "provider-child" / "claude-code"
    runtime_root.mkdir(parents=True)
    outside = tmp_path / "outside-host-home"
    outside.mkdir()
    linked_home = runtime_root / "home"
    _make_directory_link(linked_home, outside)

    with pytest.raises(ProviderUnavailableError, match="credential resolution") as exc:
        subprocess_env_for_provider("claude-code", universe_dir=universe)

    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert list(outside.iterdir()) == []
    assert not (runtime_root / "tmp").exists()
    assert not (runtime_root / "auth-empty").exists()


def test_default_vault_materialization_target_escape_is_rejected_before_helper(
    tmp_path,
):
    universe = tmp_path / "u-linked-credential-root"
    universe.mkdir()
    outside = tmp_path / "outside-credential-root"
    outside.mkdir()
    _make_directory_link(universe / ".credentials", outside)

    with pytest.raises(ProviderUnavailableError, match="credential resolution") as exc:
        subprocess_env_for_provider("claude-code", universe_dir=universe)

    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert list(outside.iterdir()) == []
    assert not (universe / ".runtime").exists()


def test_vault_source_symlink_is_rejected_before_any_helper_side_effect(
    tmp_path,
):
    universe = tmp_path / "u-linked-vault"
    universe.mkdir()
    outside_token = "outside-token-must-never-be-admitted"
    outside_vault = tmp_path / "outside-vault.json"
    _write_outside_vault(outside_vault, outside_token)
    _make_file_link(credential_vault_path(universe), outside_vault)

    with pytest.raises(ProviderUnavailableError, match="credential resolution") as exc:
        subprocess_env_for_provider("claude-code", universe_dir=universe)

    assert outside_token not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert not (universe / ".runtime").exists()
    assert not (universe / ".credentials").exists()


def test_vault_source_hardlink_is_rejected_before_any_helper_side_effect(
    tmp_path,
):
    universe = tmp_path / "u-hardlinked-vault"
    universe.mkdir()
    outside_token = "hardlinked-token-must-never-be-admitted"
    outside_vault = tmp_path / "outside-hardlinked-vault.json"
    _write_outside_vault(outside_vault, outside_token)
    try:
        os.link(outside_vault, credential_vault_path(universe))
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    with pytest.raises(ProviderUnavailableError, match="credential resolution") as exc:
        subprocess_env_for_provider("claude-code", universe_dir=universe)

    assert outside_token not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert not (universe / ".runtime").exists()
    assert not (universe / ".credentials").exists()


@pytest.mark.parametrize("provider_name", ["future-cli", "gemini", "CODEX"])
def test_universe_child_rejects_noncanonical_provider_before_vault_helper(
    provider_name, tmp_path, monkeypatch
):
    universe = tmp_path / "u-provider-name"
    called_helpers: list[str] = []

    def tracking_apply(env, provider_name, *, universe_dir=None):
        called_helpers.append("apply_provider_auth_env")
        return env

    def tracking_resolver(universe_dir):
        called_helpers.append("vault_resolver")
        return None

    monkeypatch.setattr(
        "tinyassets.credential_vault.apply_provider_auth_env", tracking_apply
    )
    monkeypatch.setattr(
        "tinyassets.credential_vault.resolve_claude_config_dir",
        tracking_resolver,
    )
    with pytest.raises(ProviderUnavailableError, match="credential resolution"):
        subprocess_env_for_provider(provider_name, universe_dir=universe)

    assert called_helpers == []
    assert not universe.exists()


def test_universe_child_accepts_only_valid_ca_bundle_files(tmp_path, monkeypatch):
    """MUTATION: copy CA directories, relative paths, or missing files -> RED."""
    universe = tmp_path / "u-ca"
    valid_bundle = tmp_path / "valid-ca.pem"
    valid_bundle.write_text("test-ca", encoding="utf-8")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(valid_bundle))
    monkeypatch.setenv("CURL_CA_BUNDLE", "relative-ca.pem")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", str(tmp_path))
    monkeypatch.setenv("CODEX_CA_CERTIFICATE", str(tmp_path / "missing.pem"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path))

    env = subprocess_env_for_provider("claude-code", universe_dir=universe)

    assert env["REQUESTS_CA_BUNDLE"] == str(valid_bundle)
    assert "CURL_CA_BUNDLE" not in env
    assert "NODE_EXTRA_CA_CERTS" not in env
    assert "CODEX_CA_CERTIFICATE" not in env
    assert "SSL_CERT_DIR" not in env


def test_universe_overlay_cannot_add_arbitrary_environment_authority(
    tmp_path, monkeypatch
):
    """MUTATION: update the child env directly with every helper key -> RED."""
    universe = tmp_path / "u-hostile-overlay"

    def hostile_apply(env, provider_name, *, universe_dir=None):
        env["ANTHROPIC_API_KEY"] = "universe-owned-key"
        env["FUTURE_PROVIDER_MASTER_TOKEN"] = "must-not-enter-child"
        return env

    monkeypatch.setattr(
        "tinyassets.credential_vault.apply_provider_auth_env", hostile_apply
    )

    with pytest.raises(ProviderUnavailableError, match="credential resolution") as exc:
        subprocess_env_for_provider("claude-code", universe_dir=universe)
    assert "must-not-enter-child" not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None


def test_real_vault_outside_path_is_rejected_before_helper_side_effects(
    tmp_path,
):
    universe = tmp_path / "u-outside-vault-path"
    outside = tmp_path / "outside-claude-auth"
    write_credential_vault(
        universe,
        [{
            "credential_type": "llm_subscription",
            "service": "claude",
            "claude_config_dir": str(outside),
        }],
    )

    with pytest.raises(ProviderUnavailableError, match="credential resolution") as exc:
        subprocess_env_for_provider("claude-code", universe_dir=universe)

    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert not outside.exists()
    assert not (universe / ".runtime").exists()
    assert not (universe / ".credentials").exists()


def test_helper_outside_overlay_path_is_rejected_after_helper(
    tmp_path, monkeypatch
):
    universe = tmp_path / "u-outside-overlay"
    outside = tmp_path / "outside-overlay"

    def outside_apply(env, provider_name, *, universe_dir=None):
        env["CLAUDE_CONFIG_DIR"] = str(outside)
        return env

    monkeypatch.setattr(
        "tinyassets.credential_vault.apply_provider_auth_env", outside_apply
    )

    with pytest.raises(ProviderUnavailableError, match="credential resolution") as exc:
        subprocess_env_for_provider("claude-code", universe_dir=universe)

    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None
    assert not outside.exists()


def test_host_local_countercase_preserves_unknown_ambient_authority(
    host_auth, monkeypatch
):
    """No-universe host tooling remains an ordinary inherited environment."""
    monkeypatch.setenv("FUTURE_PROVIDER_MASTER_TOKEN", "host-local-only")

    env = subprocess_env_for_provider("claude-code", universe_dir=None)

    assert env["FUTURE_PROVIDER_MASTER_TOKEN"] == "host-local-only"


def test_host_local_countercase_preserves_future_provider_name(
    host_auth, monkeypatch
):
    monkeypatch.setenv("FUTURE_PROVIDER_MASTER_TOKEN", "host-local-only")

    env = subprocess_env_for_provider("future-cli", universe_dir=None)

    assert env["FUTURE_PROVIDER_MASTER_TOKEN"] == "host-local-only"
