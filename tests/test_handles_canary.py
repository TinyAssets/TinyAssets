"""The old stack's handles canary (handles-canary.yml), offline, until the cutover's hold ends.

Each side's handle set is data (scripts/canary_handles.json): the old side's is the old
canary's own record, held here; the doors' is the doors' tool table, held by
platform/doors/guards/handles.test.ts, and checked live by the platform's canary
(platform/deploy/canary.ts). handles-canary.yml runs the old stack's own check by hand,
read-only, in production.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

import mcp_public_canary as public  # noqa: E402

pytestmark = pytest.mark.verifies(
    "live-mcp-connector-surface:Public Canary And Canonical Review Surface",
)

RECORD = json.loads((_REPO / "scripts" / "canary_handles.json").read_text(encoding="utf-8"))
OLD = set(RECORD["old"]["handles"])
NEW = set(RECORD["new"]["handles"])


def test_the_old_canary_runs_with_only_the_images_two_files(tmp_path):
    # The daemon image copies mcp_public_canary.py and _canary_common.py alone (Dockerfile),
    # and its healthcheck runs the first: it must import and run with nothing beside it.
    for name in ("mcp_public_canary.py", "_canary_common.py"):
        (tmp_path / name).write_bytes((_REPO / "scripts" / name).read_bytes())
    for argv in (["-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                  "import mcp_public_canary as c; print(len(c.CANONICAL_HANDLES))", str(tmp_path)],
                 ["mcp_public_canary.py", "--help"]):
        ran = subprocess.run([sys.executable, "-I", *argv], cwd=tmp_path, capture_output=True,
                             text=True, env={"PATH": os.environ.get("PATH", "")}, timeout=60)
        assert (argv[0], ran.returncode, ran.stderr) == (argv[0], 0, "")
    assert ran.stdout.startswith("usage:")


def test_the_old_side_is_the_old_canarys_own_record():
    # Two sources: the old canary's own literal and the JSON, so dropping a handle from
    # either fails.
    assert public.CANONICAL_HANDLES == frozenset(OLD)
    assert [RECORD["old"]["authorization_server"]] == list(public.EXPECTED_AUTHORIZATION_SERVERS)
    assert RECORD["new"]["authorization_server"] == "https://tinyassets.io"
    assert len(OLD) == len(RECORD["old"]["handles"])
    assert len(NEW) == len(RECORD["new"]["handles"])


def test_the_workflow_runs_the_old_stacks_own_canary_by_hand_and_nothing_else():
    data = yaml.safe_load((_REPO / ".github" / "workflows" / "handles-canary.yml")
                          .read_text(encoding="utf-8"))
    trigger = data.get("on", data.get(True))
    assert list(trigger) == ["workflow_dispatch"]
    assert data["permissions"] == {"contents": "read"}
    (job,) = data["jobs"].values()
    assert job["environment"] == "production"
    uses = [step["uses"] for step in job["steps"] if "uses" in step]
    assert [use.split("@")[0] for use in uses] == ["actions/checkout", "actions/setup-python"]
    runs = [step for step in job["steps"] if "run" in step]
    assert [step["run"] for step in runs] == [
        "python scripts/mcp_public_canary.py --timeout 30 --assert-handles --verbose"]
    assert runs[0]["env"] == {
        "TINYASSETS_WIKI_CANARY_TOKEN": "${{ secrets.TINYASSETS_WIKI_CANARY_TOKEN }}"}
    # Its URL is the old canary's own default, the public one.
    assert public.DEFAULT_URL == "https://tinyassets.io/mcp"
