"""Row 5, as an executable gate: one builder shape, many repos and forges.

Tiny's criterion, 2026-09-03, verbatim:

    Pass: starting from one builder shape, I can point it at at least two
    distinct repositories and two distinct outbound destinations by
    inputs/config only, with no branch-code edits, no repo-specific constants,
    and no platform code path that assumes one named repo.
    Fail: any required code change, hidden constant, or service-specific special
    case remains in the platform or canonical builder shape when switching
    target repo/channel.

The substrate is designed for this -- `transport_host_for` says "WHICH host is
the connection's business, not the platform's: github.com, a company GitLab, a
Gitea box", and the per-channel effect sinks were retired in favour of one
generic call. What was missing is a test that would FAIL if that stopped being
true, so the property is held by measurement rather than by intent.

Everything here reuses `test_workspace_effector`'s harness so the packet, the
consents and the ledger are the real ones.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_workspace_effector import (  # noqa: F401 - fixtures come with it
    UNIVERSE,
    EffectChain,
    FakeWorker,
    _packet,
    _principal,
    _run,
    chain,
    fs_spy,
    no_real_git,
)

#: Deliberately not all github.com. A forge the platform has never heard of is
#: the point: if any code path special-cases a vendor, one of these fails.
FORGES = [
    ("github.com", "acme/widgets"),
    ("gitlab.example.net", "team/service"),
    ("gitea.internal", "infra/tooling"),
    ("codeberg.org", "someone/notes"),
]


def _universe_for(tmp_path: Path, host: str, repo: str) -> tuple[Path, Path]:
    """The same wiring for every forge, with ONLY host and repo varying.

    `test_workspace_effector._setup` pins both to its module constants, so this
    parameterises them instead of reusing it -- which is the whole point of the
    row: nothing but inputs should change between forges.
    """
    from tinyassets.effectors import EXTERNAL_WRITE_SINK_WORKSPACE
    from tinyassets.storage.effector_consents import grant_consent
    from tinyassets.storage.outbound_connections import ConnectionLedger
    from tinyassets.storage.workspace_authority import workspace_consent_destination

    principal = _principal()
    data_root = tmp_path / "data"
    universe_dir = data_root / UNIVERSE
    universe_dir.mkdir(parents=True)
    ledger = ConnectionLedger(
        data_root / ".broker" / "outbound.db", data_root=data_root,
        verify_authenticated_principal=lambda: principal
    )
    ledger.create_connection(
        connection_id="conn-git",
        owner_user_id=principal,
        connection_class="outbound-http",
        scopes=(f"git_read:{repo}", f"git_write:{repo}"),
        provider="http",
        destination=f"{host}/{repo}",
        credential_ref="vault://http/forge",
        connection_type="http",
        auth_scheme="bearer",
        allowed_endpoints=[
            {"host": host, "path_template": f"/{repo}", "methods": ["GET"]}
        ],
    )
    ledger.grant_connection(
        grant_id="grant-git",
        connection_id="conn-git",
        owner_user_id=principal,
        universe_id=UNIVERSE,
    )
    for op in ("checkout", "push"):
        grant_consent(
            universe_dir,
            sink=EXTERNAL_WRITE_SINK_WORKSPACE,
            destination=workspace_consent_destination(
                f"workspace_{op}", repo, connection_id="conn-git", host=host
            ),
            granted_by="test",
        )
    return data_root, universe_dir


@pytest.mark.parametrize("host,repo", FORGES)
def test_one_shape_checks_out_any_forge_by_config_alone(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git, host: str, repo: str  # noqa: F811 - pytest fixtures, imported then injected
) -> None:
    """No branch-code edit between these: only the connection and the packet."""
    _root, universe_dir = _universe_for(tmp_path, host, repo)
    result = _run(
        tmp_path,
        _packet(repo=repo),           # the SAME packet shape, a different repo
        universe_dir=universe_dir,
        chain=chain,
    )
    assert result.get("error_kind") is None, (
        f"{host}/{repo} was refused where github.com was not: {result}"
    )


def test_the_transport_follows_the_connection_not_a_platform_default(
    tmp_path: Path, chain: EffectChain, fs_spy, no_real_git  # noqa: F811 - pytest fixtures, imported then injected
) -> None:
    """The host reaching the worker must be the one the OWNER allowlisted.

    A platform default here is the "hidden constant" half of tiny's fail
    criterion: it would send every user's checkout to one forge no matter what
    they configured.
    """
    host, repo = "gitea.internal", "infra/tooling"
    _root, universe_dir = _universe_for(tmp_path, host, repo)
    worker = FakeWorker()
    result = _run(
        tmp_path, _packet(repo=repo), universe_dir=universe_dir,
        chain=chain, worker=worker,
    )
    assert result.get("error_kind") is None, result

    seen = " ".join(str(r) for r in getattr(worker, "requests", []) or [])
    assert host in seen, f"the connection's host never reached the worker: {seen[:400]}"
    assert "github.com" not in seen, (
        f"a github default leaked into a {host} checkout: {seen[:400]}"
    )


def test_no_platform_module_hard_codes_a_repository() -> None:
    """The 'no platform code path that assumes one named repo' clause.

    `auto_ship.py` is exempt by the same rule `check_channel_agnostic.py`
    already encodes: there the platform ships ITS OWN releases to its own forge,
    which no user composes. Everything else must take the repo from its caller.
    """
    import ast

    root = Path(__file__).resolve().parent.parent / "tinyassets"
    exempt = {"auto_ship.py"}
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name in exempt:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        # Docstrings are excluded for the same reason `check_channel_agnostic.py`
        # excludes them: most mentions here EXPLAIN why something is agnostic,
        # and counting prose makes the rule unmeetable and therefore ignored.
        docstrings = {
            id(n.body[0].value)
            for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
            and isinstance(n.body[0].value.value, str)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                source_link = "https://github.com/tinyassets/tinyassets"
                lower = node.value.lower()
                if source_link in lower and (
                    "github repository" in lower or lower.strip() == source_link
                ):
                    # The server's homepage and manifest identify the platform's
                    # own source. They do not select a user's target repository.
                    continue
                # A concrete owner/name pair for the platform's own repo, reaching
                # the runtime as a VALUE rather than described in prose.
                if "tinyassets/tinyassets" in lower:
                    rel = path.relative_to(root.parent).as_posix()
                    offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "these platform modules carry the founder's own repository as a literal, "
        "so a user's builder silently targets it:\n  " + "\n  ".join(offenders)
    )
