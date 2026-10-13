"""Fail-closed P2a measurement and workflow wiring."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scripts import box_preflight, box_preflight_summary

ROOT = Path(__file__).resolve().parents[1]


def test_percentile_and_empty_sample() -> None:
    assert box_preflight.percentile([5, 1, 3, 2, 4], 0.95) == 5
    with pytest.raises(ValueError, match="empty"):
        box_preflight.percentile([], 0.95)


def test_gate_exception_is_a_failure() -> None:
    def broken() -> None:
        raise RuntimeError("no project quota")

    assert box_preflight.gate(broken) == {
        "pass": False,
        "reason": "RuntimeError: no project quota",
    }


def test_box_cgroups_stay_beneath_the_delegated_service() -> None:
    assert (
        box_preflight.service_cgroup_parent("/system.slice/boxhostd.service/control")
        == "/system.slice/boxhostd.service"
    )
    with pytest.raises(RuntimeError, match="boxhostd.service"):
        box_preflight.service_cgroup_parent("/system.slice/other.service")


def test_missing_quota_mount_fails_without_touching_volume(monkeypatch, tmp_path: Path) -> None:
    class Result:
        stdout = "ext4 rw,relatime\n"

    monkeypatch.setattr(box_preflight, "run", lambda _args: Result())
    assert box_preflight.g4(tmp_path) == {
        "filesystem": "ext4",
        "project_quota_mount": False,
        "pass": False,
        "reason": "project quotas are not mounted on data volume",
    }


def test_fatal_run_writes_all_four_failed_gates(monkeypatch, tmp_path: Path) -> None:
    def broken(*_args, **_kwargs) -> None:
        raise RuntimeError("runtime missing")

    path = tmp_path / "results.json"
    monkeypatch.setattr(box_preflight, "run", broken)
    monkeypatch.setattr(sys, "argv", ["box_preflight.py", "--result", str(path)])
    assert box_preflight.main() == 1
    report = json.loads(path.read_text())
    assert report["pass"] is False
    assert set(report["gates"]) == {"G1", "G3", "G4", "G6"}
    assert all(not item["pass"] for item in report["gates"].values())


def test_summary_uses_only_reported_aggregate_values(monkeypatch, capsys, tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    path.write_text(
        json.dumps(
            {
                "pass": False,
                "gates": {
                    "G1": {"pass": True, "memory_max_bytes": 1073741824},
                    "G3": {"pass": False, "awake_pss_with_chromium_mib": 701},
                },
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["box_preflight_summary.py", str(path)])
    box_preflight_summary.main()
    output = capsys.readouterr().out
    assert "Overall: **FAIL**" in output
    assert "awake_pss_with_chromium_mib=701" in output
    assert "| G4 | FAIL |" in output


def test_preflight_workflow_and_idle_service_are_decoupled_from_daemon() -> None:
    deploy = (ROOT / ".github/workflows/deploy-prod.yml").read_text()
    gates = (ROOT / ".github/workflows/box-preflight.yml").read_text()
    unit = (ROOT / "deploy/boxhostd.service").read_text()
    installer = (ROOT / "deploy/install-boxhostd-preflight.sh").read_text()
    assert (
        "sudo flock -w 120 /var/lock/tinyassets-host-mutation.lock "
        "bash /tmp/install-boxhostd-preflight.sh" in deploy
    )
    assert "workflow_dispatch:" in gates
    assert "production-host-mutation" in gates
    assert "--kill-who=main -s USR1 boxhostd.service" in gates
    assert "Delegate=yes" in unit
    assert "boxhostd_idle.py" in unit
    assert "f3ed9131bc252259df150e270154180188f9df56b73a2312940325e1f522a2d6" in installer
    assert "88a87d5d6d06160d4c144f51f95ec6dd6fe0fc6b02697eb877876683c5b57a91" in installer
