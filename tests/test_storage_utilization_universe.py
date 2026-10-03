"""BUG-032 — storage_utilization byte counters for activity_log and
universe_outputs were silently reporting zero.

Root cause: _SUBSYSTEM_PATHS mapped them relative to data_dir() root,
but on prod they live inside the universe directory (udir/activity.log,
udir/output/). Fix: get_status patches these two subsystem entries using
the already-resolved udir.

All tests use a tmp universe directory to avoid touching real data.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

# ── path_size_bytes unit tests ────────────────────────────────────────────────


def test_path_size_bytes_missing_returns_zero(tmp_path):
    from tinyassets.storage import path_size_bytes

    assert path_size_bytes(tmp_path / "no_such_file") == 0


def test_path_size_bytes_file(tmp_path):
    from tinyassets.storage import path_size_bytes

    p = tmp_path / "data.bin"
    p.write_bytes(b"a" * 512)
    assert path_size_bytes(p) == 512


def test_path_size_bytes_directory_recursive(tmp_path):
    from tinyassets.storage import path_size_bytes

    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"y" * 200)
    assert path_size_bytes(tmp_path) == 300


# ── get_status patches per-universe subsystems ────────────────────────────────


def _make_universe(
    tmp_path: Path,
    *,
    log_bytes: int,
    output_bytes: int,
    checkpoint_bytes: int = 0,
) -> tuple[Path, str]:
    """Create a minimal universe directory with activity.log and output/."""
    uid = "test-universe"
    udir = tmp_path / uid
    udir.mkdir(parents=True)
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(tmp_path, uid)
    (udir / "activity.log").write_bytes(b"L" * log_bytes)
    out = udir / "output"
    out.mkdir()
    (out / "chapter1.txt").write_bytes(b"O" * output_bytes)
    if checkpoint_bytes:
        (udir / "checkpoints.db").write_bytes(b"C" * checkpoint_bytes)
    return udir, uid


def test_get_status_activity_log_bytes_nonzero(tmp_path, monkeypatch):
    """get_status must report nonzero bytes for activity_log when the file exists."""
    from tinyassets.universe_server import get_status
    udir, uid = _make_universe(tmp_path, log_bytes=400, output_bytes=100)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))

    _cfg = _minimal_cfg()
    with (
        patch("tinyassets.dispatcher.load_dispatcher_config", return_value=_cfg),
        patch("tinyassets.dispatcher.paid_market_enabled", return_value=False),
        patch("tinyassets.api.helpers._default_universe", return_value=uid),
        patch("tinyassets.api.helpers._universe_dir", return_value=udir),
    ):
        raw = get_status(command_center_id=uid)

    result = json.loads(raw)
    su = result.get("storage_utilization", {})
    assert "per_subsystem" in su, "storage_utilization.per_subsystem missing"
    log_entry = su["per_subsystem"].get("activity_log", {})
    assert log_entry.get("bytes", 0) == 400, (
        f"Expected activity_log.bytes=400, got {log_entry.get('bytes')}"
    )


def test_get_status_universe_outputs_bytes_nonzero(tmp_path, monkeypatch):
    """get_status must report nonzero bytes for universe_outputs when files exist."""
    from tinyassets.universe_server import get_status
    udir, uid = _make_universe(tmp_path, log_bytes=10, output_bytes=800)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))

    _cfg = _minimal_cfg()
    with (
        patch("tinyassets.dispatcher.load_dispatcher_config", return_value=_cfg),
        patch("tinyassets.dispatcher.paid_market_enabled", return_value=False),
        patch("tinyassets.api.helpers._default_universe", return_value=uid),
        patch("tinyassets.api.helpers._universe_dir", return_value=udir),
    ):
        raw = get_status(command_center_id=uid)

    result = json.loads(raw)
    su = result.get("storage_utilization", {})
    out_entry = su["per_subsystem"].get("universe_outputs", {})
    assert out_entry.get("bytes", 0) == 800, (
        f"Expected universe_outputs.bytes=800, got {out_entry.get('bytes')}"
    )


def test_get_status_checkpoint_db_bytes_nonzero_and_cap_current(
    tmp_path,
    monkeypatch,
):
    """get_status must report the active universe checkpoint DB, not root."""
    from tinyassets.universe_server import get_status

    udir, uid = _make_universe(
        tmp_path,
        log_bytes=10,
        output_bytes=20,
        checkpoint_bytes=4096,
    )
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_CAP_CHECKPOINTS_BYTES", "10000")

    _cfg = _minimal_cfg()
    with (
        patch("tinyassets.dispatcher.load_dispatcher_config", return_value=_cfg),
        patch("tinyassets.dispatcher.paid_market_enabled", return_value=False),
        patch("tinyassets.api.helpers._default_universe", return_value=uid),
        patch("tinyassets.api.helpers._universe_dir", return_value=udir),
    ):
        raw = get_status(command_center_id=uid)

    result = json.loads(raw)
    su = result.get("storage_utilization", {})
    checkpoint_entry = su["per_subsystem"].get("checkpoint_db", {})
    assert checkpoint_entry.get("bytes", 0) == 4096
    assert uid in checkpoint_entry.get("path", "")
    assert su["subsystem_caps"]["checkpoints"]["current_bytes"] == 4096


def test_get_status_activity_log_path_points_into_udir(tmp_path, monkeypatch):
    """The reported path for activity_log must be inside udir, not data_dir root."""
    from tinyassets.universe_server import get_status
    udir, uid = _make_universe(tmp_path, log_bytes=50, output_bytes=50)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))

    _cfg = _minimal_cfg()
    with (
        patch("tinyassets.dispatcher.load_dispatcher_config", return_value=_cfg),
        patch("tinyassets.dispatcher.paid_market_enabled", return_value=False),
        patch("tinyassets.api.helpers._default_universe", return_value=uid),
        patch("tinyassets.api.helpers._universe_dir", return_value=udir),
    ):
        raw = get_status(command_center_id=uid)

    result = json.loads(raw)
    reported_path = result["storage_utilization"]["per_subsystem"]["activity_log"]["path"]
    assert uid in reported_path, (
        f"activity_log path should include universe dir '{uid}', got: {reported_path!r}"
    )


def test_get_status_missing_log_and_output_still_reports_zero_not_error(tmp_path, monkeypatch):
    """Missing activity.log + output/ must yield bytes=0, not crash."""
    from tinyassets.universe_server import get_status

    # Universe dir exists but has no activity.log or output/.
    uid = "empty-universe"
    udir = tmp_path / uid
    udir.mkdir()
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(tmp_path, uid)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))

    _cfg = _minimal_cfg()
    with (
        patch("tinyassets.dispatcher.load_dispatcher_config", return_value=_cfg),
        patch("tinyassets.dispatcher.paid_market_enabled", return_value=False),
        patch("tinyassets.api.helpers._default_universe", return_value=uid),
        patch("tinyassets.api.helpers._universe_dir", return_value=udir),
    ):
        raw = get_status(command_center_id=uid)

    result = json.loads(raw)
    assert "error" not in result, f"get_status returned error: {result.get('error')}"
    su = result.get("storage_utilization", {})
    assert su.get("per_subsystem", {}).get("activity_log", {}).get("bytes") == 0
    assert su.get("per_subsystem", {}).get("universe_outputs", {}).get("bytes") == 0


# ── helpers ───────────────────────────────────────────────────────────────────


def _minimal_cfg():
    """Return a minimal DispatcherConfig-like object that get_status needs."""
    from unittest.mock import MagicMock

    cfg = MagicMock()
    cfg.served_llm_type = ""
    cfg.accept_external_requests = False
    cfg.accept_goal_pool = False
    cfg.accept_paid_bids = False
    cfg.allow_opportunistic = True
    cfg.tier_status_map.return_value = {}
    return cfg
