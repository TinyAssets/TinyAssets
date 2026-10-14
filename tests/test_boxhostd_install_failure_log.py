"""A failed boxhostd install prints the unit status and journal to the deploy log.

systemctl's own message is only "control process exited". The unit stderr
stays in the journal, which is what deploy run 38519889115 did not show.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "deploy/install-boxhostd-preflight.sh"
UNITS = (
    "boxhostd.service",
    "box-store-device.service",
    "var-lib-boxhostd-owners.mount",
)


def _dump_function() -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(r"^dump_boxhostd_failure\(\) \{\n(?:.*\n)*?^\}", text, re.M)
    assert match, "installer has no dump_boxhostd_failure function"
    return match.group(0)


def test_installer_dumps_unit_diagnostics_on_any_error() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert "trap dump_boxhostd_failure ERR" in text
    function = _dump_function()
    for unit in UNITS:
        assert unit in function
    assert "journalctl -u" in function
    assert "-n 80" in function
    assert "systemctl status" in function


def test_error_trap_prints_status_and_journal_then_exits_nonzero(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "stub"
    stub.write_text(
        '#!/bin/sh\nprintf \'called %s %s\\n\' "$(basename "$0")" "$*"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    (bin_dir / "systemctl").symlink_to(stub)
    (bin_dir / "journalctl").symlink_to(stub)
    script = tmp_path / "fail.sh"
    script.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        + _dump_function()
        + "\ntrap dump_boxhostd_failure ERR\nfalse\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "boxhostd install failed; unit diagnostics follow" in output
    for unit in UNITS:
        assert f"called systemctl status {unit} --no-pager -l" in output
        assert f"called journalctl -u {unit} -n 80 --no-pager" in output
