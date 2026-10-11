"""Structural checks for every row of the architecture Shape registry."""

import re
from pathlib import Path

from scripts import check_shapes


def test_shape_registry():
    assert not (problems := check_shapes.check()), "\n".join(problems)


def test_registry_parity_catches_added_row():
    rows = check_shapes.registry_rows(
        "| S1 | one | path | old | drifted | guard | C3 |\n"
        "| S28 | new | path | old | drifted | guard | C13 |"
    )
    assert rows["S28"] == "drifted"
    assert set(rows) - set(check_shapes.SHAPES) == {"S28"}


def test_old_count_is_a_ceiling(tmp_path: Path):
    path = tmp_path / "tinyassets" / "providers"
    path.mkdir(parents=True)
    (path / "old.py").write_text("call_provider(x)\ncall_provider(y)\n", encoding="utf-8")
    shape = check_shapes.Shape((), (("tinyassets/**/*.py", r"call_provider\("),), ceiling=1)
    assert check_shapes.old_count(shape, tmp_path) == 2


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


def test_new_model_call_entry_symbol_is_detected_outside_registered_path():
    source = "def call_provider(model):\n    return model"
    assert re.search(check_shapes.ENTRY_SYMBOLS["S1"], source)
    assert not Path("tinyassets/another_model.py").match(check_shapes.SHAPES["S1"].paths[0])
