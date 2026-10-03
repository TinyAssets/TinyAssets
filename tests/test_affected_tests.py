"""Tests for scripts/affected_tests.py, the PR-time test selector.

A selector that under-selects only defers a failure to the merge queue, but
one that silently selects nothing makes the PR run a green no-op, so every
"run everything" fallback is pinned here alongside the selection rules.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "affected_tests.py"
_spec = importlib.util.spec_from_file_location("affected_tests", _SCRIPT)
assert _spec and _spec.loader
at = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(at)


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    base = {
        "tinyassets/__init__.py": "",
        "tests/__init__.py": "",
        "tests/conftest.py": "import tinyassets.shared\n",
        "tinyassets/shared.py": "",
    }
    for rel, src in {**base, **files}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(src, encoding="utf-8")
    return tmp_path


def test_direct_and_import_time_transitive_importers_are_selected(tmp_path):
    root = _repo(
        tmp_path,
        {
            "tinyassets/core.py": "",
            "tinyassets/mid.py": "from tinyassets import core\n",
            "tests/test_direct.py": "import tinyassets.core\n",
            "tests/test_transitive.py": "from tinyassets.mid import x\n",
            "tests/test_other.py": "import json\n",
        },
    )
    selected, _ = at.select(["tinyassets/core.py"], root)
    assert selected == ["tests/test_direct.py", "tests/test_transitive.py"]


def test_a_lazy_import_inside_a_function_is_not_followed(tmp_path):
    """Following lazy imports made every test reach ~470 of 525 modules."""
    root = _repo(
        tmp_path,
        {
            "tinyassets/core.py": "",
            "tinyassets/mid.py": "def f():\n    import tinyassets.core\n",
            "tests/test_mid.py": "import tinyassets.mid\n",
            "tests/test_core.py": "import tinyassets.core\n",
        },
    )
    selected, _ = at.select(["tinyassets/core.py"], root)
    assert selected == ["tests/test_core.py"]


def test_a_test_files_own_lazy_import_and_patch_string_count(tmp_path):
    root = _repo(
        tmp_path,
        {
            "tinyassets/core.py": "",
            "tinyassets/other.py": "",
            "tests/test_lazy.py": "def test_x():\n    import tinyassets.core\n",
            "tests/test_patch.py": "PATCH = 'tinyassets.other.thing'\n",
        },
    )
    assert at.select(["tinyassets/core.py"], root)[0] == ["tests/test_lazy.py"]
    assert at.select(["tinyassets/other.py"], root)[0] == ["tests/test_patch.py"]


def test_a_test_naming_a_file_by_path_is_selected(tmp_path):
    root = _repo(
        tmp_path,
        {
            "tinyassets/onboarding/app.html": "<html>",
            "docs/concerns/one.md": "x",
            "tests/test_pathlib.py": 'P = ROOT / "tinyassets" / "onboarding" / "app.html"\n',
            "tests/test_dir.py": 'D = ROOT / "docs" / "concerns"\n',
            "tests/test_none.py": "import json\n",
        },
    )
    assert at.select(["tinyassets/onboarding/app.html"], root)[0] == ["tests/test_pathlib.py"]
    assert at.select(["docs/concerns/one.md"], root)[0] == ["tests/test_dir.py"]


def test_a_changed_test_runs_and_a_deleted_one_does_not(tmp_path):
    root = _repo(tmp_path, {"tests/test_mine.py": "import json\n"})
    assert at.select(["tests/test_mine.py", "tests/test_gone.py"], root)[0] == [
        "tests/test_mine.py"
    ]


def test_an_unmentioned_non_python_change_selects_nothing(tmp_path):
    root = _repo(tmp_path, {"docs/notes/x.md": "x", "tests/test_a.py": "import json\n"})
    assert at.select(["docs/notes/x.md"], root)[0] == []


def test_shared_infrastructure_runs_everything(tmp_path):
    root = _repo(tmp_path, {"tests/test_a.py": "import json\n"})
    for rel in ("tests/conftest.py", "pyproject.toml", ".github/known-failing-tests.txt"):
        selected, reasons = at.select([rel], root)
        assert selected is None, rel
        assert "shared test infrastructure" in reasons[0]


def test_a_module_conftest_loads_at_runtime_runs_everything(tmp_path):
    """The static graph undercounts conftest: it calls code that imports lazily."""
    root = _repo(
        tmp_path,
        {
            "tests/conftest.py": "import tinyassets.shared\ntinyassets.shared.boot()\n",
            "tinyassets/shared.py": "def boot():\n    import tinyassets.lazy\n",
            "tinyassets/lazy.py": "",
            "tests/test_a.py": "import tinyassets.lazy\n",
        },
    )
    selected, reasons = at.select(["tinyassets/lazy.py"], root)
    assert selected is None
    assert "conftest" in reasons[0]


def test_a_conftest_that_will_not_import_raises_rather_than_guessing(tmp_path):
    root = _repo(tmp_path, {"tests/conftest.py": "raise ImportError('boom')\n"})
    try:
        at.select(["docs/x.md"], root)
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("an unimportable conftest must not yield a selection")


def test_a_python_file_no_test_reaches_runs_everything(tmp_path):
    root = _repo(tmp_path, {"tinyassets/orphan.py": "", "tests/test_a.py": "import json\n"})
    selected, reasons = at.select(["tinyassets/orphan.py"], root)
    assert selected is None
    assert "no test reaches" in reasons[0]


def test_a_deleted_source_module_runs_everything(tmp_path):
    """Its importers are invisible: the graph is built from the tree without it."""
    root = _repo(tmp_path, {"tests/test_a.py": "import json\n"})
    selected, reasons = at.select(["tinyassets/gone.py"], root)
    assert selected is None
    assert "deleted or moved" in reasons[0]


def test_a_module_only_a_conftest_fixture_imports_runs_everything(tmp_path):
    """Autouse fixture bodies run for every test; module-body probing misses them."""
    root = _repo(
        tmp_path,
        {
            "tests/conftest.py": (
                "import pytest\n\n@pytest.fixture(autouse=True)\n"
                "def _f():\n    import tinyassets.fixture_only\n"
            ),
            "tinyassets/fixture_only.py": "",
            "tests/test_a.py": "import json\n",
        },
    )
    selected, reasons = at.select(["tinyassets/fixture_only.py"], root)
    assert selected is None
    assert "conftest" in reasons[0]


def test_a_tree_walker_naming_a_top_level_root_is_selected(tmp_path):
    root = _repo(
        tmp_path,
        {
            "mobile/package.json": "{}",
            "tests/test_walk.py": 'for p in (ROOT / "mobile").rglob("*"):\n    pass\n',
            "tests/test_named_only.py": 'X = "mobile"\n',
        },
    )
    # The bare root alone is too common to match a non-walking test on.
    assert at.select(["mobile/package.json"], root)[0] == ["tests/test_walk.py"]


def test_cli_writes_all_for_a_full_suite_trigger(tmp_path, monkeypatch):
    out = tmp_path / "sel.txt"
    monkeypatch.setattr(
        "sys.argv", ["affected_tests.py", "--changed", "pyproject.toml", "--out", str(out)]
    )
    assert at.main() == 0
    assert out.read_text(encoding="utf-8") == "ALL\n"


def test_every_full_suite_trigger_exists_in_the_repo():
    """A renamed trigger silently stops forcing the full run."""
    missing = [rel for rel in at.FULL_SUITE_TRIGGERS if not (at.REPO_ROOT / rel).is_file()]
    assert not missing, missing
