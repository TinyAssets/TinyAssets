"""Every script a workflow or a composite action runs or imports exists: a renamed script
leaves its callers' steps naming a missing file, and only that step's own run shows it."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_PATH = re.compile(r"(?<![\w/.-])scripts/[\w/.-]+\.py\b")
_MODULE = re.compile(r"\bscripts\.(\w+)|\bfrom scripts import (\w+(?:, *\w+)*)")


def _named(text: str) -> set[str]:
    """The `scripts/` files a step's shell names: by path, or by module."""
    named = set(_PATH.findall(text))
    for module, names in _MODULE.findall(text):
        named |= {f"scripts/{name.strip()}.py" for name in (module or names).split(",")}
    return named


def _steps() -> list[tuple[str, Path, str]]:
    """(file, working directory, shell) of every `run` step."""
    files = sorted((_ROOT / ".github" / "workflows").glob("*.y*ml"))
    files += sorted((_ROOT / ".github" / "actions").glob("*/action.y*ml"))
    out = []
    for file in files:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        top = (data.get("defaults") or {}).get("run", {}).get("working-directory", ".")
        for job in list((data.get("jobs") or {}).values()) or [data.get("runs") or {}]:
            here = (job.get("defaults") or {}).get("run", {}).get("working-directory", top)
            out += [(file.relative_to(_ROOT).as_posix(),
                     _ROOT / step.get("working-directory", here), step["run"])
                    for step in job.get("steps") or [] if "run" in step]
    return out


def test_every_script_a_step_names_exists() -> None:
    missing = [f"{file}: {name}" for file, where, shell in _steps()
               for name in sorted(_named(shell))
               if not (where / name).is_file() and not (where / name).with_suffix("").is_dir()]
    assert not missing, missing


def test_a_step_names_a_script_by_path_or_by_module() -> None:
    assert _named("python scripts/a.py --x; python -m scripts.b\n"
                  "python -c 'from scripts.c import d; from scripts import e, f'") == {
        f"scripts/{name}.py" for name in "abcef"}
    assert ("review.yml", "scripts/review_checks.py") in {
        (Path(file).name, name) for file, _, shell in _steps() for name in _named(shell)}
