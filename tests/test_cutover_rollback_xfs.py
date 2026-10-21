"""Root Linux proof: isolated container, separate XFS store, no host data mounts.

Run: python scripts/linux_oracle.py --docker-engine --as-root --
     tests/test_cutover_rollback_xfs.py -q -s
"""

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from tests.support import cutover_rollback_cases


@pytest.mark.verifies('owner-daemon-in-box:C4')
def test_rollback_on_separate_xfs():
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip(
            "requires linux_oracle.py --as-root; owner=c6-owner-box runs-in=linux_oracle.py"
        )
    base = os.environ["TA_REHEARSAL_BASE_IMAGE"]
    root = Path(__file__).resolve().parents[1]
    cases = Path(cutover_rollback_cases.__file__).relative_to(root).as_posix()
    name = "ta-rollback-proof-" + uuid.uuid4().hex[:12]

    def command(*args):
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=600)

    command("docker", "create", "--name", name, "--init", "--privileged", "--network", "none",
            "--label", "tinyassets.disposable=true", "--workdir", "/proof",
            "--entrypoint", "python", base, "-m", "pytest",
            cases, "-q", "-ra", "--tb=short",
            "--basetemp=/tmp/rollback")
    try:
        for entry in ("tinyassets", "tests", "deploy", "scripts", "pyproject.toml"):
            command("docker", "cp", str(root / entry), name + ":/proof/" + entry)
        result = subprocess.run(["docker", "start", "--attach", name],
                                capture_output=True, text=True, timeout=1200)
        print(result.stdout + result.stderr, flush=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert command("docker", "inspect", "-f", "{{.State.ExitCode}}", name).stdout.strip() == "0"
    finally:
        command("docker", "rm", "-f", name)
