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

import ast
import importlib
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


def test_engine_mcp_server_child_gets_a_scrubbed_env(monkeypatch):
    from tinyassets import engine_mcp_http

    for name in CHILD_FORBIDDEN_ENV:
        monkeypatch.setenv(name, "placeholder")
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    captured = {}

    class _Proc:
        def poll(self):
            return None

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return _Proc()

    monkeypatch.setattr(engine_mcp_http.subprocess, "Popen", fake_popen)
    server = engine_mcp_http._EngineServer("universe-a", "user:owner", 8790, "/data")
    assert server.start() is True

    env = captured["env"]
    assert captured["argv"][-1] == "tinyassets.engine_mcp_server"
    assert set(env) & CHILD_FORBIDDEN_ENV == set()
    # Its own pins and ordinary configuration still arrive.
    assert env["TINYASSETS_ENGINE_GRAPH_ID"] == "universe-a"
    assert env["TINYASSETS_ENGINE_ACTOR_ID"] == "user:owner"
    assert env["TINYASSETS_ENGINE_MCP_TOOLS"] == "1"
    assert env["TINYASSETS_DATA_DIR"] == "/data"


#: The engine child's environment is built in ONE place. It used to be the
#: stdio ``server_env`` inside ``claude_provider._engine_mcp_flags``; the
#: per-owner isolation cutover deleted the stdio engine (an engine inside the
#: owner's cell cannot reach the platform's stores, and one outside it would be
#: an unconfined daemon child), so the only engine the daemon spawns is the
#: pinned loopback server here.
_ENGINE_SERVER = PACKAGE / "engine_mcp_http.py"


def _server_env_builder(source: str) -> tuple[ast.AST, ast.AST]:
    """The function that builds the engine child's ``env``, and its module.

    Located by shape, not by name: the assignment whose value is a
    ``child_env(...)`` call. That call IS the scrub, so a rename of the
    enclosing function cannot quietly move the subject out from under this
    test, and a builder that stops scrubbing loses it loudly.
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "env" for t in sub.targets)
                    and isinstance(sub.value, ast.Call)
                    and isinstance(sub.value.func, ast.Name)
                    and sub.value.func.id == "child_env"):
                return node, tree
    raise AssertionError(
        f"{_ENGINE_SERVER.name} no longer builds the engine child env with "
        "child_env(); this test has lost its subject"
    )


def _rebound_at(tree: ast.AST, name: str) -> list[int]:
    """Lines where ``name`` is bound by anything other than an import.

    A name resolved through its import statement is only trustworthy if the
    import is the ONLY thing that binds it. Codex round 3 on #4267: inserting
    ``TREE_ENV = "DO_API_TOKEN"`` above the environ reads satisfied an
    import-only resolver while forwarding a forbidden credential. Every binding
    form is enumerated here so a rebinding cannot hide in an unusual one.
    """
    lines: list[int] = []

    def _stores(target: ast.AST) -> list[ast.Name]:
        return [
            n for n in ast.walk(target)
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))
        ]

    for node in ast.walk(tree):
        targets: list[ast.AST] = []
        if isinstance(node, (ast.Assign, ast.Delete)):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = [node.target]
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            targets = [node.target]
        elif isinstance(node, ast.withitem):
            targets = [node.optional_vars] if node.optional_vars is not None else []
        elif isinstance(node, ast.ExceptHandler):
            if node.name == name:
                lines.append(node.lineno)
            continue
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            if name in node.names:
                lines.append(node.lineno)
            continue
        elif isinstance(node, (ast.MatchAs, ast.MatchStar)):
            if node.name == name:
                lines.append(node.lineno)
            continue
        elif isinstance(node, ast.MatchMapping):
            if node.rest == name:
                lines.append(node.lineno)
            continue
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                lines.append(node.lineno)
            args = getattr(node, "args", None)
            if args is not None:
                for arg in [
                    *args.posonlyargs, *args.args, *args.kwonlyargs,
                    args.vararg, args.kwarg,
                ]:
                    if arg is not None and arg.arg == name:
                        lines.append(arg.lineno)
            continue
        else:
            continue
        for target in targets:
            lines.extend(n.lineno for n in _stores(target) if n.id == name)
    return sorted(set(lines))


def _resolve_env_name(key: ast.AST, source: str) -> str:
    """The literal env name a config key refers to.

    Names are module constants (``TREE_ENV``), imported either at module scope
    or inside the function. Resolve through the real module so a rename cannot
    quietly turn a pin into a different variable than the test believes.
    """
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        return key.value
    assert isinstance(key, ast.Name), (
        f"environment key is a computed expression ({ast.dump(key)}); it must be a "
        "literal or a named constant so the name is auditable here"
    )
    for module, imported in re.findall(r"(?m)^\s*from ([\w.]+) import ([^\n(]+)$", source):
        if key.id in {part.strip().split(" as ")[0] for part in imported.split(",")}:
            return getattr(importlib.import_module(module), key.id)
    raise AssertionError(f"could not resolve the env name behind {key.id}")


#: The one name the engine child may inherit BESIDE the scrubbed base. A 32-hex
#: process-tree id (``owner_lease.TREE_ENV``) whose documented purpose is to be
#: inherited.
_FORWARDABLE = {"TINYASSETS_OWNER_TREE"}


def _scrubbed_bulk_reads(func: ast.AST) -> list[ast.AST]:
    """``child_env(os.environ)`` accesses: the ONE sanctioned bulk read.

    ``child_env`` is itself proved to drop every ``CHILD_FORBIDDEN_ENV`` name
    (``test_child_env_removes_every_platform_secret_and_keeps_the_rest``), so
    handing it the whole environment is auditable. A raw ``os.environ.copy()``,
    ``**os.environ`` or ``os.environ`` passed anywhere else is not, and is
    counted as an unaccounted access below.
    """
    found: list[ast.AST] = []
    for node in ast.walk(func):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "child_env"):
            continue
        for argument in [*node.args, *(kw.value for kw in node.keywords)]:
            if isinstance(argument, ast.Attribute) and argument.attr == "environ":
                found.append(argument)
    return found


def _forwarded_env_names(source: str) -> set[str]:
    """Every env name the engine child inherits by name, or AssertionError.

    Rejects an unscrubbed wholesale read in any form, a computed key, and --
    since the name behind a constant is resolved through its import -- any
    REBINDING of that constant, which would otherwise let the resolver believe
    one name while the code forwarded another.
    """
    func, tree = _server_env_builder(source)
    accesses = [
        node for node in ast.walk(func)
        if isinstance(node, ast.Attribute) and node.attr == "environ"
    ]
    scrubbed = _scrubbed_bulk_reads(func)
    assert len(scrubbed) == 1, (
        f"{len(scrubbed)} scrubbed bulk read(s) of os.environ: the engine child "
        "env is built from exactly one child_env(os.environ) base"
    )
    keys: list[ast.AST] = []
    for node in ast.walk(func):
        # environ["NAME"]
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "environ":
            keys.append(node.slice)
        # environ.get("NAME", ...)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" \
                and isinstance(node.func.value, ast.Attribute) \
                and node.func.value.attr == "environ":
            assert node.args, "environ.get() with no name reads the whole environment"
            keys.append(node.args[0])

    # Every environ access is accounted for: either the one scrubbed base, or a
    # single-key read. Any other bulk form (.copy(), ** unpacking, passing
    # environ itself) leaves an access with no match and fails here.
    assert len(accesses) == len(keys) + len(scrubbed), (
        f"{len(accesses)} environ access(es) but only {len(keys)} single-key "
        f"read(s) plus {len(scrubbed)} scrubbed base: the engine child must "
        "never inherit the environment wholesale unscrubbed"
    )

    forwarded: set[str] = set()
    for key in keys:
        forwarded.add(_resolve_env_name(key, source))
        if isinstance(key, ast.Name):
            # Resolution trusted the import; prove the import is the only binder.
            rebound = _rebound_at(tree, key.id)
            assert not rebound, (
                f"{key.id} is resolved through its import but is also rebound at "
                f"line(s) {rebound}: the forwarded name is not what this test "
                "resolved, so the value cannot be audited here"
            )
    return forwarded


def _engine_route(root: Path, *, actor: str, graph: str) -> None:
    """Publish one live engine route, the way the daemon's supervisor does."""
    from tinyassets.engine_mcp_http import ROUTES_FILENAME

    (root / ROUTES_FILENAME).write_text(
        json.dumps({graph: {
            "version": 1, "actor_id": actor, "port": 8790,
            "url": "http://127.0.0.1:8790/mcp", "secret": "s" * 43,
            "grant_key": "g" * 43,
        }}),
        encoding="utf-8",
    )


def test_the_sealed_engine_mcp_config_carries_no_platform_secret(
    tmp_path, monkeypatch,
):
    """BEHAVIOURAL: build the real config and look at what actually lands in it.

    The source-level checks below can only reason about the code as written.
    This one sets a distinguishable sentinel for every ``CHILD_FORBIDDEN_ENV``
    name, builds the engine MCP config through the real ``_engine_mcp_flags``
    path, and asserts none of those sentinels appears -- as a key or as a
    value -- anywhere in the file the provider cell is handed. Independent of
    how the config is spelled.

    The per-owner isolation cutover made this file the WHOLE channel: there is
    no stdio engine and no ``env`` block any more, because the provider CLI
    runs in the owner's cell and the sealed launch snapshot is the only
    daemon-written state it receives. So a platform secret could only reach the
    engine by being written into this config.
    """
    from tests.engine_authority_helpers import seed_engine_authority
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.claude_provider import _engine_mcp_flags

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    sentinels = {name: f"SENTINEL-{name}" for name in CHILD_FORBIDDEN_ENV}
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TINYASSETS_OWNER_TREE", "a" * 32)

    actor, graph = "user:owner", "universe-a"
    seed_engine_authority(tmp_path, actor=actor, graph=graph)
    _engine_route(tmp_path, actor=actor, graph=graph)
    universe_dir = tmp_path / graph
    universe_dir.mkdir(exist_ok=True)
    snapshot = tmp_path / "launch-snapshot"
    snapshot.mkdir()

    flags = _engine_mcp_flags(
        ModelConfig(
            engine_mcp_enabled=True,
            engine_mcp_actor_id=actor,
            engine_mcp_graph_id=graph,
            credential_snapshot_dir=snapshot,
        ),
        universe_dir,
    )
    assert flags, "the engine MCP config was not written, so nothing was proved"
    assert "--strict-mcp-config" in flags, (
        "without strict mode the CLI also grants the logged-in account's "
        "connectors, and this config is no longer the whole channel"
    )
    raw = Path(flags[flags.index("--mcp-config") + 1]).read_text(encoding="utf-8")
    server = json.loads(raw)["mcpServers"]["tinyassets"]
    assert server["type"] == "http", (
        "expected the owner's HTTP engine route; the config shape moved and "
        "this test no longer covers what it was written for"
    )

    leaked_keys = sorted(name for name in CHILD_FORBIDDEN_ENV if name in raw)
    assert not leaked_keys, f"platform secret(s) named in the engine config: {leaked_keys}"
    leaked_values = sorted(name for name, value in sentinels.items() if value in raw)
    assert not leaked_values, (
        f"platform secret VALUE(s) reached the engine config: {leaked_values}"
    )
    # Not vacuous: the owner's own route and bearer DO arrive, so a scan that
    # found nothing cannot be mistaken for a config that carried nothing.
    assert server["url"].startswith("http://127.0.0.1:8790/mcp")
    assert server["headers"]["Authorization"] == "Bearer " + "s" * 43


def test_the_engine_server_child_inherits_no_platform_secret():
    """SOURCE-LEVEL: only auditable pins beside one scrubbed base.

    Originally a substring ban on ``os.environ``. That over-fired once
    execution-owner-lease D2 (#4308) began forwarding one named, non-secret
    variable, so the check now enforces the invariant it was a proxy for: every
    ``environ`` access must be the one ``child_env(os.environ)`` base or a
    single-key read, each key must resolve to a name, and that name must be
    allowlisted and not a platform secret.
    """
    forwarded = _forwarded_env_names(_ENGINE_SERVER.read_text(encoding="utf-8"))
    assert forwarded <= _FORWARDABLE, (
        f"new name(s) forwarded into the engine child env: "
        f"{sorted(forwarded - _FORWARDABLE)}. Adding one is a deliberate "
        "inheritance decision -- classify it in "
        "docs/reference/environment-variables.md first."
    )
    # And whatever is forwarded is not one of the platform's own secrets.
    assert forwarded.isdisjoint(CHILD_FORBIDDEN_ENV), (
        f"{sorted(forwarded & CHILD_FORBIDDEN_ENV)} is a platform secret and cannot "
        "be forwarded to the engine child"
    )


def test_rebinding_a_resolved_env_constant_is_rejected():
    """The import-only resolver is not fooled by a later reassignment.

    Codex round 3 on #4267: resolving ``TREE_ENV`` from its import without
    checking for a rebinding let a mutation insert ``TREE_ENV = "DO_API_TOKEN"``
    above the reads -- the resolver still reported the imported value while the
    code forwarded a forbidden credential. Mutated SOURCE is fed to the checker;
    the real file is never edited.
    """
    source = _ENGINE_SERVER.read_text(encoding="utf-8")
    assert "DO_API_TOKEN" in DAEMON_FORBIDDEN_ENV, "the mutation must name a real secret"
    needle = "        if os.environ.get(TREE_ENV):"
    assert needle in source, "the forwarding shape moved; update this mutation"
    mutated = source.replace(
        needle, '        TREE_ENV = "DO_API_TOKEN"\n' + needle, 1,
    )
    assert mutated != source

    # Unmutated source is accepted, so the failure below is the mutation.
    assert _forwarded_env_names(source) == _FORWARDABLE
    with pytest.raises(AssertionError, match="rebound at line"):
        _forwarded_env_names(mutated)


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
