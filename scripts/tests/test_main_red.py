"""main-red's decision: a red main is re-run once, then alarmed, and never reverted."""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main_red  # noqa: E402


@pytest.mark.parametrize("required,attempt,tip,expected", [
    ("failure", 1, True, "rerun"),
    ("timed_out", 1, False, "rerun"),
    ("failure", 2, True, "alarm"),
    ("failure", 2, False, "alarm"),
    ("timed_out", 3, True, "alarm"),
    ("success", 1, True, "close-alarm"),
    ("success", 2, False, "none"),
    # No verdict about the code: cancelled, skipped, or no required job at all.
    ("cancelled", 2, True, "none"),
    ("skipped", 1, True, "none"),
    (None, 2, True, "none"),
])
def test_a_red_main_is_rerun_once_then_alarmed(required, attempt, tip, expected) -> None:
    assert main_red.decide(attempt=attempt, required=required,
                           head_is_main_tip=tip) == expected


def test_nothing_reverts() -> None:
    # The decision reads no parent and no event, so no merge can be named a cause, and
    # the script holds no way to push a branch or open a pull request.
    assert set(inspect.signature(main_red.decide).parameters) == {
        "attempt", "required", "head_is_main_tip"}
    source = inspect.getsource(main_red)
    for word in ('"revert"', '"checkout"', '"pr"', "REVERT_TOKEN", "x-access-token"):
        assert word not in source, word
    body = main_red.render_body(sha="a" * 40, subject="s", run_url="u",
                                failures=["tests/test_x.py::test_y"])
    assert "nothing is reverted automatically" in body and "tests/test_x.py::test_y" in body
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows"
                / "main-red.yml").read_text(encoding="utf-8")
    assert "secrets." not in workflow


@pytest.mark.parametrize(("artifact", "listing"), [
    ("absent", True), ("unreadable", True), ("failing", True),
    ("failing", False), ("unreadable", False), ("failing", "no_workflow"),
])
def test_alarm_links_only_this_attempts_unsuccessful_required_legs(monkeypatch, artifact, listing):
    import json
    import subprocess
    from types import SimpleNamespace

    import yaml

    repo, sha = "owner/repo", "f" * 40
    run = "https://github.com/owner/repo/actions/runs/73"
    workflow = yaml.safe_load((main_red.REPO_ROOT / ".github/workflows/tests.yml").read_text())
    needs = workflow["jobs"]["required-tests"]["needs"]
    # Use a different display name at the reported SHA than at the checkout.
    workflow["jobs"]["structural-guards"]["name"] = "structure (required)"
    if artifact == "failing":
        workflow["jobs"]["affected-tests"]["if"] = "${{ github.event_name== 'pull_request' }}"
    selected = [key for key in needs if key != "affected-tests"]
    # Use GitHub's matrix display names, including the workspace command.
    details = {
        "platform-tests": " (owner 1/2, npm run test:workerd -w owner -- --shard=1/2)",
        "platform-sizes": " (files, npm run test:sizes -w files -- --shard=1/1)",
        "platform-checks": " (typecheck, npm run typecheck)",
    }
    jobs = [{"name": workflow["jobs"][family].get("name", family).replace("${{ matrix.shard }}", "4") + details.get(family, ""),
             "html_url": f"{run}/job/{i}",
             "conclusion": ("cancelled" if family == "process" else "failure")}
            for i, family in enumerate(selected, start=10)]
    jobs += [{"name": "platform-tests (contract, npm run test -w contract)",
              "html_url": f"{run}/job/83", "conclusion": "success"},
             {"name": "affected-tests ${{ matrix.shard }}/4",
              "html_url": f"{run}/job/82", "conclusion": "skipped"},
             {"name": "heavy-tests", "html_url": f"{run}/job/84", "conclusion": "failure"},
             {"name": "required-tests", "html_url": f"{run}/job/85", "conclusion": "failure"}]
    queries = []

    def github(*args):
        if args == ("api", "repos/owner/repo/actions/runs/73"):
            return json.dumps({"run_attempt": 2})
        assert args[:2] == ("api", "--paginate")
        assert args[2].split("?")[0] == "repos/owner/repo/actions/runs/73/attempts/2/jobs"
        query = args[args.index("--jq") + 1]
        queries.append(query)
        if len(queries) == 2 and listing is False:
            raise OSError("private jobs diagnostic")
        # gh --paginate evaluates the query on every page.
        return "".join(subprocess.check_output(["jq", "-cr", query], text=True,
                            input=json.dumps({"jobs": page})) for page in (jobs[:2], jobs[2:]))

    def python_failures(repo_arg, run_id, attempt):
        assert (repo_arg, run_id, attempt) == (repo, 73, 2)
        if artifact == "unreadable":
            raise OSError("private diagnostic")
        return ["tests/test_shape.py::test_roundtrip"] if artifact == "failing" else []

    bodies = []
    monkeypatch.setattr(main_red, "_gh", github)
    def git(*args, **kwargs):
        if args[:2] == ("git", "show"):
            assert args[2] == f"{sha}:.github/workflows/tests.yml"
            if listing == "no_workflow":
                raise OSError("private workflow diagnostic")
            return SimpleNamespace(stdout=yaml.safe_dump(workflow))
        return SimpleNamespace(stdout=sha)

    monkeypatch.setattr(main_red, "_run", git)
    monkeypatch.setattr(main_red, "new_failures", python_failures)
    monkeypatch.setattr(main_red, "_alarm", lambda r, body, s: bodies.append(body))
    assert main_red.handle(repo, 73, sha, "push", 2, run) == "alarm"
    assert len(queries) == (1 if listing == "no_workflow" else 2)
    body, = bodies
    assert f"{run}/attempts/2" in body
    for job in jobs[:-4]:
        link = f"[{job['name']}]({job['html_url']}) ({job['conclusion']})"
        assert (link in body) == (listing is True)
    for job in jobs[-4:]:
        assert job["html_url"] not in body
    assert ("tests/test_shape.py::test_roundtrip" in body) == (artifact == "failing")
    assert ("Python failure details could not be collected" in body) == (artifact == "unreadable")
    assert ("Required job details could not be collected" in body) == (listing is not True)
    assert "private workflow diagnostic" not in body
    assert "private jobs diagnostic" not in body
    assert "private diagnostic" not in body
