"""Tests for scripts/queue_freshness.py and its workflow."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "queue_freshness", _REPO / "scripts" / "queue_freshness.py"
)
assert _spec and _spec.loader
qf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qf)

_WF = _REPO / ".github" / "workflows" / "queue-freshness.yml"


def _workflow() -> dict:
    return yaml.safe_load(_WF.read_text("utf-8"))


def test_only_tests_both_sides_can_affect_are_run():
    run, why = qf.probe_set(["tests/a.py", "tests/b.py"], ["tests/b.py", "tests/c.py"])
    assert run == ["tests/b.py"]
    assert "1 shared" in why


def test_disjoint_selections_cannot_conflict_in_a_test():
    run, why = qf.probe_set(["tests/a.py"], ["tests/c.py"])
    assert run == []
    assert "no test" in why


def test_one_side_selecting_everything_runs_the_other_sides_tests():
    assert qf.probe_set(None, ["tests/c.py"])[0] == ["tests/c.py"]
    assert qf.probe_set(["tests/a.py"], None)[0] == ["tests/a.py"]


def test_a_probe_bigger_than_the_cap_is_left_to_the_queue():
    many = [f"tests/t{i}.py" for i in range(qf.MAX_SHARED_TESTS + 1)]
    run, why = qf.probe_set(many, many)
    assert run is None and "cap" in why
    assert qf.probe_set(None, None)[0] is None


def test_only_a_failure_that_repeats_and_passes_on_main_is_blamed_on_the_pr():
    first = ["t::real", "t::flake", "t::main_broken"]
    assert qf.blame(first, {"t::real", "t::main_broken"}, {"t::main_broken"}) == ["t::real"]


def test_confirmation_reads_real_pytest_ids(tmp_path):
    """Codex on #4293: the confirmation run's default junit family dropped
    `file`, so every id read as "did not run" and blame() removed them all."""
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_x.py").write_text(
        "def test_ok():\n    pass\n\n\ndef test_bad():\n    assert False\n", encoding="utf-8"
    )
    ids = ["tests/test_x.py::test_ok", "tests/test_x.py::test_bad"]
    still = qf._still_failing(tree, ids, str(tmp_path), "t")
    assert still == {"tests/test_x.py::test_bad"}
    assert qf.blame(ids, still, set()) == ["tests/test_x.py::test_bad"]


def test_act_uses_the_trusted_number_and_head_never_the_artifacts():
    trusted = {"number": 5, "head": "h5"}
    forged = {"number": 9, "head": "h5", "verdict": "semantic-conflict", "why": "x",
              "failures": ["t::y"]}
    assert qf.actionable(trusted, forged)["number"] == 5
    assert qf.actionable(trusted, {**forged, "head": "other"}) is None
    assert qf.actionable(trusted, {**forged, "verdict": "fresh"}) is None
    assert qf.actionable(trusted, None) is None


def test_the_comment_never_mentions_anyone_and_is_marked_per_head_and_main():
    """Lead 2026-10-02: every PR is the founder's account; a mention pages him."""
    body = qf.render_comment(
        {"head": "a" * 40, "verdict": "semantic-conflict", "why": "2 shared test file(s)",
         "failures": ["tests/x.py::test_y"]},
        "b" * 40,
    )
    assert body.startswith(qf.MARKER.format(head="a" * 40, main="b" * 40))
    assert "@" not in body and "`tests/x.py::test_y`" in body
    assert "Drain-Review-Diff" in body, "tell the builder their diff-key stamp survives a rebase"


def test_pr_code_runs_without_any_github_token(monkeypatch):
    for name in qf._SECRET_ENV:
        monkeypatch.setenv(name, "t0ken")
    code = "import os; print([os.environ.get(n) for n in %r])" % (qf._SECRET_ENV,)
    out = qf._run(sys.executable, "-c", code, untrusted=True)
    assert "t0ken" not in out.stdout


def test_selection_imports_conftests_without_tokens(monkeypatch, tmp_path):
    """Codex on #4293: the selector's conftest subprocess inherited GH_TOKEN."""
    monkeypatch.setenv("GH_TOKEN", "t0ken")
    seen = {}

    class _Fake:
        @staticmethod
        def select(files, root):
            seen["token"] = __import__("os").environ.get("GH_TOKEN")
            return ["tests/a.py"], []

    monkeypatch.setitem(sys.modules, "affected_tests", _Fake)
    assert qf._selection(["x.py"], tmp_path) == ["tests/a.py"]
    assert seen["token"] is None
    assert __import__("os").environ["GH_TOKEN"] == "t0ken", "restored for the trusted code"


def test_candidates_paginate_filter_base_and_order_by_queue(monkeypatch):
    def pr(n, *, armed=False, draft=False, base="main"):
        return {"id": f"id{n}", "number": n, "isDraft": draft, "headRefOid": f"h{n}",
                "baseRefOid": "b", "baseRefName": base, "author": {"login": "u"},
                "autoMergeRequest": {"enabledAt": "t"} if armed else None}

    queue = {"entries": {"nodes": [{"pullRequest": {"number": 7}},
                                   {"pullRequest": {"number": 3}}]}}
    pages = [
        {"mergeQueue": queue, "pullRequests": {
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            "nodes": [pr(1, armed=True), pr(5), pr(9, armed=True, draft=True)]}},
        {"mergeQueue": queue, "pullRequests": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [pr(3), pr(7), pr(11, armed=True, base="release")]}},
    ]
    calls = []

    def fake_graphql(query, **variables):
        calls.append(variables.get("after"))
        return {"repository": pages[len(calls) - 1]}

    monkeypatch.setattr(qf, "_graphql", fake_graphql)
    assert [p["number"] for p in qf.candidates("o/r")] == [7, 3, 1]
    assert calls == [None, "c1"]


def test_act_re_derives_a_conflict_against_current_main_without_pr_code(tmp_path):
    """A textual-conflict verdict is checked by a trusted merge-tree, not believed."""
    repo = tmp_path / "r"
    repo.mkdir()

    def git(*a):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a],
                              cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

    git("init", "-q", "-b", "main")
    (repo / "f").write_text("base\n", encoding="utf-8")
    git("add", "f")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "pr")
    (repo / "f").write_text("pr\n", encoding="utf-8")
    git("commit", "-qam", "pr")
    head = git("rev-parse", "HEAD")
    git("checkout", "-q", "main")
    (repo / "f").write_text("main\n", encoding="utf-8")
    git("commit", "-qam", "main")
    main = git("rev-parse", "HEAD")
    out = subprocess.run(["git", "merge-tree", "--write-tree", main, head], cwd=repo,
                         capture_output=True, text=True)
    assert out.returncode == 1, "the act job's conflict test relies on this exit code"


def test_the_workflow_gives_pr_code_no_token_and_names_artifacts_from_the_matrix():
    wf = _workflow()
    triggers = wf[True] if True in wf else wf["on"]
    assert triggers["push"]["branches"] == ["main"]
    assert wf["permissions"] == {}
    assert wf["concurrency"]["cancel-in-progress"] is False
    jobs = wf["jobs"]
    probe, act = jobs["probe"], jobs["act"]
    assert probe["permissions"] == {"contents": "read"}
    probe_env = {k for s in probe["steps"] for k in (s.get("env") or {})}
    assert not probe_env & {"GH_TOKEN", "GITHUB_TOKEN"}
    upload = next(s for s in probe["steps"] if "upload-artifact" in str(s.get("uses", "")))
    assert upload["with"]["name"] == "verdict-${{ matrix.pr.number }}"
    assert act["needs"] == ["list", "probe"]
    for job in jobs.values():
        checkout = next(s for s in job["steps"] if "actions/checkout" in str(s.get("uses", "")))
        assert checkout["with"]["persist-credentials"] is False
        assert "pull_request" not in str(checkout["with"]["ref"])
    act_runs = "\n".join(s.get("run", "") for s in act["steps"])
    assert "pip install" not in act_runs and "pytest" not in act_runs, "act must not run PR code"
    assert "queue_freshness.py act" in act_runs


def test_list_output_is_capped_json(monkeypatch, capsys):
    many = [{"number": i, "head": f"h{i}", "base": "b", "author": ""} for i in range(20)]
    monkeypatch.setattr(qf, "candidates", lambda repo: many)
    monkeypatch.setattr(qf, "has_receipt", lambda *a: True)
    monkeypatch.setattr(sys, "argv", ["qf", "list", "--repo", "o/r"])
    assert qf.main() == 0
    assert len(json.loads(capsys.readouterr().out)) == qf.MAX_PROBES


def _pr(head="h", queued=True, armed=True, state="OPEN"):
    return {"id": "PR_id", "state": state, "isDraft": False, "headRefOid": head,
            "baseRefName": "main", "isInMergeQueue": queued, "author": {"login": "dev1"},
            "autoMergeRequest": {"enabledAt": "t"} if armed else None}


def _fake_act(monkeypatch, tmp_path, verdicts, lives, main="m1"):
    """Drive act() against scripted API reads; returns the mutations it made."""
    calls = []
    reads = iter(lives)
    monkeypatch.setattr(qf, "_main_sha", lambda repo: main)
    monkeypatch.setattr(qf, "_live", lambda repo, n: next(reads))
    monkeypatch.setattr(qf, "_graphql", lambda q, **v: calls.append(q.split("(")[1]) or {})
    monkeypatch.setattr(qf, "_gh", lambda *a: calls.append(a[0] + " " + a[1]) or "")
    for n, body in verdicts.items():
        d = tmp_path / f"verdict-{n}"
        d.mkdir()
        (d / "verdict.json").write_text(body, encoding="utf-8")
    return calls


def test_a_malformed_artifact_skips_only_its_own_pr(monkeypatch, tmp_path):
    """Codex on #4293 round 2: one bad artifact aborted every later action."""
    good = json.dumps({"head": "h", "verdict": "semantic-conflict", "why": "w",
                       "failures": ["t::x"]})
    calls = _fake_act(monkeypatch, tmp_path, {1: "{not json", 2: good},
                      [_pr(), _pr(), _pr(queued=False), _pr(queued=False, armed=False)])
    matrix = [{"number": 1, "head": "h"}, {"number": 2, "head": "h"}]
    assert qf.act("o/r", "m1", matrix, tmp_path) == 0
    assert "$id:ID!){dequeuePullRequest" in calls
    assert "$id:ID!){disablePullRequestAutoMerge" in calls


def test_a_head_that_moves_between_dequeue_and_disarm_is_not_disarmed(monkeypatch, tmp_path):
    good = json.dumps({"head": "h", "verdict": "semantic-conflict", "why": "w",
                       "failures": ["t::x"]})
    calls = _fake_act(monkeypatch, tmp_path, {2: good},
                      [_pr(), _pr(), _pr(head="new", queued=False)])
    qf.act("o/r", "m1", [{"number": 2, "head": "h"}], tmp_path)
    assert "$id:ID!){dequeuePullRequest" in calls
    assert "$id:ID!){disablePullRequestAutoMerge" not in calls
    assert not any(c.startswith("pr comment") for c in calls)


def test_a_semantic_verdict_is_dropped_once_main_moved(monkeypatch, tmp_path):
    good = json.dumps({"head": "h", "verdict": "semantic-conflict", "why": "w",
                       "failures": ["t::x"]})
    calls = _fake_act(monkeypatch, tmp_path, {2: good}, [], main="m2")
    qf.act("o/r", "m1", [{"number": 2, "head": "h"}], tmp_path)
    assert calls == []


def test_receipt_check_fails_closed(monkeypatch):
    def boom(*a):
        raise subprocess.CalledProcessError(1, "gh", stderr="nope")

    monkeypatch.setattr(qf, "_gh", boom)
    assert qf.has_receipt("o/r", 1, "h", "b") is False


def test_a_verdict_missing_fields_is_never_acted_on():
    """Codex on #4293 r3: a verdict without `why` dequeued, then crashed."""
    t = {"number": 5, "head": "h"}
    assert qf.actionable(t, {"head": "h", "verdict": "conflict"}) is None
    assert qf.actionable(t, {"head": "h", "verdict": "conflict", "why": "w", "failures": 1}) is None
    assert qf.actionable(t, {"head": "h", "verdict": "semantic-conflict", "why": "w"}) is None
    ok = qf.actionable(t, {"head": "h", "verdict": "conflict", "why": "w", "x": "junk"})
    assert ok == {"number": 5, "head": "h", "verdict": "conflict", "why": "w", "failures": []}


def test_main_moving_between_dequeue_and_disarm_stops_the_removal(monkeypatch, tmp_path):
    good = json.dumps({"head": "h", "verdict": "semantic-conflict", "why": "w",
                       "failures": ["t::x"]})
    calls = _fake_act(monkeypatch, tmp_path, {2: good}, [_pr(), _pr(), _pr(queued=False)])
    mains = iter(["m1", "m1", "m2"])
    monkeypatch.setattr(qf, "_main_sha", lambda repo: next(mains))
    qf.act("o/r", "m1", [{"number": 2, "head": "h"}], tmp_path)
    assert "$id:ID!){dequeuePullRequest" in calls
    assert "$id:ID!){disablePullRequestAutoMerge" not in calls
    assert not any(c.startswith("pr comment") for c in calls)


def test_a_failed_gate_with_nothing_to_blame_is_unchecked_not_fresh():
    """Codex on #4293 round 2: a stale quarantine entry failed the gate, and
    the probe still said fresh."""
    assert qf.outcome([], False, "w")["verdict"] == "unchecked"
    assert qf.outcome([], True, "w")["verdict"] == "fresh"
    assert qf.outcome(["t::x"], False, "w")["verdict"] == "semantic-conflict"
