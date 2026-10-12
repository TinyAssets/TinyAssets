"""Storage utilization observability (BUG-023 Phase 1 + BUG-032) tests.

Guards the `inspect_storage_utilization` surface + its wiring into
`get_status`. Spec source: `openspec/specs/storage-runtime-accounting/spec.md` §storage_inspect.

Covered:
- Shape contract (keys present, types correct).
- Missing subsystem path → bytes=0 (not an error).
- pressure_level thresholds: ok < 80% ≤ warn < 95% ≤ critical.
- growth_estimate is null in Phase 1 (no timeseries yet).
- Synthetic-fs: monkeypatched `shutil.disk_usage` exercises all three
  pressure levels deterministically without touching the real disk.
- get_status response embeds `storage_utilization`.
- BUG-032: get_status patches activity_log + universe_outputs bytes from
  the universe directory (not data_dir root) when files are populated.
"""
from __future__ import annotations

import json
from collections import namedtuple
from pathlib import Path

import pytest

from tinyassets import storage
from tinyassets.storage import inspect_storage_utilization

_FakeUsage = namedtuple("_FakeUsage", ["total", "used", "free"])


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point TINYASSETS_DATA_DIR at a tmp directory for deterministic walks."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    return tmp_path


class TestShapeContract:
    def test_returns_expected_keys(self, isolated_data_dir):
        result = inspect_storage_utilization()

        for key in (
            "volume_percent",
            "volume_bytes_total",
            "volume_bytes_free",
            "per_subsystem",
            "growth_estimate",
            "pressure_level",
        ):
            assert key in result, f"missing key: {key}"

    def test_per_subsystem_includes_all_spec_names(self, isolated_data_dir):
        result = inspect_storage_utilization()

        expected = {
            "run_transcripts",
            "knowledge_db",
            "story_db",
            "lance_indexes",
            "checkpoint_db",
            "wiki",
            "activity_log",
            "universe_outputs",
        }
        assert set(result["per_subsystem"].keys()) == expected

    def test_each_subsystem_has_bytes_and_path(self, isolated_data_dir):
        result = inspect_storage_utilization()

        for name, info in result["per_subsystem"].items():
            assert "bytes" in info, f"{name} missing bytes"
            assert "path" in info, f"{name} missing path"
            assert isinstance(info["bytes"], int)
            assert isinstance(info["path"], str)


class TestMissingPathsReturnZero:
    def test_empty_data_dir_all_subsystems_zero(self, isolated_data_dir):
        """No subsystem paths exist yet → every entry is 0 bytes, no raise."""
        result = inspect_storage_utilization()

        for name, info in result["per_subsystem"].items():
            assert info["bytes"] == 0, f"{name} should be 0 on empty dir"

    def test_partial_population(self, isolated_data_dir: Path):
        """Only some subsystems have data; others report 0."""
        (isolated_data_dir / "knowledge.db").write_bytes(b"x" * 1024)
        (isolated_data_dir / "wiki").mkdir()
        (isolated_data_dir / "wiki" / "page.md").write_bytes(b"y" * 512)

        result = inspect_storage_utilization()

        assert result["per_subsystem"]["knowledge_db"]["bytes"] == 1024
        assert result["per_subsystem"]["wiki"]["bytes"] == 512
        # Still missing → 0, not an error.
        assert result["per_subsystem"]["story_db"]["bytes"] == 0
        assert result["per_subsystem"]["checkpoint_db"]["bytes"] == 0

    def test_run_transcripts_measures_sqlite_stores_not_a_runs_dir(
        self, isolated_data_dir: Path
    ):
        """run_transcripts reflects the real run stores (root + per-universe
        .langgraph_runs.db + .runs.db), NOT a `runs/` dir that is never created on
        the live system. Regression for the 2026-06-25 '/data/runs=0 → transcripts
        not persisting' false alarm (run data was persisting fine in SQLite)."""
        (isolated_data_dir / ".langgraph_runs.db").write_bytes(b"x" * 1000)
        (isolated_data_dir / ".langgraph_runs.db-wal").write_bytes(b"w" * 50)
        (isolated_data_dir / ".langgraph_runs.db-shm").write_bytes(b"s" * 25)
        (isolated_data_dir / ".runs.db").write_bytes(b"y" * 500)
        uni = isolated_data_dir / "concordance"
        uni.mkdir()
        from tests.conftest import own_universe
        # A universe needs an OWNER to be readable at all (2026-09-02).
        own_universe(isolated_data_dir, "concordance")
        (uni / ".langgraph_runs.db").write_bytes(b"z" * 300)
        (uni / ".runs.db").write_bytes(b"w" * 200)
        (uni / ".runs.db-wal").write_bytes(b"v" * 40)
        # a stray runs/ dir must NOT be what gets measured
        (isolated_data_dir / "runs").mkdir()
        (isolated_data_dir / "runs" / "junk").write_bytes(b"q" * 9999)

        result = inspect_storage_utilization()

        # 1000 + 50 + 25 (wal/shm) + 500 + 300 + 200 + 40 = 2115; runs/ dir ignored.
        assert result["per_subsystem"]["run_transcripts"]["bytes"] == 2115


class TestDirectoryRecursion:
    def test_wiki_dir_sums_nested_file_sizes(self, isolated_data_dir: Path):
        wiki = isolated_data_dir / "wiki"
        (wiki / "subdir").mkdir(parents=True)
        (wiki / "top.md").write_bytes(b"a" * 100)
        (wiki / "subdir" / "nested.md").write_bytes(b"b" * 200)
        (wiki / "subdir" / "deeper.md").write_bytes(b"c" * 300)

        result = inspect_storage_utilization()

        assert result["per_subsystem"]["wiki"]["bytes"] == 600


class TestPressureLevelThresholds:
    @pytest.mark.parametrize(
        "percent,expected",
        [
            (0.0, "ok"),
            (0.50, "ok"),
            (0.79, "ok"),
            (0.80, "warn"),
            (0.85, "warn"),
            (0.94, "warn"),
            (0.95, "critical"),
            (0.99, "critical"),
            (1.0, "critical"),
        ],
    )
    def test_thresholds(
        self, isolated_data_dir, monkeypatch, percent: float, expected: str,
    ):
        total = 100_000_000
        free = int(total * (1 - percent))

        def fake_disk_usage(_path):
            return _FakeUsage(total=total, used=total - free, free=free)

        monkeypatch.setattr("shutil.disk_usage", fake_disk_usage)

        result = inspect_storage_utilization()

        assert result["pressure_level"] == expected
        # volume_percent rounded to 4 decimal places in the contract.
        assert abs(result["volume_percent"] - percent) < 0.01

    def test_zero_total_does_not_divide_by_zero(
        self, isolated_data_dir, monkeypatch,
    ):
        def fake_disk_usage(_path):
            return _FakeUsage(total=0, used=0, free=0)

        monkeypatch.setattr("shutil.disk_usage", fake_disk_usage)

        result = inspect_storage_utilization()

        assert result["volume_percent"] == 0.0
        assert result["pressure_level"] == "ok"

    def test_disk_usage_oserror_tolerated(
        self, isolated_data_dir, monkeypatch,
    ):
        def boom(_path):
            raise OSError("simulated volume unavailable")

        monkeypatch.setattr("shutil.disk_usage", boom)

        # Must not raise — observability never breaks the probe.
        result = inspect_storage_utilization()

        assert result["volume_bytes_total"] == 0
        assert result["pressure_level"] == "ok"


class TestGrowthEstimate:
    def test_growth_estimate_null_without_timeseries(self, isolated_data_dir):
        """Phase 1: no historical timeseries → growth_estimate = null."""
        result = inspect_storage_utilization()

        assert result["growth_estimate"] is None


class TestGetStatusIntegration:
    def test_get_status_includes_storage_utilization(
        self, isolated_data_dir, monkeypatch,
    ):
        # Stable disk percent so the pressure_level is deterministic.
        def fake_disk_usage(_path):
            return _FakeUsage(
                total=100_000_000, used=10_000_000, free=90_000_000,
            )
        monkeypatch.setattr("shutil.disk_usage", fake_disk_usage)

        # Create the default universe so get_status has a real target.
        (isolated_data_dir / "default-universe").mkdir()
        from tests.conftest import own_universe
        # A universe needs an OWNER to be readable at all (2026-09-02).
        own_universe(isolated_data_dir, "default-universe")

        from tinyassets.universe_server import get_status
        raw = get_status("default-universe")
        payload = json.loads(raw)

        assert "storage_utilization" in payload
        su = payload["storage_utilization"]
        assert "volume_percent" in su
        assert "per_subsystem" in su
        assert su["pressure_level"] in {"ok", "warn", "critical"}

    def test_get_status_patches_activity_log_and_outputs_from_universe_dir(
        self, isolated_data_dir, monkeypatch,
    ):
        """BUG-032: activity_log + universe_outputs bytes must come from the
        universe dir (udir/activity.log, udir/output/), not data_dir root.
        inspect_storage_utilization() looks at the root; get_status patches
        them from the correct universe-scoped paths.
        """
        def fake_disk_usage(_path):
            return _FakeUsage(total=100_000_000, used=10_000_000, free=90_000_000)
        monkeypatch.setattr("shutil.disk_usage", fake_disk_usage)

        # Create a universe with a populated activity.log and output dir.
        udir = isolated_data_dir / "default-universe"
        udir.mkdir()
        from tests.conftest import own_universe
        # A universe needs an OWNER to be readable at all (2026-09-02).
        own_universe(isolated_data_dir, "default-universe")
        activity_log = udir / "activity.log"
        activity_log.write_bytes(b"log entry\n" * 100)  # 1000 bytes
        out_dir = udir / "output"
        out_dir.mkdir()
        (out_dir / "chapter1.txt").write_bytes(b"content " * 64)  # 512 bytes

        expected_log_bytes = activity_log.stat().st_size
        expected_output_bytes = (out_dir / "chapter1.txt").stat().st_size

        from tinyassets.universe_server import get_status
        raw = get_status("default-universe")
        payload = json.loads(raw)

        su = payload["storage_utilization"]
        assert "per_subsystem" in su

        log_entry = su["per_subsystem"].get("activity_log", {})
        assert log_entry.get("bytes", 0) == expected_log_bytes, (
            f"activity_log bytes should be {expected_log_bytes} from udir, "
            f"got {log_entry.get('bytes', 0)}"
        )

        output_entry = su["per_subsystem"].get("universe_outputs", {})
        assert output_entry.get("bytes", 0) >= expected_output_bytes, (
            f"universe_outputs bytes should be >= {expected_output_bytes} from udir, "
            f"got {output_entry.get('bytes', 0)}"
        )


class TestPathSizeHelper:
    def test_missing_path_zero(self, tmp_path: Path):
        assert storage.path_size_bytes(tmp_path / "nope") == 0

    def test_file_size(self, tmp_path: Path):
        p = tmp_path / "f.bin"
        p.write_bytes(b"x" * 2048)
        assert storage.path_size_bytes(p) == 2048

    def test_directory_walk(self, tmp_path: Path):
        (tmp_path / "a.bin").write_bytes(b"1" * 100)
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.bin").write_bytes(b"2" * 50)
        assert storage.path_size_bytes(tmp_path) == 150

    def test_neither_file_nor_dir_returns_zero(self, tmp_path: Path):
        # A symlink to a non-existent target satisfies .exists()==False.
        # Also covers edge cases like sockets / FIFOs.
        # Using a path that doesn't exist covers the same branch here.
        assert storage.path_size_bytes(tmp_path / "ghost") == 0
