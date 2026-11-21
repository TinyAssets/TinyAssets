"""pr_times.py's phases, read through a faked `gh` as the script reads GitHub."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "pr_times.py"

# Answers by the path's prefix; a list answer is printed as two pages, as
# `gh api --paginate` prints them.
_FAKE_GH = '''\
import json, os, sys
path = next(a for a in sys.argv[1:] if a.startswith("repos/"))
with open(os.environ["FAKE_GH"], encoding="utf-8") as f:
    answers = json.load(f)
answer = next(v for k, v in answers.items() if path.startswith(k))
if isinstance(answer, list):
    print(json.dumps(answer[:1]))
    print(json.dumps(answer[1:]))
else:
    print(json.dumps(answer))
'''

_R = "repos/o/r"
_ANSWERS = {
    f"{_R}/pulls?": [
        {"number": 7, "merged_at": "2026-11-20T10:00:00Z"},
        {"number": 8, "merged_at": None},
        {"number": 9, "merged_at": "2026-11-21T10:00:00Z"},
    ],
    f"{_R}/pulls/7": {"created_at": "2026-11-20T08:00:00Z",
                      "merged_at": "2026-11-20T10:00:00Z", "head": {"sha": "a7"}},
    f"{_R}/pulls/9": {"created_at": "2026-11-21T08:00:00Z",
                      "merged_at": "2026-11-21T10:00:00Z", "head": {"sha": "a9"}},
    # PR 7: drafted twice, queued, kicked out, queued again.
    f"{_R}/issues/7/": [
        {"event": "ready_for_review", "created_at": "2026-11-20T08:10:00Z"},
        {"event": "convert_to_draft", "created_at": "2026-11-20T08:20:00Z"},
        {"event": "ready_for_review", "created_at": "2026-11-20T08:30:00Z"},
        {"event": "added_to_merge_queue", "created_at": "2026-11-20T09:00:00Z"},
        {"event": "removed_from_merge_queue", "created_at": "2026-11-20T09:20:00Z"},
        {"event": "added_to_merge_queue", "created_at": "2026-11-20T09:40:00Z"},
    ],
    # PR 9: opened ready, queued once.
    f"{_R}/issues/9/": [
        {"event": "added_to_merge_queue", "created_at": "2026-11-21T09:30:00Z"},
    ],
    f"{_R}/actions/runs?head_sha=a7": {"workflow_runs": [
        {"name": "Tests", "status": "completed", "run_started_at": "2026-11-20T08:30:00Z",
         "updated_at": "2026-11-20T08:55:00Z"},
        {"name": "Tests", "status": "completed", "run_started_at": "2026-11-20T08:00:00Z",
         "updated_at": "2026-11-20T08:05:00Z"},
        {"name": "Lint", "status": "completed", "run_started_at": "2026-11-20T08:00:00Z",
         "updated_at": "2026-11-20T09:00:00Z"},
    ]},
    f"{_R}/actions/runs?head_sha=a9": {"workflow_runs": []},
}


def _run(tmp_path: Path, *args: str) -> list[list[str]]:
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "gh").write_text(f"#!{sys.executable}\n{_FAKE_GH}", encoding="utf-8")
    (tools / "gh").chmod(0o755)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(_ANSWERS), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo", "o/r", *args], capture_output=True,
        text=True, check=True, timeout=60,
        env={**os.environ, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
             "FAKE_GH": str(answers)})
    return [line.split("\t") for line in done.stdout.splitlines()]


def test_the_last_merged_prs_are_timed_phase_by_phase(tmp_path: Path) -> None:
    header, newest, older, summary = _run(tmp_path, "--last", "2")
    assert header == ["pr", "open>ready", "ready>queued", "queued>merged", "open>merged",
                      "queue_entries", "tests_min"]
    # Ready is the last ready_for_review; queued>merged runs from the last queue entry;
    # the Tests minutes are the latest Tests run's.
    assert older == ["7", "30", "30", "20", "120", "2", "25"]
    assert newest == ["9", "0", "90", "30", "120", "1", "-"]
    assert summary == ["open>ready median 15 max 30; ready>queued median 60 max 90; "
                       "queued>merged median 25 max 30; open>merged median 120 max 120; "
                       "queue_entries median 1.5 max 2; tests_min median 25 max 25"]
