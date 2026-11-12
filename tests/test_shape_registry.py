"""Structural checks for every row of the architecture Shape registry."""

import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import check_shapes


@pytest.fixture(autouse=True)
def _no_ambient_ci_event(monkeypatch, request):
    """A real CI run sets these for its own event; each test sets what it needs."""
    if request.node.name == "test_shape_registry":
        return  # the live registry check runs against the real event
    for name in ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH",
                 "GITHUB_SHA", "MERGE_GROUP_BASE_SHA", "ORACLE_SOURCE_HEAD"):
        monkeypatch.delenv(name, raising=False)


def test_shape_registry():
    assert not (problems := check_shapes.check()), "\n".join(problems)


def test_s2_retains_executor_measure_and_s3_tracks_duplicate(tmp_path):
    root = tmp_path / 'tinyassets'
    root.mkdir()
    (root / 'second.py').write_text(
        '_call_writer()\nmake_interactive_agent_turn()\n'
        'class WorkflowAgentTurn(AgentTurnCoordinator): pass\n')
    assert check_shapes.old_count(check_shapes.SHAPES['S2'], tmp_path) >= 2
    assert check_shapes.old_count(check_shapes.SHAPES['S3'], tmp_path) >= 1


def test_registry_parity_catches_added_row():
    rows = check_shapes.registry_rows(
        "| S1 | one | path | old | drifted | guard | C3 |\n"
        "| N99 | new | path | old | unbuilt | guard | C13 |"
    )
    assert rows["N99"] == "unbuilt"
    assert set(rows) - set(check_shapes.SHAPES) == {"N99"}


def test_old_count_is_a_ceiling(tmp_path: Path):
    path = tmp_path / "tinyassets" / "providers"
    path.mkdir(parents=True)
    (path / "old.py").write_text("call_provider(x)\ncall_provider(y)\n", encoding="utf-8")
    shape = check_shapes.Shape((), (("tinyassets/**/*.py", r"call_provider\("),), ceiling=1)
    assert check_shapes.old_count(shape, tmp_path) == 2


def test_c3_owner_helpers_are_not_platform_calls(tmp_path):
    owner = tmp_path / "tinyassets/memory"
    owner.mkdir(parents=True)
    (owner / "manager.py").write_text("from tinyassets.memory.reflexion import ReflexionEngine\n")
    shape = check_shapes.SHAPES["S13"]
    assert check_shapes.old_count(shape, tmp_path) == 0
    shared = tmp_path / "tinyassets/broker"
    shared.mkdir()
    (shared / "worker.py").write_text("from tinyassets.memory.reflexion import ReflexionEngine\n")
    assert check_shapes.old_count(shape, tmp_path) == 1
    for bypass in ("from tinyassets.providers.router import ProviderRouter",
                   "router.call_sync(prompt)", "provider.complete(prompt)",
                   "call_provider(prompt)",
                   "from tinyassets.providers import call as bridge; "
                   "bridge.get_provider_router().call(prompt)"):
        assert any(re.search(pattern, bypass) for pattern in shape.forbid)


def test_s13_ceiling_counts_async_router_bridge(tmp_path):
    path = tmp_path / "tinyassets" / "shared.py"
    path.parent.mkdir()
    path.write_text("await bridge.get_provider_router().call('writer')\n")
    assert check_shapes.old_count(check_shapes.SHAPES["S13"], tmp_path) == 1


def test_c3_sampling_and_mock_fallbacks_cannot_return(tmp_path):
    path = tmp_path / "tinyassets"
    path.mkdir()
    (path / "review.py").write_text("session.create_message(prompt)\n")
    assert check_shapes.old_count(check_shapes.SHAPES["S1"], tmp_path) == 1
    for mock in ("_force_mock = True", "return '[MOCK] success'"):
        assert any(re.search(pattern, mock) for pattern in check_shapes.SHAPES["S23"].forbid)


def test_s8_public_forwarder_belongs_only_to_the_owner_daemon(tmp_path):
    root = tmp_path / "tinyassets"
    root.mkdir()
    (root / "owner_public_egress.py").write_text("class OwnerPublicForwarder: pass\n")
    assert check_shapes.old_count(check_shapes.SHAPES["S8"], tmp_path) == 0
    (root / "daemon_server.py").write_text("from tinyassets.owner_public_egress import PROXY_ENV\n")
    (root / "owner_router.py").write_text("OwnerPublicForwarder(relay)\n")
    (root / "router").mkdir()
    (root / "router" / "worker.py").write_text("JAIL_PROXY_URL = 'second path'\n")
    assert check_shapes.old_count(check_shapes.SHAPES["S8"], tmp_path) == 3


def test_recursive_file_probe_works_on_python_311(tmp_path: Path):
    nested = tmp_path / "mirror" / "nested"
    nested.mkdir(parents=True)
    (nested / "module.py").write_text("pass\n", encoding="utf-8")
    shape = check_shapes.Shape((), (("mirror/**", ""),), ceiling=1)
    assert check_shapes.old_count(shape, tmp_path) == 1


def test_c5_host_domain_and_spawn_guards(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\n'
        '[project.scripts]\n'
        + "\n".join(f'{name} = "{target}"' for name, target in check_shapes.LIVE_SCRIPTS.items())
        + '\n[project.entry-points."tinyassets.domains"]\n'
        + "\n".join(
            f'{name} = "{target}"'
            for name, target in check_shapes.LIVE_DOMAIN_ENTRY_POINTS.items()
        )
        + "\n",
        encoding="utf-8",
    )
    assert check_shapes.dead_entry_problems(tmp_path) == []
    host = tmp_path / "tinyassets" / "host_pool"
    host.mkdir(parents=True)
    (host / "__init__.py").write_text("", encoding="utf-8")
    assert "host-paths: tinyassets/host_pool" in check_shapes.dead_entry_problems(tmp_path)
    (host / "__init__.py").unlink()
    host.rmdir()
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        + 'new_domain = "third_party.entry:Domain"\n',
        encoding="utf-8",
    )
    assert any(
        p.startswith("domain-entry-points:")
        for p in check_shapes.dead_entry_problems(tmp_path)
    )
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8")
        + '[project.gui-scripts]\nlegacy = "tinyassets.desktop.launcher:main"\n',
        encoding="utf-8",
    )
    assert any(p.startswith("spawn-entry:") for p in check_shapes.dead_entry_problems(tmp_path))


def test_c5_retired_prototype_compose_and_local_mcp_guards(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\n[project.scripts]\n'
        + "\n".join(f'{name} = "{target}"' for name, target in check_shapes.LIVE_SCRIPTS.items())
        + '\n[project.entry-points."tinyassets.domains"]\n'
        + "\n".join(
            f'{name} = "{target}"'
            for name, target in check_shapes.LIVE_DOMAIN_ENTRY_POINTS.items()
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "prototype").mkdir()
    assert (
        "host-paths: prototype/ fixture tree returned"
        in check_shapes.dead_entry_problems(tmp_path)
    )
    (tmp_path / "prototype").rmdir()

    compose = tmp_path / "deploy/compose.yml"
    compose.parent.mkdir()
    compose.write_text("services:\n  slack-agent:\n    image: retired\n", encoding="utf-8")
    assert "host-paths: compose slack-agent returned" in check_shapes.dead_entry_problems(tmp_path)
    compose.unlink()

    runner = tmp_path / "fantasy_daemon/__main__.py"
    runner.parent.mkdir()
    runner.write_text("pass\n", encoding="utf-8")
    assert "host-paths: fantasy_daemon" in check_shapes.dead_entry_problems(tmp_path)


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
        {"tinyassets/api/workflow_actions.py": 'Route("/.well-knownness/evil", handler)'}
    ) == ["public-routes: tinyassets/api/workflow_actions.py mounts /.well-knownness/evil"]


def test_new_model_call_entry_symbol_is_detected_outside_registered_path():
    assert re.search(check_shapes.ENTRY_SYMBOLS["S1"], "call_provider(model)")
    assert re.search(check_shapes.ENTRY_SYMBOLS["S1"], "def call_provider(model): pass")
    assert not Path("tinyassets/another_model.py").match(check_shapes.SHAPES["S1"].paths[0])
    assert check_shapes.registered_path(
        "tinyassets/control_plane/sub/worker.py", "tinyassets/control_plane/**"
    )


def test_entry_symbols_scan_only_python():
    source = "class Scheduler {}\nexport function model_call() {}\n"
    assert check_shapes.second_path_problems("platform/owner/src/scheduler.ts", source) == []
    assert check_shapes.second_path_problems("platform/x.js", source) == []
    found = check_shapes.second_path_problems("tinyassets/new_scheduler.py", "class Scheduler:\n")
    assert found == [
        f"S9 second-path: tinyassets/new_scheduler.py matches {check_shapes.ENTRY_SYMBOLS['S9']}"
    ]


def test_owner_tick_and_account_limit_guards(tmp_path: Path):
    code = tmp_path / "tinyassets" / "legacy.py"
    code.parent.mkdir()
    code.write_text(
        'TINYASSETS_ASSIGNED_QUEUE_CONSUMER = "on"\nRUN_WRITE_LIMIT = 10\n',
        encoding="utf-8",
    )
    assert check_shapes.control_plane_policy_problems(tmp_path) == [
        "opt-in-flags: tinyassets/legacy.py gates the owner tick",
        "limit-names: tinyassets/legacy.py contains a retired account meter",
    ]
    for rel in check_shapes.CONSUMER_DISABLE_SITES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('TINYASSETS_ASSIGNED_QUEUE_CONSUMER = "off"\n', encoding="utf-8")
        assert check_shapes.added_line_problems({rel: path.read_text()}) == []
    assert check_shapes.control_plane_policy_problems(tmp_path) == [
        "opt-in-flags: tinyassets/legacy.py gates the owner tick",
        "limit-names: tinyassets/legacy.py contains a retired account meter",
    ]


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


def test_mirror_count_only_includes_tracked_files(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    mirror = tmp_path / check_shapes.MIRROR_ROOT
    mirror.mkdir(parents=True)
    (mirror / "generated.py").write_text("pass\n", encoding="utf-8")
    assert check_shapes.old_count(check_shapes.SHAPES["S19"], tmp_path) == 0
    subprocess.run(["git", "add", "-f", str(mirror / "generated.py")], cwd=tmp_path, check=True)
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
        assert "S19 ceiling: pin raised 0 -> 7" in check_shapes.base_shape_problems()


def test_old_path_growth_requires_labeled_floor_reason(tmp_path: Path, monkeypatch):
    baseline = Path(check_shapes.__file__).read_text(encoding="utf-8")
    monkeypatch.setitem(check_shapes.LINE_CEILINGS, "S3", 1336)
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

    assert "S3 line ceiling: pin raised 1335 -> 1336" in base_problems()

    monkeypatch.delitem(check_shapes.LINE_CEILINGS, "S3")
    assert "S3 line ceiling: pin removed" in base_problems()
    monkeypatch.setitem(check_shapes.LINE_CEILINGS, "S3", 1336)

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "merge_group")
    monkeypatch.setenv("GITHUB_SHA", "queued-head")
    event.write_text(json.dumps({"merge_group": {"head_sha": "queued-head"}}), encoding="utf-8")
    assert "S3 line ceiling: pin raised 1335 -> 1336" not in base_problems()
    monkeypatch.setenv("GITHUB_SHA", "different-head")
    assert "S3 line ceiling: pin raised 1335 -> 1336" in base_problems()
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
    assert "S3 line ceiling: pin raised 1335 -> 1336" not in base_problems()
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
    assert "S3 line ceiling: pin raised 1335 -> 1336" in base_problems()
    event.write_text(
        json.dumps({"pull_request": {"labels": [], "body": "Floor fix: outage."}}),
        encoding="utf-8",
    )
    assert "S3 line ceiling: pin raised 1335 -> 1336" in base_problems()


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "shape@localhost")
    _git(repo, "config", "user.name", "shape")


def _commit_file(repo: Path, name: str, content: str, message: str) -> str:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _queue_history(repo: Path) -> tuple[str, str]:
    """A0 (stale main) -> base (queue base) -> head (this entry adds one file)."""
    _init_repo(repo)
    _commit_file(repo, "tinyassets/old.py", "old\n", "a0")
    stale = _git(repo, "rev-parse", "HEAD")
    base = _commit_file(repo, "tinyassets/from_main.py", "main\n", "base")
    _commit_file(repo, "tinyassets/new.py", "new\n", "entry")
    _git(repo, "update-ref", "refs/remotes/origin/main", stale)
    return base, stale


def test_merge_group_diff_starts_at_base_sha_not_a_stale_main(tmp_path: Path, monkeypatch):
    """Each case owns the event context.

    A merge_group runner already has GITHUB_EVENT_PATH, GITHUB_ACTIONS and
    GITHUB_EVENT_NAME. Clearing only MERGE_GROUP_BASE_SHA lets the guard read
    that payload's base sha, which is not a commit in this temp repo.
    """
    repo = tmp_path / "checkout"
    base, stale = _queue_history(repo)

    # No queue base: the event name alone must not adopt a leaked payload.
    monkeypatch.setenv("GITHUB_EVENT_NAME", "merge_group")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("MERGE_GROUP_BASE_SHA", raising=False)
    assert check_shapes.git_added(repo) == {"tinyassets/from_main.py", "tinyassets/new.py"}

    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"merge_group": {"base_sha": base, "head_sha": "queued-head"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_EVENT_NAME", "merge_group")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("MERGE_GROUP_BASE_SHA", raising=False)
    assert check_shapes._merge_base(repo) == base
    assert check_shapes.git_added(repo) == {"tinyassets/new.py"}

    # The oracle has no event file; the shard passes the sha in the environment.
    monkeypatch.setenv("GITHUB_EVENT_NAME", "merge_group")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("MERGE_GROUP_BASE_SHA", base)
    assert check_shapes.git_added(repo) == {"tinyassets/new.py"}
    assert stale != base


def test_oracle_snapshot_borrows_the_queue_base_from_the_checkout_mount(
    tmp_path: Path, monkeypatch
):
    checkout = tmp_path / "checkout"
    base, _stale = _queue_history(checkout)
    snapshot = tmp_path / "work"
    _init_repo(snapshot)
    for name in ("tinyassets/old.py", "tinyassets/from_main.py", "tinyassets/new.py"):
        source = checkout / name
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    _git(snapshot, "add", "-A")
    _git(snapshot, "commit", "-q", "-m", "linux oracle snapshot")

    monkeypatch.setattr(check_shapes, "_checkout_git_dir", lambda: checkout / ".git")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setenv("MERGE_GROUP_BASE_SHA", base)
    assert check_shapes._merge_base(snapshot) == base
    assert check_shapes.git_added(snapshot) == {"tinyassets/new.py"}

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setenv("MERGE_GROUP_BASE_SHA", "a" * 40)
    with pytest.raises(RuntimeError, match="merge queue base"):
        check_shapes._merge_base(snapshot)


def test_actions_without_main_history_still_fails(tmp_path: Path, monkeypatch):
    repo = tmp_path / "shallow"
    _init_repo(repo)
    _commit_file(repo, "tinyassets/old.py", "old\n", "only")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("MERGE_GROUP_BASE_SHA", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.setattr(check_shapes, "_checkout_git_dir", lambda: None)
    with pytest.raises(RuntimeError, match="fetch-depth must be 0"):
        check_shapes._merge_base(repo)

    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("MERGE_GROUP_BASE_SHA", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert check_shapes._merge_base(repo) is None


def test_oracle_snapshot_borrows_head_parent_when_there_is_no_queue_base(
    tmp_path: Path, monkeypatch
):
    """A pull_request checkout of depth 2 has HEAD~1 and no merge_group.base_sha."""
    checkout = tmp_path / "checkout"
    _init_repo(checkout)
    parent = _commit_file(checkout, "tinyassets/old.py", "old\n", "base")
    _commit_file(checkout, "tinyassets/new.py", "new\n", "pr")
    snapshot = tmp_path / "work"
    _init_repo(snapshot)
    for name in ("tinyassets/old.py", "tinyassets/new.py"):
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((checkout / name).read_text(encoding="utf-8"), encoding="utf-8")
    _git(snapshot, "add", "-A")
    _git(snapshot, "commit", "-q", "-m", "linux oracle snapshot")

    monkeypatch.setattr(check_shapes, "_checkout_git_dir", lambda: checkout / ".git")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("MERGE_GROUP_BASE_SHA", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    # An image whose `git init` defaults to main must not treat that snapshot
    # commit as upstream main.
    _git(snapshot, "branch", "-M", "main")
    assert check_shapes._merge_base(snapshot) == parent
    assert check_shapes.git_added(snapshot) == {"tinyassets/new.py"}


def test_git_added_uses_the_pr_diff_not_untracked_files():
    def git_diff_only(command, **kwargs):
        assert command[:4] == ["git", "diff", "--no-renames", "--name-only"]
        return subprocess.CompletedProcess(command, 0, stdout="tinyassets/new.py\n")

    with (
        patch.object(check_shapes, "_merge_base", return_value="base"),
        patch.object(check_shapes.subprocess, "run", side_effect=git_diff_only),
    ):
        assert check_shapes.git_added() == {"tinyassets/new.py"}


def test_an_instruction_citing_any_deleted_path_goes_red(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "tinyassets/attribution/core.py", "x\n", "code")
    _commit_file(repo, "AGENTS.md", "10. Ship-time attribution: `tinyassets/attribution/`.\n"
                 "Codex reads CODEX.md; deploy with care.\n"
                 "See docs/getting-started for setup. Run scripts/old_tool.py.\n", "a")
    _commit_file(repo, "CODEX.md", "x\n", "codex")
    _commit_file(repo, "docs/getting-started" + "/README.md", "x\n", "folder")
    _commit_file(repo, "scripts/old_tool.py", "x\n", "renamed below")
    _commit_file(repo, "docs/ops/launch.md", "Read [the handoff](gone.md) first.\n", "rel")
    _commit_file(repo, ".agents/skills/s/references/gone.md", "x\n", "ref")
    _commit_file(repo, ".agents/skills/s/SKILL.md", "See [it](references/gone.md).\n", "s")
    _commit_file(repo, ".github/workflows/w.yml",
                 "# deploys tinyassets/attribution/core.py\n"
                 "run: echo tinyassets/attribution\n", "w")
    _commit_file(repo, "tinyassets/other.py", "# tinyassets/attribution/core.py\n", "code2")
    _commit_file(repo, "openspec/changes/archive/old/tasks.md", "docs/ops/gone.md\n", "arch")
    _commit_file(repo, "openspec/changes/the-build/tasks.md", "Run `scripts/old_tool.py`.\n", "b")
    _commit_file(repo, "openspec/changes/old-plan/proposal.md",
                 "# Old plan\n\n**Superseded by ADR-032.**\n", "sup")
    _commit_file(repo, "openspec/changes/old-plan/tasks.md", "Run scripts/old_tool.py.\n", "sup2")
    _commit_file(repo, "deploy/x.sh", "x\n", "a folder named like a prose word")
    # Dated records: an ADR and a decision log may cite a deleted path, but not a deleted doc.
    _commit_file(repo, "docs/decisions/ADR-099-test.md",
                 "Run scripts/old_tool.py; see `tinyassets/attribution/`.\n", "adr")
    _commit_file(repo, "docs/decisions/untangle-log.md",
                 "Run scripts/old_tool.py; see `tinyassets/attribution/` and docs/ops/gone.md.\n",
                 "log")
    base = _commit_file(repo, "docs/ops/gone.md", "x\n", "doc")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    _git(repo, "rm", "-q", "-r", "tinyassets/attribution", ".agents/skills/s/references",
         "docs/ops/gone.md", "CODEX.md", "deploy/x.sh", "docs/getting-started")
    _git(repo, "mv", "scripts/old_tool.py", "scripts/new_tool.py")
    _git(repo, "commit", "-q", "-m", "head")

    assert check_shapes.deleted_doc_references(repo) == [
        "deleted-doc-reference: docs/decisions/untangle-log.md cites docs/ops/gone.md",
        "deleted-path-reference: .agents/skills/s/SKILL.md cites "
        ".agents/skills/s/references/gone.md",
        "deleted-path-reference: .github/workflows/w.yml cites tinyassets/attribution/core.py",
        "deleted-path-reference: AGENTS.md cites CODEX.md",
        "deleted-path-reference: AGENTS.md cites docs/getting-started",
        "deleted-path-reference: AGENTS.md cites scripts/old_tool.py",
        "deleted-path-reference: AGENTS.md cites tinyassets/attribution",
        "deleted-path-reference: docs/ops/launch.md cites docs/ops/gone.md",
        "deleted-path-reference: openspec/changes/the-build/tasks.md cites scripts/old_tool.py",
    ]


def test_a_tracked_ignored_path_missing_from_the_tree_is_not_deleted(
    tmp_path: Path, monkeypatch
):
    """The oracle snapshot drops tracked-but-ignored files; only a commit deletes a path."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, ".gitignore", "output/\n", "ignore")
    (repo / "output").mkdir()
    (repo / "output/log.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-f", "output/log.md")
    _git(repo, "commit", "-q", "-m", "tracked though ignored")
    base = _commit_file(repo, "AGENTS.md", "Append to `output/log.md`.\n", "cite")
    head = _commit_file(repo, "README.md", "x\n", "the branch")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)

    # A local work tree without the file: not committed, so not deleted.
    (repo / "output/log.md").unlink()
    assert check_shapes.deleted_doc_references(repo) == []

    # The oracle's snapshot: a fresh index from the copied tree, committed on the head.
    snapshot = tmp_path / "work"
    snapshot.mkdir()
    for name in (".gitignore", "AGENTS.md", "README.md"):
        (snapshot / name).write_text((repo / name).read_text(encoding="utf-8"), encoding="utf-8")
    _init_repo_at(snapshot)
    info = snapshot / ".git/objects/info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "alternates").write_text(f"{(repo / '.git/objects').as_posix()}\n", encoding="utf-8")
    _git(snapshot, "update-ref", "HEAD", head)
    _git(snapshot, "update-ref", "refs/remotes/origin/main", base)
    _git(snapshot, "add", "-A")
    _git(snapshot, "commit", "-q", "-m", "linux oracle snapshot")
    assert _git(snapshot, "ls-files", "output") == ""
    monkeypatch.setenv("ORACLE_SOURCE_HEAD", head)
    assert check_shapes.deleted_doc_references(snapshot) == []

    # A real deletion on the branch still fails.
    _git(repo, "checkout", "-q", "--", ".")
    _git(repo, "rm", "-q", "output/log.md")
    _git(repo, "commit", "-q", "-m", "delete it")
    monkeypatch.delenv("ORACLE_SOURCE_HEAD")
    assert check_shapes.deleted_doc_references(repo) == [
        "deleted-path-reference: AGENTS.md cites output/log.md"
    ]


def _init_repo_at(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "shape@localhost")
    _git(repo, "config", "user.name", "shape")


def test_the_gate_runs_both_new_checks():
    with (
        patch.object(check_shapes, "adr_superseded_open", return_value=["adr-sentinel"]),
        patch.object(check_shapes, "deleted_doc_references", return_value=["path-sentinel"]),
    ):
        problems = check_shapes.check()
    assert "adr-sentinel" in problems
    assert "path-sentinel" in problems


def _adr(root: Path, status: str, body: str) -> None:
    path = root / "docs/decisions/ADR-099-test.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# ADR-099: Test\n\n## Status\n\n{status}\n\n## Decision\n\nx\n\n{body}",
                    encoding="utf-8")


def _proposal(root: Path, name: str, text: str) -> None:
    path = root / "openspec/changes" / name / "proposal.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_change_an_adr_supersedes_must_be_archived_or_marked(tmp_path: Path):
    _adr(tmp_path, "Accepted. supersedes `openspec/changes/by-status`. Build order:\n"
         "`openspec/changes/the-build/`.",
         "## What this supersedes\n\n- `openspec/changes/by-section` D10.\n"
         "- `bare-name`, by name alone.\n"
         "- `openspec/changes/archived-already`.\n\n## Consequences\n\n"
         "`openspec/changes/later-section`.\n")
    (tmp_path / "openspec/changes/archive/2026-01-01-archived-already").mkdir(parents=True)
    for name in ("by-status", "by-section", "bare-name", "the-build", "later-section"):
        _proposal(tmp_path, name, f"# {name}\n\n## Why\n")
    assert check_shapes.adr_superseded_open(tmp_path) == [
        f"superseded-open: ADR-099 supersedes {name}; archive it or put "
        "'Superseded by ADR-099' in its proposal's first lines"
        for name in ("bare-name", "by-section", "by-status")
    ]
    for name in ("by-status", "by-section", "bare-name"):
        _proposal(tmp_path, name, f"# {name}\n\n**Superseded by ADR-099.**\n\n## Why\n")
    assert check_shapes.adr_superseded_open(tmp_path) == []
    _proposal(tmp_path, "by-status", "# by-status\n\n**Superseded by ADR-098.**\n")
    assert len(check_shapes.adr_superseded_open(tmp_path)) == 1


def test_a_superseded_change_never_seen_fails_and_a_deleted_one_is_gone(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "openspec/changes/deleted-one/proposal.md", "# Deleted one\n", "c")
    _git(repo, "rm", "-q", "-r", "openspec/changes/deleted-one")
    _git(repo, "commit", "-q", "-m", "delete the change, without archiving it")
    _adr(repo, "Accepted.", "## Supersedes\n\n- `openspec/changes/typo-name`.\n"
         "- `openspec/changes/deleted-one`.\n")
    (repo / "openspec/changes").mkdir(parents=True)
    assert check_shapes.adr_superseded_open(repo) == [
        "superseded-unknown: ADR-099 supersedes typo-name, "
        "which no open, archived or deleted change ever had as its name"
    ]


def test_a_proposed_adr_supersedes_nothing_yet(tmp_path: Path):
    _adr(tmp_path, "Proposed.", "## What it supersedes\n\n- `openspec/changes/open-one`.\n")
    _proposal(tmp_path, "open-one", "# open-one\n")
    assert check_shapes.adr_superseded_open(tmp_path) == []


pytestmark = pytest.mark.verifies('shape-convergence:C1')
