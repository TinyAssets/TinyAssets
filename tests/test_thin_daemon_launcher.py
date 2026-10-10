"""The container's ``__main__`` must cost a spawn child nothing.

Live 2026-09-29: each served round on the free account waited about 20 s before
its model was asked. Each round spawns about three credential-broker children,
and a spawn child re-imports the parent's ``__main__`` by name before running
anything; under ``python -m tinyassets.universe_server`` that was the whole
server, about 5 s each (4.95-5.04 s measured in the production image, against
0.25 s under a light ``__main__``).
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Exactly what ``multiprocessing.spawn`` does in the child for a ``-m`` parent
# (``_fixup_main_from_name``), then report whether the server's stack got loaded.
# The server module itself runs as ``__mp_main__`` there, so its name is not in
# ``sys.modules``; its MCP framework is the witness.
_AS_SPAWN_CHILD = (
    "import runpy, sys; "
    "runpy.run_module({module!r}, run_name='__mp_main__', alter_sys=True); "
    "print('fastmcp' in sys.modules)"
)
# The same for a script parent (``_fixup_main_from_path``): the image CMD.
_AS_SCRIPT_SPAWN_CHILD = (
    "import runpy, sys; "
    "runpy.run_path({path!r}, run_name='__mp_main__'); "
    "print('fastmcp' in sys.modules)"
)


def _child_imports_server(module: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-c", _AS_SPAWN_CHILD.format(module=module)],
        cwd=ROOT, capture_output=True, text=True, timeout=300, check=True,
    )
    return result.stdout.strip().splitlines()[-1] == "True"


def test_a_spawn_child_of_the_launcher_imports_no_server():
    assert _child_imports_server("tinyassets.serve") is False


def test_the_old_entry_point_is_what_cost_every_child_the_server():
    """The control: the same child under the old ``__main__`` pays for the server."""
    assert _child_imports_server("tinyassets.universe_server") is True


def test_a_spawn_child_of_the_pid1_launcher_imports_no_server():
    result = subprocess.run(
        [sys.executable, "-c", _AS_SCRIPT_SPAWN_CHILD.format(
            path=str(ROOT / "deploy" / "role_launcher.py"))],
        cwd=ROOT, capture_output=True, text=True, timeout=300, check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "False"


def test_the_image_runs_the_pid1_launcher():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    commands = re.findall(r"^CMD\s+(.+)$", dockerfile, flags=re.MULTILINE)
    assert commands == ['["/opt/venv/bin/python", "-I", "-B", '
                        '"/usr/local/libexec/ta-launch.py"]']


def test_the_launcher_starts_the_same_server():
    source = (ROOT / "tinyassets" / "serve.py").read_text(encoding="utf-8")
    body = source.split('if __name__ == "__main__":', 1)
    # Nothing but the docstring runs on import.
    assert "import" not in body[0].split('"""')[-1]
    assert "from tinyassets.universe_server import main" in body[1]
    assert "main()" in body[1]
