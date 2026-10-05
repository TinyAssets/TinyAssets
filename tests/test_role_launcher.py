"""Portable contract checks; kernel behavior is proven in the production oracle."""
import runpy
from pathlib import Path

import pytest

from tinyassets.platform_secrets import CHILD_FORBIDDEN_ENV

LAUNCHER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "deploy/role_launcher.py"))


def test_broker_allowlist_withholds_unknown_and_forbidden_environment(monkeypatch):
    for name in (*CHILD_FORBIDDEN_ENV, "UNENUMERATED_SECRET", "PYTHONPATH", "LD_PRELOAD"):
        monkeypatch.setenv(name, "must-not-cross")
    monkeypatch.setenv("TZ", "UTC")
    result = LAUNCHER["broker_environment"]("/data")
    assert set(result) == {"PATH", "LANG", "HOME", "PYTHONDONTWRITEBYTECODE",
                           "TINYASSETS_DATA_DIR", "TZ"}
    assert not CHILD_FORBIDDEN_ENV.intersection(result)
    assert "must-not-cross" not in result.values()


def test_no_engine_identity_can_run_outside_an_owner_cell():
    with pytest.raises(KeyError):
        LAUNCHER["retire_child"]("engine")


def test_capability_masks_exclude_sys_admin_and_retire_only_migration_authority():
    assert LAUNCHER["ENTRY_CAPS"] == sum(1 << cap for cap in (0, 1, 3, 5, 6, 7, 8))
    assert LAUNCHER["SERVING_CAPS"] == sum(1 << cap for cap in (5, 6, 7, 8))
