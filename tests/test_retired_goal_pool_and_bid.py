"""The goal-pool setting and the node-bid package stay gone.

#4845 removed the goal-pool producer, the node-bid producer, and the bid
actions. This fails if either returns on a surface that would make them
live again.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SETTING = "TINYASSETS_GOAL_POOL"
MODULES = (
    "tinyassets/bid",
    "tinyassets/executors/node_bid.py",
    "tinyassets/producers/goal_pool.py",
    "tinyassets/producers/node_bid.py",
    "bids",
    "goal_pool",
)
SCAN_ROOTS = (
    "deploy",
    "docs/reference",
    "openspec/specs",
    "scripts",
    "tests",
    "tinyassets",
)
SKIP = {Path("tests/test_retired_goal_pool_and_bid.py")}


def test_retired_modules_are_absent():
    present = [rel for rel in MODULES if (REPO / rel).exists()]
    assert present == []


def test_goal_pool_setting_is_not_read_or_set():
    hits: list[str] = []
    for root in SCAN_ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        files = [base] if base.is_file() else [
            path for path in base.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        ]
        for path in files:
            rel = path.relative_to(REPO)
            if rel in SKIP:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if SETTING in text:
                hits.append(rel.as_posix())
    assert hits == []


def test_env_summary_does_not_name_the_goal_pool_flag():
    text = (REPO / "deploy/native/ta_op.c").read_text(encoding="utf-8")
    assert "GOAL_POOL" not in text
