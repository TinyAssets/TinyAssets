"""The boxhostd installer only sees the files deploy-prod copies beside it.

Deploy run 38516322795 failed with `bash: /tmp/box-store-unit.sh: No such file
or directory`: install-boxhostd-preflight.sh read a file the scp list omitted.
#4835 named that file in the scp list. The list is now derived from the
installer, and this check still requires every sibling the installer reads.
"""

import re
from pathlib import Path

from scripts.boxhostd_install_files import ship_list

ROOT = Path(__file__).resolve().parents[1]


def _copied_names() -> set[str]:
    workflow = (ROOT / ".github/workflows/deploy-prod.yml").read_text(encoding="utf-8")
    start = workflow.index('python scripts/boxhostd_install_files.py > "$list"')
    block = workflow[start : workflow.index(":/tmp/", start)]
    if '"${boxhostd_files[@]}"' not in block:
        return set()
    named = re.findall(r"(?:deploy|scripts)/[\w.-]+", block)
    if any(path != "scripts/boxhostd_install_files.py" for path in named):
        return set()
    return {Path(path).name for path in ship_list(ROOT)}


def _installer_reads() -> set[str]:
    installer = (ROOT / "deploy/install-boxhostd-preflight.sh").read_text(encoding="utf-8")
    names = set(re.findall(r"\$\{source_dir\}/([\w.-]+)", installer))
    for loop in re.findall(r"for name in ([^;]+);", installer):
        names.update(loop.split())
    return names


def test_every_file_the_installer_reads_is_copied():
    assert _installer_reads() - _copied_names() == set()
