r"""`deployed_sha --assert-contains` judges non-runtime commits by the deploy chain's classifier.

`deployed_sha.py` answers Hard Rule 14 ("merged is not deployed"). A commit
that changes nothing production runs is never built -- build-image.yml cancels
itself for it (scripts/runtime_paths.py) -- so its sha can never appear in the
release receipt. Before 2026-09-24 the gate returned 1 for such a commit and
explained that nothing was waiting; that was true but still read as a failure,
and once docs-only merges stopped deploying it would have "failed" every such
merge forever.

The contract now: a commit that DESCENDS from the served sha and changes no
runtime input since is served (exit 0, labelled runtime-equivalent). A commit
with undeployed runtime changes between it and the served sha -- including a
docs merge sitting on top of an undeployed runtime merge -- is still exit 1,
and names the runtime paths waiting to deploy.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tests.runtime_repo_fixture import make_repo

REPO_ROOT = Path(__file__).resolve().parents[1]

_SPEC = importlib.util.spec_from_file_location(
    "deployed_sha_for_runtime_test", REPO_ROOT / "scripts" / "deployed_sha.py"
)
assert _SPEC is not None and _SPEC.loader is not None
deployed_sha = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = deployed_sha
_SPEC.loader.exec_module(deployed_sha)


@pytest.fixture
def history(tmp_path, monkeypatch):
    repo, base = make_repo(tmp_path / "repo")
    monkeypatch.setattr(deployed_sha, "REPO_ROOT", repo.root)
    return repo, base


def _serve(monkeypatch, sha: str) -> None:
    monkeypatch.setattr(
        deployed_sha,
        "live_release_state",
        lambda url, timeout: {
            "git_sha": sha,
            "image_tag": f"ghcr.io/o/tinyassets-daemon:{sha[:12]}",
        },
    )


def test_docs_only_commit_on_top_of_the_served_sha_is_served(history, monkeypatch, capsys):
    repo, base = history
    docs = repo.commit("docs", {"docs/notes.md": "more\n"})
    _serve(monkeypatch, base)

    assert deployed_sha.main(["--assert-contains", docs]) == 0
    out = capsys.readouterr().out
    assert "runtime-equivalent" in out


def test_root_doc_edit_on_top_of_the_served_sha_is_served(history, monkeypatch):
    """A repo-root doc is in no COPY source and in no host input."""
    repo, base = history
    head = repo.commit("root doc", {"AGENTS.md": "agents x\n"})
    _serve(monkeypatch, base)

    assert deployed_sha.main(["--assert-contains", head]) == 0


def test_docs_merge_on_an_undeployed_runtime_merge_is_not_served(history, monkeypatch, capsys):
    """The runtime merge never deployed; the docs merge after it must not pass."""
    repo, base = history
    repo.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    docs = repo.commit("docs", {"docs/notes.md": "more\n"})
    _serve(monkeypatch, base)

    assert deployed_sha.main(["--assert-contains", docs]) == 1
    err = capsys.readouterr().err
    assert "tinyassets/app.py" in err
    assert "ADR-004" in err


def test_runtime_commit_is_not_served_until_deployed(history, monkeypatch):
    repo, base = history
    code = repo.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    _serve(monkeypatch, base)

    assert deployed_sha.main(["--assert-contains", code]) == 1


def test_a_commit_off_the_served_line_is_not_served(history, monkeypatch):
    """No descent from the served sha: equivalence is never even considered."""
    repo, base = history
    served = repo.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    repo.git("checkout", "-q", "-b", "side", base)
    side_docs = repo.commit("docs", {"docs/notes.md": "side\n"})
    _serve(monkeypatch, served)

    assert deployed_sha.main(["--assert-contains", side_docs]) == 1


def test_a_classifier_failure_is_not_a_pass(history, monkeypatch):
    repo, base = history
    docs = repo.commit("docs", {"docs/notes.md": "more\n"})
    _serve(monkeypatch, base)

    def broken(*_a, **_k):
        raise deployed_sha.runtime_paths.ClassifyError("simulated")

    monkeypatch.setattr(deployed_sha.runtime_paths, "runtime_changes", broken)
    assert deployed_sha.main(["--assert-contains", docs]) == 1


def test_json_output_says_which_kind_of_pass(history, monkeypatch, capsys):
    repo, base = history
    docs = repo.commit("docs", {"docs/notes.md": "more\n"})
    _serve(monkeypatch, base)

    assert deployed_sha.main(["--json", "--assert-contains", docs]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["contains"] is False
    assert payload["runtime_equivalent"] is True
    assert payload["undeployed_runtime_changes"] == []


def test_report_counts_only_runtime_commits_as_behind(history, monkeypatch):
    repo, base = history
    repo.commit("code", {"tinyassets/app.py": "VERSION = 2\n"})
    repo.commit("root doc", {"AGENTS.md": "agents x\n"})
    repo.commit("docs", {"docs/notes.md": "more\n"})
    repo.git("update-ref", "refs/remotes/origin/main", "HEAD")
    _serve(monkeypatch, base)

    info = deployed_sha.report("https://example.invalid/mcp", 1.0)
    assert info["commits_on_main_not_deployed"] == 3
    assert info["build_affecting_not_deployed"] == 1


def test_a_deploy_workflow_edit_is_not_served_until_deployed(history, monkeypatch, capsys):
    """#3936 shape: deploy-prod.yml changed. That edit mutates the host only on
    the next deploy, so it is not served until then -- never "equivalent"."""
    repo, base = history
    text = (repo.root / ".github/workflows/deploy-prod.yml").read_text(encoding="utf-8")
    head = repo.commit(
        "deploy tweak",
        {".github/workflows/deploy-prod.yml": text + "# x\n", "docs/review.md": "r\n"},
    )
    _serve(monkeypatch, base)

    assert deployed_sha.main(["--assert-contains", head]) == 1
    assert ".github/workflows/deploy-prod.yml" in capsys.readouterr().err


def test_equivalence_requires_descent_even_when_the_trees_match_on_runtime(
    history, monkeypatch
):
    """The ancestor gate, isolated: served and asserted differ only in docs,
    but the asserted commit is on a side line production never served."""
    repo, base = history
    served = repo.commit("docs on main", {"docs/notes.md": "main\n"})
    repo.git("checkout", "-q", "-b", "side", base)
    side = repo.commit("docs on side", {"docs/other.md": "side\n"})
    _serve(monkeypatch, served)

    assert deployed_sha.main(["--assert-contains", side]) == 1


def test_a_helper_the_deploy_imports_is_not_served_until_deployed(
    history, monkeypatch, capsys
):
    """prepare_expected_instance_state.py -> cloud_only_preflight.py shape: the
    helper is imported by a script deploy-prod runs; its output lands on the
    host at the next deploy, not before."""
    repo, base = history
    head = repo.commit(
        "helper", {"scripts/preflight_helper.py": "def resolve_expected():\n    return {}\n"}
    )
    _serve(monkeypatch, base)

    assert deployed_sha.main(["--assert-contains", head]) == 1
    assert "scripts/preflight_helper.py" in capsys.readouterr().err
