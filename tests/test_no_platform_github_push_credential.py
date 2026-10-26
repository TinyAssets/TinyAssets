"""Guard: the platform holds no GitHub push credential.

Until 2026-09-24 the daemon could push to GitHub with a token the PLATFORM
held: a ``TINYASSETS_GITHUB_PUSH_CAPABILITIES`` (legacy
``TINYASSETS_GITHUB_PR_CAPABILITIES``) destination->token map in the host env
file, synced by the deploy from a repository secret and re-minted by a GitHub
App token refresher on a systemd timer. That path is cut. GitHub is a
connection a universe's owner may or may not have made; the platform never
pushes with a token of its own.

This test is red the moment any part of that path comes back: the map reader,
a code reader of the map names, a workflow that syncs or installs them, the
refresher and its units, or a compose / env-template entry. The entrypoint and
``deploy/retire_platform_llm_logins.sh`` are the only files allowed to name
the variables: the first strips them, the second deletes them from the host.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

PUSH_CREDENTIAL_ENV = (
    "TINYASSETS_GITHUB_PUSH_CAPABILITIES",
    "TINYASSETS_GITHUB_PR_CAPABILITIES",
)
RETIRED_WORKFLOW_NAMES = PUSH_CREDENTIAL_ENV + (
    "WORKFLOW_GITHUB_PR_CAPABILITIES",
    "HAS_GITHUB_PR_CAPABILITY",
    "GITHUB_PR_CAPABILITIES_SOURCE",
    "github-app-token-refresher",
)
REFRESHER_FILES = (
    "scripts/github-app-token-refresher.py",
    "deploy/github-app-token-refresher.service",
    "deploy/github-app-token-refresher.timer",
)


def test_the_map_reader_has_no_push_capability():
    from tinyassets.auth import provider

    assert "push" not in provider._GITHUB_SECRET_CAPABILITY_ENVS
    for names in provider._GITHUB_SECRET_CAPABILITY_ENVS.values():
        assert not set(names) & set(PUSH_CREDENTIAL_ENV)


def test_a_push_map_in_the_environment_vends_nothing(monkeypatch):
    from tinyassets.auth.provider import vend_github_destination_secret

    for name in PUSH_CREDENTIAL_ENV:
        monkeypatch.setenv(name, '{"TinyAssets/TinyAssets": "platform-token"}')

    vended = vend_github_destination_secret(
        destination="TinyAssets/TinyAssets", capability="push",
    )

    assert vended["token"] == ""


def _python_sources() -> list[Path]:
    roots = [REPO / "tinyassets", REPO / "domains", REPO / "fantasy_daemon", REPO / "scripts"]
    return [p for root in roots if root.is_dir() for p in root.rglob("*.py")]


def test_no_code_reads_a_platform_push_map():
    pattern = re.compile("|".join(PUSH_CREDENTIAL_ENV))
    readers = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith("#"):
                readers.append(f"{path.relative_to(REPO)}:{lineno}")
    # Only prose may mention the retired names (a comment or docstring that
    # says they are gone); an executable reference is a reader.
    executable = [
        r for r in readers
        if not _is_docstring_line(REPO / r.rsplit(":", 1)[0], int(r.rsplit(":", 1)[1]))
    ]
    assert not executable, f"code still references a platform push map: {executable}"


def _is_docstring_line(path: Path, lineno: int) -> bool:
    """True when ``lineno`` sits inside a module/function docstring or comment block."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(getattr(first, "value", None), ast.Constant)
            and isinstance(first.value.value, str)
            and first.lineno <= lineno <= (first.end_lineno or first.lineno)
        ):
            return True
    return False


def test_the_refresher_and_its_units_are_gone():
    present = [rel for rel in REFRESHER_FILES if (REPO / rel).exists()]
    assert not present, f"GitHub App token refresher files still in the repo: {present}"


@pytest.mark.parametrize(
    "workflow",
    sorted((REPO / ".github" / "workflows").glob("*.yml")),
    ids=lambda p: p.name,
)
def test_no_workflow_syncs_or_installs_a_platform_push_credential(workflow: Path):
    text = workflow.read_text(encoding="utf-8")
    found = [name for name in RETIRED_WORKFLOW_NAMES if name in text]
    assert not found, f"{workflow.name} still handles {found}"


def test_compose_and_env_template_carry_no_push_map():
    for rel in ("deploy/compose.yml", "deploy/tinyassets-env.template"):
        text = (REPO / rel).read_text(encoding="utf-8")
        found = [name for name in PUSH_CREDENTIAL_ENV if name in text]
        assert not found, f"{rel} still carries {found}"


def test_entrypoint_strips_and_retire_script_scrubs_the_push_maps():
    entrypoint = (REPO / "deploy" / "docker-entrypoint.sh").read_text(encoding="utf-8")
    match = re.search(
        r"^_platform_credential_env=\(\n(?P<body>.*?)^\)\n",
        entrypoint,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, "entrypoint strip array missing"
    inside = {line.strip() for line in match.group("body").splitlines()}
    outside = entrypoint[: match.start()] + entrypoint[match.end():]
    for name in PUSH_CREDENTIAL_ENV:
        assert name in inside, f"entrypoint does not strip {name}"
        assert name not in outside, f"entrypoint names {name} outside its strip array"

    retire = (REPO / "deploy" / "retire_platform_llm_logins.sh").read_text(encoding="utf-8")
    for name in PUSH_CREDENTIAL_ENV:
        assert name in retire, f"host cleanup does not scrub {name}"
    assert "github-app-token-refresher.timer" in retire
    assert "github-app-private-key.pem" in retire


def test_gh_token_is_host_backup_only_never_the_daemon():
    """GH_TOKEN is the off-host backup upload token the ROOT backup unit reads
    from the host env file. The daemon process never holds it: the entrypoint
    strips it, and compose never sets it."""
    entrypoint = (REPO / "deploy" / "docker-entrypoint.sh").read_text(encoding="utf-8")
    match = re.search(
        r"^_platform_credential_env=\(\n(?P<body>.*?)^\)\n",
        entrypoint,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match
    inside = {line.strip() for line in match.group("body").splitlines()}
    assert {"GH_TOKEN", "GITHUB_TOKEN"} <= inside, "entrypoint must strip GH_TOKEN/GITHUB_TOKEN"

    compose = (REPO / "deploy" / "compose.yml").read_text(encoding="utf-8")
    assert "GH_TOKEN" not in compose


pytestmark = pytest.mark.verifies('owner-daemon-in-box:T6')
