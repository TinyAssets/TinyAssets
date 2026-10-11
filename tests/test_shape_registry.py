"""Structural checks for every row of the architecture Shape registry."""

import re
from pathlib import Path

from scripts import check_shapes


def test_shape_registry():
    assert not (problems := check_shapes.check()), "\n".join(problems)


def test_registry_parity_catches_added_row():
    rows = check_shapes.registry_rows(
        "| S1 | one | path | old | drifted | guard | C3 |\n"
        "| S29 | new | path | old | drifted | guard | C13 |"
    )
    assert rows["S29"] == "drifted"
    assert set(rows) - set(check_shapes.SHAPES) == {"S29"}


def test_old_count_is_a_ceiling(tmp_path: Path):
    path = tmp_path / "tinyassets" / "providers"
    path.mkdir(parents=True)
    (path / "old.py").write_text("call_provider(x)\ncall_provider(y)\n", encoding="utf-8")
    shape = check_shapes.Shape((), (("tinyassets/**/*.py", r"call_provider\("),), ceiling=1)
    assert check_shapes.old_count(shape, tmp_path) == 2


def test_recursive_file_probe_works_on_python_311(tmp_path: Path):
    nested = tmp_path / "mirror" / "nested"
    nested.mkdir(parents=True)
    (nested / "module.py").write_text("pass\n", encoding="utf-8")
    shape = check_shapes.Shape((), (("mirror/**", ""),), ceiling=1)
    assert check_shapes.old_count(shape, tmp_path) == 1


def test_public_route_and_opt_in_flag_on_existing_file():
    changes = {
        "tinyassets/universe_server.py": 'app.mount("/other", other)\n'
        'if os.getenv("TINYASSETS_AGENT_LOOP"):'
    }
    assert any(
        problem.startswith("public-routes:")
        for problem in check_shapes.added_line_problems(changes)
    )
    assert any(
        problem.startswith("opt-in-flags:") for problem in check_shapes.added_line_problems(changes)
    )
    assert not check_shapes.added_line_problems(
        {"tinyassets/universe_server.py": 'app.mount("/api/v1", api)'}
    )
    assert check_shapes.added_line_problems(
        {"tinyassets/onboarding/__init__.py": 'Route("/other", handler)'}
    ) == ["public-routes: tinyassets/onboarding/__init__.py mounts /other"]


def test_new_model_call_entry_symbol_is_detected_outside_registered_path():
    assert re.search(check_shapes.ENTRY_SYMBOLS["S1"], "call_provider(model)")
    assert re.search(check_shapes.ENTRY_SYMBOLS["S1"], "def call_provider(model): pass")
    assert not Path("tinyassets/another_model.py").match(check_shapes.SHAPES["S1"].paths[0])
    assert check_shapes.registered_path(
        "tinyassets/control_plane/sub/worker.py", "tinyassets/control_plane/**"
    )


def test_base_pin_parser_records_ceiling_and_probes():
    baseline = (
        "SHAPES: dict[str, Shape] = {"
        "'S1': Shape(('new.py',), (('old.py', 'call_provider'),), "
        "forbid=('unsafe',), ceiling=4)"
        "}"
    )
    assert check_shapes.recorded_shapes(baseline) == {
        "S1": (4, (("old.py", "call_provider"),), ("unsafe",))
    }
