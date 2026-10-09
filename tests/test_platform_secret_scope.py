"""The daemon and its children hold none of the platform's own secrets.

Found 2026-10-02: the production daemon and its engine MCP children carried the
account-wide DigitalOcean token, the live Stripe key, the tunnel token and the
WorkOS key in their environment, because the daemon loaded the box's whole env
file and the engine server copied ``os.environ``
(docs/concerns/2026-10-02-platform-secrets-in-daemon-env.md).

Values never appear here: every fixture value is a placeholder and every
assertion is on names.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tinyassets import platform_secrets
from tinyassets.platform_secrets import (
    CHILD_FORBIDDEN_ENV,
    DAEMON_FORBIDDEN_ENV,
    DAEMON_ONLY_ENV,
    child_env,
)

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "deploy" / "install-tinyassets-env.sh"
COMPOSE = REPO / "deploy" / "compose.yml"
PACKAGE = REPO / "tinyassets"

_POSIX_SHELL = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="shell helper is exercised on POSIX CI",
)


def _shell_forbidden() -> set[str]:
    text = HELPER.read_text(encoding="utf-8")
    match = re.search(r"^DAEMON_FORBIDDEN_ENV=\(\n(.*?)^\)", text, re.S | re.M)
    assert match, "DAEMON_FORBIDDEN_ENV array not found in install-tinyassets-env.sh"
    return {line.strip() for line in match.group(1).splitlines() if line.strip()}


# ---------------------------------------------------------------------------
# the lists
# ---------------------------------------------------------------------------


def test_the_reported_secrets_are_scoped():
    assert {"DO_API_TOKEN", "CLOUDFLARE_TUNNEL_TOKEN"} <= DAEMON_FORBIDDEN_ENV
    assert {"STRIPE_SECRET_KEY", "WORKOS_API_KEY"} <= DAEMON_ONLY_ENV
    assert DAEMON_FORBIDDEN_ENV | DAEMON_ONLY_ENV <= CHILD_FORBIDDEN_ENV


def test_shell_and_python_forbid_the_same_names():
    assert _shell_forbidden() == set(DAEMON_FORBIDDEN_ENV)


def _shell_retired() -> set[str]:
    text = HELPER.read_text(encoding="utf-8")
    match = re.search(r"^RETIRED_ENV=\(\n(.*?)^\)", text, re.S | re.M)
    assert match, "RETIRED_ENV array not found in install-tinyassets-env.sh"
    return {line.strip() for line in match.group(1).splitlines() if line.strip()}


def test_a_retired_name_is_withheld_at_the_renderer_not_by_one_workflow():
    """Retirement is enforced where daemon.env is WRITTEN, not where it is deployed.

    A deploy-side scrub of the shared env would not be enough on its own, and
    is not what this PR relies on: every ``set`` whose target is the shared env
    renders daemon.env, and apply-daemon-env.yml, deploy/hetzner-bootstrap.sh
    and p0-outage-triage.yml all reach the daemon through this helper without
    passing any workflow's scrub (Codex, 2026-10-03). So the helper's own
    forbidden predicate has to cover retired names, or a stale assignment on
    any host still reaches the daemon -- which is worse than before the name
    was de-listed, because nothing withholds it any more.

    Deleting a retired key from /etc/tinyassets/env is the separate, additive
    half, deferred to branch security/retire-github-oauth-workflow-secret. This
    test is about the withholding, which is what makes the de-listing safe.
    """
    retired = _shell_retired()
    assert retired, "the retirement list exists so a de-listed name is still withheld"
    # The predicate the renderer uses must treat them as forbidden.
    text = HELPER.read_text(encoding="utf-8")
    predicate = re.search(r"is_daemon_forbidden\(\)\s*\{(.*?)\n\}", text, re.S)
    assert predicate, "is_daemon_forbidden not found"
    assert "RETIRED_ENV[@]" in predicate.group(1), (
        "the renderer's forbidden check must include RETIRED_ENV, or a retired "
        "name is only withheld on the deploy-prod path"
    )
    # And the generated header tells whoever reads daemon.env what was dropped.
    assert "${RETIRED_ENV[*]}" in text


def test_a_retired_name_is_gone_from_the_product_not_merely_guarded():
    """A retired name is NOT a platform secret: it has no Python counterpart.

    Listing it in DAEMON_FORBIDDEN_ENV would claim the daemon must be protected
    from a live secret, and would put the channel-specific name back into the
    user substrate that scripts/check_channel_agnostic.py ratchets.
    """
    retired = _shell_retired()
    assert retired.isdisjoint(DAEMON_FORBIDDEN_ENV), (
        "a name cannot be both retired and a live platform secret"
    )
    assert retired.isdisjoint(DAEMON_ONLY_ENV)
    template = (REPO / "deploy/tinyassets-env.template").read_text(encoding="utf-8")
    for name in retired:
        assert not re.search(rf"(?m)^{name}=", template), (
            f"{name} is retired but the template still declares it, so a host "
            "would be told to set it again"
        )
        assert not _readers(name), f"{name} is retired but something reads it: {_readers(name)}"


def _readers(name: str) -> set[str]:
    own = Path(platform_secrets.__file__).resolve()
    found = set()
    for path in PACKAGE.rglob("*.py"):
        if path.resolve() == own:
            continue
        if name in path.read_text(encoding="utf-8", errors="replace"):
            found.add(path.relative_to(REPO).as_posix())
    return found


#: Reads a forbidden name but is never imported by the daemon (asserted below).
_UNIMPORTED_READERS = {"tinyassets/host_pool/client.py"}


@pytest.mark.parametrize("name", sorted(DAEMON_FORBIDDEN_ENV))
def test_no_daemon_code_reads_a_forbidden_name(name):
    """Removing a name from the daemon is safe only while nothing reads it."""
    assert _readers(name) - _UNIMPORTED_READERS == set(), (
        f"{name} is now read by daemon code; it cannot be withheld from the daemon"
    )


def test_host_pool_is_not_imported_by_the_daemon():
    importers = {
        path.relative_to(REPO).as_posix()
        for path in PACKAGE.rglob("*.py")
        if "host_pool" not in path.parts
        and re.search(
            r"^\s*(?:from|import)\s+[\w.]*host_pool\b|import_module\([^)]*host_pool",
            path.read_text(encoding="utf-8", errors="replace"),
            re.M,
        )
    }
    assert importers == set(), (
        "host_pool is imported now; it reads SUPABASE_SERVICE_ROLE_KEY, which the "
        "daemon no longer receives"
    )


@pytest.mark.parametrize("name", sorted(DAEMON_ONLY_ENV))
def test_daemon_only_names_are_read_by_daemon_routes_only(name):
    """Billing and account deletion are daemon HTTP routes; no child serves them."""
    allowed = {"tinyassets/billing/stripe_adapter.py", "tinyassets/account_deletion.py"}
    assert _readers(name) <= allowed


# ---------------------------------------------------------------------------
# the daemon container
# ---------------------------------------------------------------------------


def _daemon_service() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["daemon"]


def test_daemon_loads_the_rendered_env_file_not_the_secret_store():
    env_files = _daemon_service()["env_file"]
    assert "/etc/tinyassets/daemon.env" in env_files
    assert "/etc/tinyassets/env" not in env_files


def test_daemon_environment_block_names_no_forbidden_secret():
    environment = _daemon_service().get("environment") or {}
    assert set(environment) & DAEMON_FORBIDDEN_ENV == set()


def test_tunnel_still_gets_its_token_from_interpolation():
    """The split must not take the public surface down with it."""
    tunnel = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["cloudflared"]
    assert "${CLOUDFLARE_TUNNEL_TOKEN}" in tunnel["command"]


# ---------------------------------------------------------------------------
# children
# ---------------------------------------------------------------------------


def test_child_env_removes_every_platform_secret_and_keeps_the_rest():
    source = {name: "placeholder" for name in CHILD_FORBIDDEN_ENV}
    source.update({"TINYASSETS_DATA_DIR": "/data", "UNRELATED": "kept"})
    assert child_env(source) == {"TINYASSETS_DATA_DIR": "/data", "UNRELATED": "kept"}


def test_http_engine_config_carries_only_its_route_and_no_ambient_secrets(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tinyassets import credential_vault, engine_mcp_http
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.claude_provider import _engine_mcp_flags

    sentinels = {name: f"SENTINEL-{name}" for name in CHILD_FORBIDDEN_ENV}
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    route = SimpleNamespace(url="http://127.0.0.1:8790/mcp", secret="owner-route",
                            grant_key="grant-proof")
    monkeypatch.setattr(engine_mcp_http, "read_engine_mcp_route", lambda **kw: route)
    monkeypatch.setattr(credential_vault, "_write_exclusive_snapshot_file",
                        lambda path, data: path.write_bytes(data))
    config = ModelConfig(engine_mcp_actor_id="owner", engine_mcp_graph_id="home",
                         credential_snapshot_dir=tmp_path)
    flags = _engine_mcp_flags(config, tmp_path)
    assert "--strict-mcp-config" in flags
    path = Path(flags[flags.index("--mcp-config") + 1])
    assert path.parent == tmp_path
    document = json.loads(path.read_text())
    server = document["mcpServers"]["tinyassets"]
    assert set(server) == {"type", "url", "headers"}
    assert server["type"] == "http" and server["url"].startswith(route.url)
    assert server["headers"] == {"Authorization": "Bearer owner-route"}
    assert not any(value in path.read_text() for value in sentinels.values())


def test_missing_http_engine_route_never_falls_back_to_a_spawn(tmp_path, monkeypatch):
    from tinyassets import engine_mcp_http
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.claude_provider import _engine_mcp_flags

    monkeypatch.setattr(engine_mcp_http, "read_engine_mcp_route", lambda **kw: None)
    config = ModelConfig(engine_mcp_actor_id="owner", engine_mcp_graph_id="home",
                         credential_snapshot_dir=tmp_path)
    assert _engine_mcp_flags(config, tmp_path) == []
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# the renderer
# ---------------------------------------------------------------------------


def _helper(tmp_path: Path, args: list[str], source: Path, daemon: Path, stdin: str = ""):
    env = os.environ.copy()
    env.update({
        "TINYASSETS_ENV_FILE": str(source),
        "TINYASSETS_LEGACY_ENV_FILE": str(tmp_path / "no-legacy"),
        "TINYASSETS_ENV_OWNER": "",
        "TINYASSETS_ENV_READ_USER": "",
        "TINYASSETS_DAEMON_ENV_SOURCE": str(source),
        "TINYASSETS_DAEMON_ENV_FILE": str(daemon),
    })
    return subprocess.run(
        ["bash", str(HELPER), *args], input=stdin, text=True, capture_output=True,
        cwd=tmp_path, env=env, check=False,
    )


def _assigned(path: Path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^export\s+", "", line)
        names.add(re.split(r"\s*[=:]", line, maxsplit=1)[0])
    return names


@_POSIX_SHELL
def test_render_removes_every_compose_spelling_of_a_forbidden_name(tmp_path):
    source = tmp_path / "env"
    daemon = tmp_path / "daemon.env"
    source.write_text(
        "TINYASSETS_IMAGE=ghcr.io/x@sha256:abc\n"
        "DO_API_TOKEN=placeholder-do\n"
        "  export CLOUDFLARE_TUNNEL_TOKEN = placeholder-cf\n"
        "BETTERSTACK_SOURCE_TOKEN: placeholder-bs\n"
        "STRIPE_SECRET_KEY=placeholder-stripe\n"
        "# a comment kept verbatim\n"
        "WORKOS_API_KEY='placeholder-workos'\n",
        encoding="utf-8",
    )

    result = _helper(tmp_path, ["render-daemon-env"], source, daemon)

    assert result.returncode == 0, result.stderr
    assert _assigned(daemon) == {"TINYASSETS_IMAGE", "STRIPE_SECRET_KEY", "WORKOS_API_KEY"}
    text = daemon.read_text(encoding="utf-8")
    assert "# a comment kept verbatim\n" in text
    assert "placeholder-do" not in text and "placeholder-cf" not in text
    assert "placeholder-bs" not in text
    # The report names what it removed and never prints a value.
    assert "DO_API_TOKEN" in result.stdout
    assert "placeholder" not in result.stdout + result.stderr


@_POSIX_SHELL
def test_render_refuses_a_forbidden_value_that_continues_past_its_line(tmp_path):
    source = tmp_path / "env"
    daemon = tmp_path / "daemon.env"
    source.write_text(
        'KEEP=1\nDO_API_TOKEN="placeholder-start\nplaceholder-rest"\n', encoding="utf-8"
    )

    result = _helper(tmp_path, ["render-daemon-env"], source, daemon)

    assert result.returncode == 7
    assert not daemon.exists()
    assert "placeholder" not in result.stdout + result.stderr


@_POSIX_SHELL
def test_every_write_to_the_source_re_renders_the_daemon_copy(tmp_path):
    source = tmp_path / "env"
    daemon = tmp_path / "daemon.env"
    source.write_text("DO_API_TOKEN=placeholder\n", encoding="utf-8")

    set_result = _helper(tmp_path, ["set", "TINYASSETS_IMAGE"], source, daemon, stdin="ref-1\n")
    assert set_result.returncode == 0, set_result.stderr
    assert _assigned(daemon) == {"TINYASSETS_IMAGE"}

    delete_result = _helper(tmp_path, ["delete", "TINYASSETS_IMAGE"], source, daemon)
    assert delete_result.returncode == 0, delete_result.stderr
    assert _assigned(daemon) == set()


@_POSIX_SHELL
def test_a_write_to_another_env_file_renders_nothing(tmp_path):
    other = tmp_path / "request-idempotency.env"
    daemon = tmp_path / "daemon.env"
    env = os.environ.copy()
    env.update({
        "TINYASSETS_ENV_FILE": str(other),
        "TINYASSETS_LEGACY_ENV_FILE": str(tmp_path / "no-legacy"),
        "TINYASSETS_ENV_OWNER": "",
        "TINYASSETS_ENV_READ_USER": "",
        "TINYASSETS_DAEMON_ENV_SOURCE": str(tmp_path / "env"),
        "TINYASSETS_DAEMON_ENV_FILE": str(daemon),
    })
    result = subprocess.run(
        ["bash", str(HELPER), "set", "SOME_KEY"], input="v\n", text=True,
        capture_output=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not daemon.exists()


@_POSIX_SHELL
def test_forbidden_names_subcommand_prints_the_list(tmp_path):
    result = subprocess.run(
        ["bash", str(HELPER), "daemon-forbidden-names"], text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert set(result.stdout.split()) == set(DAEMON_FORBIDDEN_ENV)


@_POSIX_SHELL
@pytest.mark.parametrize(
    "source_text",
    [
        'KEEP="ok" DO_API_TOKEN=placeholder\n',  # Compose reads a second assignment
        "KEEP='never closed\n",
        'KEEP="a\nb" DO_API_TOKEN=placeholder\n',
    ],
)
def test_render_refuses_what_it_cannot_place_exactly(tmp_path, source_text):
    source = tmp_path / "env"
    daemon = tmp_path / "daemon.env"
    source.write_text(source_text, encoding="utf-8")

    result = _helper(tmp_path, ["render-daemon-env"], source, daemon)

    assert result.returncode == 7
    assert not daemon.exists()
    assert "placeholder" not in result.stdout + result.stderr


@_POSIX_SHELL
def test_render_keeps_a_multiline_value_verbatim_even_when_a_line_looks_forbidden(tmp_path):
    source = tmp_path / "env"
    daemon = tmp_path / "daemon.env"
    kept = 'PEM="-----BEGIN-----\nDO_API_TOKEN=this-is-inside-the-value\n-----END-----"\n'
    source.write_text(kept + 'DO_API_TOKEN="placeholder" # trailing comment\n', encoding="utf-8")

    result = _helper(tmp_path, ["render-daemon-env"], source, daemon)

    assert result.returncode == 0, result.stderr
    text = daemon.read_text(encoding="utf-8")
    assert kept in text
    assert "placeholder" not in text


@_POSIX_SHELL
def test_render_drops_a_bom_that_the_header_would_move_off_byte_zero(tmp_path):
    source = tmp_path / "env"
    daemon = tmp_path / "daemon.env"
    source.write_bytes("\ufeffKEEP=1\n".encode("utf-8"))

    result = _helper(tmp_path, ["render-daemon-env"], source, daemon)

    assert result.returncode == 0, result.stderr
    assert "\ufeff" not in daemon.read_text(encoding="utf-8")
    assert "KEEP=1\n" in daemon.read_text(encoding="utf-8")


@_POSIX_SHELL
def test_a_write_the_daemon_copy_cannot_follow_leaves_the_source_unchanged(tmp_path):
    """Committing the source first would leave daemon.env stale, and the unit
    refuses to start on a stale copy."""
    source = tmp_path / "env"
    daemon = tmp_path / "daemon.env"
    original = "KEEP=1\n"
    source.write_text(original, encoding="utf-8")

    result = _helper(
        tmp_path, ["set", "DO_API_TOKEN"], source, daemon, stdin='"opens\ncloses"'
    )

    assert result.returncode == 7
    assert source.read_text(encoding="utf-8") == original
    assert not daemon.exists()


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(["docker", "compose", "version"], capture_output=True, check=False)
    return probe.returncode == 0


def _compose_environment(env_file: Path, work: Path) -> dict:
    """Compose's reading of *env_file*, with a project env that holds every
    forbidden name, as `--env-file /etc/tinyassets/env` does in production: a
    bare declaration left in the daemon copy would be filled from it."""
    project = work / "project.env"
    project.write_text(
        "".join(f"{name}=from-project\n" for name in sorted(DAEMON_FORBIDDEN_ENV)),
        encoding="utf-8",
    )
    compose = work / "compose.yml"
    compose.write_text(
        f"services:\n  x:\n    image: busybox\n    env_file:\n      - {env_file.as_posix()}\n",
        encoding="utf-8",
    )
    rendered = subprocess.run(
        ["docker", "compose", "--env-file", str(project), "-f", str(compose),
         "config", "--format", "json"],
        capture_output=True, text=True, cwd=work, check=True,
    )
    return json.loads(rendered.stdout)["services"]["x"].get("environment") or {}


@_POSIX_SHELL
@pytest.mark.skipif(not _compose_available(), reason="needs docker compose")
@pytest.mark.parametrize(
    "source_text",
    [
        "A=1\nDO_API_TOKEN=x\nB=two words\n",
        "export A = 1\n  CLOUDFLARE_TUNNEL_TOKEN : y\nB: 2\n",
        "\ufeffA=1\nDO_API_TOKEN=x\n",
        "\ufeffDO_API_TOKEN=x\nA=1\n",
        'DO_API_TOKEN="v" # c\nA="q" # c\n',
        "A='lit $X'\nDO_API_TOKEN='z'\n",
        'A="line1\nDO_API_TOKEN=inner\nline3"\nB=2\n',
        "A='l1\nDO_API_TOKEN=inner'\nB=2\n",
        'A="say \\"hi\\""\nDO_API_TOKEN=x\n',
        "A=1\r\nDO_API_TOKEN=x\r\nB=2\r\n",
        "DO_API_TOKEN=a\nDO_API_TOKEN=b\nA=1\n",
        "A='it\\'s valid'\nDO_API_TOKEN=x\n",
        "A='abc\\\\'\nB=1\n",
        "A='l1\\'\nDO_API_TOKEN=inner'\nB=2\n",
        "DO_API_TOKEN=x\nDO_API_TOKEN\nB=1\n",
        "export CLOUDFLARE_TUNNEL_TOKEN\nB=1\n",
        "B\nC=1\n",
    ],
)
def test_render_matches_composes_own_parser(tmp_path, source_text):
    """The daemon copy, read by Compose, is the source read by Compose minus
    the forbidden names: same keys, same values."""
    source = tmp_path / "env"
    daemon = tmp_path / "daemon.env"
    source.write_bytes(source_text.encode("utf-8"))

    result = _helper(tmp_path, ["render-daemon-env"], source, daemon)

    assert result.returncode == 0, result.stderr
    expected = {
        key: value
        for key, value in _compose_environment(source, tmp_path).items()
        if key not in DAEMON_FORBIDDEN_ENV
    }
    assert _compose_environment(daemon, tmp_path) == expected


@_POSIX_SHELL
@pytest.mark.parametrize("declaration", ["DO_API_TOKEN", "  export DO_API_TOKEN  "])
def test_render_removes_a_bare_forbidden_declaration(tmp_path, declaration):
    """Compose fills a bare name from the project environment, which in
    production is the host env file holding the real value."""
    source = tmp_path / "env"
    daemon = tmp_path / "daemon.env"
    source.write_text(f"KEEP=1\n{declaration}\nBARE_KEPT\n", encoding="utf-8")

    result = _helper(tmp_path, ["render-daemon-env"], source, daemon)

    assert result.returncode == 0, result.stderr
    body = [
        line for line in daemon.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert body == ["KEEP=1", "BARE_KEPT"]


@_POSIX_SHELL
def test_render_accepts_an_escaped_quote_inside_single_quotes(tmp_path):
    source = tmp_path / "env"
    daemon = tmp_path / "daemon.env"
    source.write_text("A='it\\'s valid'\nDO_API_TOKEN=placeholder\n", encoding="utf-8")

    result = _helper(tmp_path, ["render-daemon-env"], source, daemon)

    assert result.returncode == 0, result.stderr
    assert "A='it\\'s valid'\n" in daemon.read_text(encoding="utf-8")


@_POSIX_SHELL
def test_a_source_that_cannot_be_read_installs_nothing(tmp_path):
    """`-r` passes on a directory and the read then fails; that must not
    install an empty daemon file."""
    source = tmp_path / "env"
    source.mkdir()
    daemon = tmp_path / "daemon.env"

    result = _helper(tmp_path, ["render-daemon-env"], source, daemon)

    assert result.returncode == 7
    assert not daemon.exists()
