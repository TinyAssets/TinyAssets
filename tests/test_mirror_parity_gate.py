"""The plugin runtime is generated and absent from Git's index."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.invariants.mirror_parity import tracked_mirror

ROOT = Path(__file__).resolve().parents[1]


def test_no_plugin_runtime_files_tracked() -> None:
    assert tracked_mirror(ROOT) == []
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_mirror_parity.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_hook_and_ci_run_the_same_guard() -> None:
    hook = (ROOT / "scripts/git-hooks/pre-commit").read_text()
    workflow = (ROOT / ".github/workflows/build-bundle.yml").read_text()
    assert "${MIRROR_PYTHON} scripts/check_mirror_parity.py" in hook
    assert "python scripts/check_mirror_parity.py" in workflow
    assert "command -v python3" in hook
    assert "python is required to check tracked plugin runtime files" in hook
