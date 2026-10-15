"""The owner-box restore accepts only the two members committed together."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "deploy" / "backup-pair.py"


def pair(command: str, data: Path, box: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), command, str(data), str(box), str(manifest)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_paired_manifest_rejects_a_changed_or_replaced_member(tmp_path: Path) -> None:
    data = tmp_path / "tinyassets-data-2026-10-14T01-00-00Z.tar.gz"
    box = tmp_path / "tinyassets-box-store-2026-10-14T01-00-00Z.tar.gz"
    manifest = tmp_path / "tinyassets-pair-2026-10-14T01-00-00Z.json"
    data.write_bytes(b"data generation one")
    box.write_bytes(b"box generation one")

    assert pair("create", data, box, manifest).returncode == 0
    assert pair("verify", data, box, manifest).returncode == 0
    box.write_bytes(b"box generation two")
    assert pair("verify", data, box, manifest).returncode != 0
    box.write_bytes(b"box generation one")
    assert pair("verify", data, box, manifest).returncode == 0

    replacement = tmp_path / "replacement"
    replacement.write_bytes(data.read_bytes())
    data.unlink()
    if os.name == "posix":
        data.symlink_to(replacement)
    else:
        os.link(replacement, data)
    assert pair("verify", data, box, manifest).returncode != 0
