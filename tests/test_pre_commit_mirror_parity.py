"""The index guard catches a staged generated runtime file."""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check_mirror_parity.py"


def test_checker_rejects_tracked_runtime(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    runtime = repo / "packaging/claude-plugin/plugins/tinyassets-universe-server/runtime"
    runtime.mkdir(parents=True)
    (runtime / "server.py").write_text("pass\n")
    subprocess.run(["git", "add", "-f", "packaging"], cwd=repo, check=True)
    # Drive the shared invariant against the temporary index.
    from scripts.invariants.mirror_parity import MirrorParityInvariant
    result = MirrorParityInvariant(root=repo).check()
    assert result.status.value == "violated"
    assert "server.py" in result.message or result.evidence["paths"][0].endswith("server.py")
