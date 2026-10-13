from __future__ import annotations

import os
import subprocess
import venv
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parent
SERVER_PATH = RUNTIME_ROOT / "server.py"
REQUIREMENTS_PATH = RUNTIME_ROOT / "requirements.txt"
VENV_ROOT = RUNTIME_ROOT / ".venv"


def _venv_python() -> Path:
    if os.name == "nt":
        return VENV_ROOT / "Scripts" / "python.exe"
    return VENV_ROOT / "bin" / "python"


def _create_venv() -> None:
    builder = venv.EnvBuilder(with_pip=True)
    builder.create(VENV_ROOT)


def _ensure_runtime() -> Path:
    python_path = _venv_python()
    if not python_path.is_file():
        _create_venv()

    probe = subprocess.run(
        [str(python_path), "-c", "import fastmcp"],
        check=False,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        subprocess.run(
            [
                str(python_path),
                "-m",
                "pip",
                "install",
                "-r",
                str(REQUIREMENTS_PATH),
            ],
            check=True,
        )
    return python_path


def main() -> None:
    python_path = _ensure_runtime()
    subprocess.run(
        [str(python_path), str(SERVER_PATH)],
        check=True,
        env=os.environ.copy(),
    )


if __name__ == "__main__":
    main()
