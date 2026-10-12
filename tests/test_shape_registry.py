"""Structural checks for every row of the architecture Shape registry."""

import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts import check_shapes


def test_shape_registry():
    assert not (problems := check_shapes.check()), "\n".join(problems)


def test_registry_parity_catches_added_row():
    rows = check_shapes.registry_rows(
        "| S1 | one | path | old | drifted | guard | C3 |\n"
        "| N11 | new | path | old | unbuilt | guard | C13 |"
    )
    assert rows["N11"] == "unbuilt"
    assert set(rows) - set(check_shapes.SHAPES) == {"N11"}


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
        {
            "tinyassets/universe_server.py": (
                'app.mount("/api", api)\napp.mount("/hooks/inbound", inbound)\n'
                'Route("/", home)\nRoute("/.well-known/oauth-authorization-server", oauth)\n'
                'Route("/.well-known/oauth-protected-resource", oauth)'
            )
        }
    )
    assert check_shapes.PUBLIC_ROOTS == {"/", "/mcp", "/api", "/hooks/inbound", "/app"}
    assert check_shapes.added_line_problems(
        {"tinyassets/onboarding/__init__.py": 'Route("/other", handler)'}
    ) == ["public-routes: tinyassets/onboarding/__init__.py mounts /other"]
    assert check_shapes.added_line_problems(
        {"tinyassets/api/extensions.py": 'Route("/.well-knownness/evil", handler)'}
    ) == ["public-routes: tinyassets/api/extensions.py mounts /.well-knownness/evil"]


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


def test_mirror_count_only_includes_orphans(tmp_path: Path):
    canonical = tmp_path / "tinyassets"
    mirror = tmp_path / check_shapes.MIRROR_ROOT / "tinyassets"
    canonical.mkdir(parents=True)
    mirror.mkdir(parents=True)
    (canonical / "paired.py").write_text("pass\n", encoding="utf-8")
    (mirror / "paired.py").write_text("pass\n", encoding="utf-8")
    (mirror / "orphan.py").write_text("pass\n", encoding="utf-8")
    assert check_shapes.old_count(check_shapes.SHAPES["S19"], tmp_path) == 1


def test_line_ratchet_catches_growth_without_file_growth(tmp_path: Path):
    path = tmp_path / "old.py"
    path.write_text("one\ntwo\n", encoding="utf-8")
    shape = check_shapes.Shape((), (("old.py", ""),), ceiling=1)
    assert check_shapes.old_count(shape, tmp_path) == 1
    assert check_shapes.old_lines(shape, tmp_path) == 2


def test_s22_counts_new_universe_modules_not_mentions(tmp_path: Path):
    root = tmp_path / "tinyassets"
    root.mkdir()
    # Target homes and mentions inside ordinary files never count: a routine fix
    # that adds `universe_id` somewhere must pass.
    (root / "universe_tools.py").write_text("universe\nextra fix\n", encoding="utf-8")
    (root / "universe_server.py").write_text("universe\n", encoding="utf-8")
    (root / "legacy.py").write_text("universe_id = 'Universe'\n", encoding="utf-8")
    shape = check_shapes.SHAPES["S22"]
    assert "S22" not in check_shapes.LINE_CEILINGS
    assert check_shapes.old_count(shape, tmp_path) == 0
    # A new universe_* module counts; renaming it to command_center lowers the count.
    (root / "universe_extra.py").write_text("x = 1\n", encoding="utf-8")
    assert check_shapes.old_count(shape, tmp_path) == 1
    (root / "universe_extra.py").rename(root / "command_center_extra.py")
    assert check_shapes.old_count(shape, tmp_path) == 0
    assert check_shapes.registered_path("tinyassets/universe_tools.py", shape.paths[1])
    assert check_shapes.registered_path("tinyassets/universe_server.py", shape.paths[2])
    assert not check_shapes.registered_path(
        "tinyassets/nested/tinyassets/universe_tools.py", shape.paths[1]
    )


def test_raising_a_ceiling_fails_against_the_base_pin(monkeypatch):
    baseline = Path(check_shapes.__file__).read_text(encoding="utf-8")
    monkeypatch.setitem(
        check_shapes.SHAPES,
        "S19",
        replace(check_shapes.SHAPES["S19"], ceiling=7),
    )
    with (
        patch.object(check_shapes, "_merge_base", return_value="base"),
        patch.object(
            check_shapes.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, stdout=baseline),
        ),
    ):
        assert "S19 ceiling: pin raised 6 -> 7" in check_shapes.base_shape_problems()


def test_old_path_growth_requires_labeled_floor_reason(tmp_path: Path, monkeypatch):
    baseline = Path(check_shapes.__file__).read_text(encoding="utf-8")
    monkeypatch.setitem(check_shapes.LINE_CEILINGS, "S3", 2435)
    event = tmp_path / "event.json"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))

    def base_problems():
        with (
            patch.object(check_shapes, "_merge_base", return_value="base"),
            patch.object(
                check_shapes.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout=baseline),
            ),
        ):
            return check_shapes.base_shape_problems()

    assert "S3 line ceiling: pin raised 2434 -> 2435" in base_problems()

    monkeypatch.delitem(check_shapes.LINE_CEILINGS, "S3")
    assert "S3 line ceiling: pin removed" in base_problems()
    monkeypatch.setitem(check_shapes.LINE_CEILINGS, "S3", 2435)

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "merge_group")
    monkeypatch.setenv("GITHUB_SHA", "queued-head")
    event.write_text(json.dumps({"merge_group": {"head_sha": "queued-head"}}), encoding="utf-8")
    assert "S3 line ceiling: pin raised 2434 -> 2435" not in base_problems()
    monkeypatch.setenv("GITHUB_SHA", "different-head")
    assert "S3 line ceiling: pin raised 2434 -> 2435" in base_problems()
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "labels": [{"name": "shape-ceiling-raise"}],
                    "body": "Floor fix: outage in the native CLI loop.",
                }
            }
        ),
        encoding="utf-8",
    )
    assert "S3 line ceiling: pin raised 2434 -> 2435" not in base_problems()
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "labels": [{"name": "shape-ceiling-raise"}],
                    "body": "Routine cleanup",
                }
            }
        ),
        encoding="utf-8",
    )
    assert "S3 line ceiling: pin raised 2434 -> 2435" in base_problems()
    event.write_text(
        json.dumps({"pull_request": {"labels": [], "body": "Floor fix: outage."}}),
        encoding="utf-8",
    )
    assert "S3 line ceiling: pin raised 2434 -> 2435" in base_problems()


def test_git_added_uses_the_pr_diff_not_untracked_files():
    def git_diff_only(command, **kwargs):
        assert command[:4] == ["git", "diff", "--no-renames", "--name-only"]
        return subprocess.CompletedProcess(command, 0, stdout="tinyassets/new.py\n")

    with (
        patch.object(check_shapes, "_merge_base", return_value="base"),
        patch.object(check_shapes.subprocess, "run", side_effect=git_diff_only),
    ):
        assert check_shapes.git_added() == {"tinyassets/new.py"}
