"""A red public check after a committed cutover holds; it never image-rolls-back.

Cutover attempt 10 (2026-10-22): the canary rollback reinstalled the previous
bundle under the committed owner-box layout and the new image, and the daemon
crash-looped. The rollback step must detect this run's pre_traffic cutover and
hold before any --restore-bundle call.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.verifies('owner-daemon-in-box:C4')


def _rollback_step():
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy-prod.yml").read_text())
    steps = [step for job in workflow["jobs"].values() for step in job.get("steps", [])]
    return next(s for s in steps if s.get("name") == "Roll back if the public canary is red")


def test_committed_cutover_holds_before_any_image_rollback():
    script = _rollback_step()["run"]
    hold = script.index('"state": *"pre_traffic"')
    restore = script.index("--restore-bundle")
    assert hold < restore
    held = script[hold:restore]
    assert "exit 1" in held and "HOLDING" in held
    assert "cutover_control rollback" in held
    # An unreadable cutover state holds too; it never falls through to the restore.
    assert script.index("could not read this run's cutover state") < restore
