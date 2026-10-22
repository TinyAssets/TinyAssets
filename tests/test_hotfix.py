"""Hotfix records and content guard, without contacting a host."""

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/hotfix.py"
spec = importlib.util.spec_from_file_location("hotfix", SCRIPT)
hotfix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hotfix)


def packed(before, after):
    path = "tinyassets/example.py"
    record = {
        "pr": 12,
        "branch": "fix/signin",
        "commit": "b" * 40,
        "deployed_commit": "a" * 40,
        "operator": "test",
        "time": "now",
        "files": [
            {
                "path": path,
                "before_sha256": hotfix.digest(before),
                "after_sha256": hotfix.digest(after),
            }
        ],
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in [(path, after), ("manifest.json", json.dumps(record).encode())]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.mark.parametrize("red_smoke", [False, True])
def test_record_and_revert(tmp_path, monkeypatch, capsys, red_smoke):
    monkeypatch.setattr(hotfix, "STATE", tmp_path / "hotfixes")
    monkeypatch.setattr(hotfix, "lock", lambda: io.BytesIO())
    monkeypatch.setattr(hotfix.sys, "stdin", io.TextIOWrapper(io.BytesIO(packed(b"old", b"new"))))
    live = {
        (c, "tinyassets/example.py"): b"old" for c in ("tinyassets-daemon", "tinyassets-broker")
    }

    def fake_run(*args, **kwargs):
        if args[:2] == ("docker", "ps"):
            return (
                b"tinyassets-daemon\ttinyassets:live\n"
                b"tinyassets-broker\tghcr.io/org/tinyassets:live\n"
            )
        if args[:2] == ("docker", "inspect"):
            return ("a" * 40 + "\n").encode()
        return b""

    def fake_docker_file(container, path):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            data = live[container, path]
            info = tarfile.TarInfo(Path(path).name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    monkeypatch.setattr(hotfix, "run", fake_run)
    monkeypatch.setattr(hotfix, "docker_file", fake_docker_file)
    monkeypatch.setattr(
        hotfix,
        "cp_into",
        lambda source, container, path: live.__setitem__(
            (container, path), Path(source).read_bytes()
        ),
    )
    monkeypatch.setattr(hotfix, "wait_healthy", lambda container: None)
    monkeypatch.setattr(hotfix, "BOX", tmp_path / "box")
    box_file = hotfix.BOX / "tinyassets/example.py"
    box_file.parent.mkdir(parents=True)
    box_file.write_bytes(b"box original")

    def smoke(*args):
        assert set(live.values()) == {b"new"}
        assert box_file.read_bytes() == b"new"
        if red_smoke:
            raise RuntimeError("smoke /app: expected 200, got 500")
        return {"passed": True}

    monkeypatch.setattr(hotfix, "smoke_checks", smoke)
    if red_smoke:
        with pytest.raises(RuntimeError, match="smoke /app"):
            hotfix.host_apply("tinyassets-daemon", True)
        record = hotfix.records()[0]
        assert record["status"] == "reverted"
        assert record["smoke"]["passed"] is False
        assert set(live.values()) == {b"old"}
        assert box_file.read_bytes() == b"box original"
        return
    hotfix.host_apply("tinyassets-daemon", True)
    identifier = capsys.readouterr().out.strip()
    record = json.loads((hotfix.STATE / f"{identifier}.json").read_text())
    assert record["status"] == "active"
    assert record["files"][0]["before_sha256"] == hotfix.digest(b"old")
    assert set(live.values()) == {b"new"}
    hotfix.host_revert(identifier)
    assert set(live.values()) == {b"old"}
    assert box_file.read_bytes() == b"box original"
    assert json.loads((hotfix.STATE / f"{identifier}.json").read_text())["status"] == "reverted"


def test_refuses_file_outside_branch_diff(monkeypatch):
    monkeypatch.setattr(hotfix, "remote", lambda *args, **kwargs: ("a" * 40).encode())
    monkeypatch.setattr(
        hotfix,
        "git",
        lambda *args: (
            "b" * 40 + "\trefs/heads/fix/signin"
            if args[0] == "ls-remote"
            else "tinyassets/other.py"
            if args[0] == "diff"
            else ""
        ),
    )
    with pytest.raises(ValueError, match="not modified"):
        hotfix.branch_payload("fix/signin", ["tinyassets/example.py"])


@pytest.mark.parametrize(
    "path",
    [
        "migrations/001.py",
        "tinyassets/box_layout_migration.py",
        "tinyassets/storage/new_shape.py",
        "tinyassets/outcomes/schema.py",
        "pyproject.toml",
        "uv.lock",
        "poetry.lock",
        "Dockerfile",
        "deploy/compose.yml",
        "deploy/daemon.service",
        "deploy/boxhostd.py",
        "tinyassets/gvisor/runner.py",
        "scripts/watchdog.py",
    ],
)
def test_ineligible_paths(path):
    assert not hotfix.eligible(path)


@pytest.mark.parametrize(
    "path",
    [
        "tinyassets/auth/provider.py",
        "tinyassets/onboarding/app.html",
        "tinyassets/onboarding/app/main.js",
        "tinyassets/example.py",
    ],
)
def test_eligible_paths(path):
    assert hotfix.eligible(path)


@pytest.mark.parametrize("approved,head_matches", [(False, True), (True, False), (True, True)])
def test_floor_receipt_bound_to_exact_head(monkeypatch, approved, head_matches):
    head = "b" * 40
    url = "https://github.com/org/repo/pull/12#issuecomment-123"
    receipt = (
        f"Drain-Review-Verdict: {'APPROVE' if approved else 'BLOCK'}\n"
        f"Drain-Review-Head: {head if head_matches else 'c' * 40}\n"
        f"Drain-Review-Artifact: {url}"
    )

    def fake_run(*args):
        if args[1:3] == ("pr", "view"):
            return json.dumps({"number": 12, "body": receipt, "headRefOid": head}).encode()
        if args[1:3] == ("repo", "view"):
            return b'{"nameWithOwner":"org/repo"}'
        return json.dumps({"url": url, "association": "OWNER", "body": receipt}).encode()

    monkeypatch.setattr(hotfix, "run", fake_run)
    if approved and head_matches:
        hotfix.require_review("fix/signin", head, ["tinyassets/auth/provider.py"])
    else:
        with pytest.raises(ValueError, match="APPROVE receipt"):
            hotfix.require_review("fix/signin", head, ["tinyassets/auth/provider.py"])


def test_selected_file_cannot_hide_dependency_change(monkeypatch):
    monkeypatch.setattr(hotfix, "remote", lambda *a, **kw: ("a" * 40).encode())
    monkeypatch.setattr(
        hotfix,
        "git",
        lambda *a: (
            "b" * 40 + "\trefs/heads/fix/x"
            if a[0] == "ls-remote"
            else "tinyassets/example.py\npyproject.toml"
            if a[0] == "diff"
            else ""
        ),
    )
    with pytest.raises(ValueError, match="normal deploy"):
        hotfix.branch_payload("fix/x", ["tinyassets/example.py"])


def test_failed_tests_prevent_payload(monkeypatch):
    monkeypatch.setattr(hotfix, "require_review", lambda *a: None)
    monkeypatch.setattr(hotfix, "remote", lambda *a, **kw: ("a" * 40).encode())
    monkeypatch.setattr(
        hotfix,
        "git",
        lambda *a: (
            "b" * 40 + "\trefs/heads/fix/x"
            if a[0] == "ls-remote"
            else "tinyassets/example.py"
            if a[0] == "diff"
            else "100644 blob deadbeef\ttinyassets/example.py"
            if a[0] == "ls-tree"
            else ""
        ),
    )

    def fail(*args):
        raise RuntimeError("tests failed")

    monkeypatch.setattr(hotfix, "targeted_tests", fail)
    with pytest.raises(RuntimeError, match="tests failed"):
        hotfix.branch_payload("fix/x")


def test_archive_refuses_wrong_running_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(hotfix, "STATE", tmp_path)
    monkeypatch.setattr(hotfix, "lock", lambda: io.BytesIO())
    monkeypatch.setattr(hotfix, "run", lambda *a, **kw: ("a" * 40).encode())
    with pytest.raises(ValueError, match="deployed image changed"):
        hotfix.host_archive([], "b" * 40)


def test_remote_shell_quotes_bootstrap(monkeypatch):
    calls = []
    monkeypatch.setattr(hotfix, "run", lambda *a, **kw: calls.append((a, kw)) or b"")
    monkeypatch.setattr(hotfix, "ssh_target", lambda: "operator@example.invalid")
    hotfix.remote("list")
    import shlex

    command = shlex.split(calls[0][0][-1])
    assert command[:3] == ["sudo", "python3", "-c"]
    assert "__file__=" in command[3]
    assert command[4:] == ["--host", "list"]


@pytest.mark.parametrize("bad_path", ["/app", "/mcp", "/mcp/pulse", None])
def test_smoke_http_contract(monkeypatch, bad_path):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Opener:
        def open(self, req, timeout):
            path = req.full_url.removeprefix("https://tinyassets.io")
            seen.append(path)
            assert bool(req.get_header("Authorization")) == (path == "/mcp/pulse")
            result = Response()
            result.status = 500 if path == bad_path else 401 if path == "/mcp" else 200
            return result

    monkeypatch.setattr(hotfix.urllib.request, "build_opener", lambda *a: Opener())
    if bad_path:
        with pytest.raises(RuntimeError, match="smoke"):
            hotfix.smoke_checks({"TINYASSETS_WIKI_CANARY_TOKEN": "fake"}, {})
    else:
        assert hotfix.smoke_checks({"TINYASSETS_WIKI_CANARY_TOKEN": "fake"}, {})["passed"]
        assert seen == ["/app", "/mcp", "/mcp/pulse"]


def test_signin_probe_failure_is_red(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class Opener:
        def open(self, req, timeout):
            result = Response()
            result.status = 401 if req.full_url.endswith("/mcp") else 200
            return result

    monkeypatch.setattr(hotfix.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(RuntimeError, match="sign-in probe failed"):
        hotfix.smoke_checks(
            {"TINYASSETS_WIKI_CANARY_TOKEN": "fake"},
            {"live_signin_probe.py": "raise SystemExit(1)"},
        )


def test_auto_payload_uses_pushed_bytes_after_tests(monkeypatch):
    monkeypatch.setattr(hotfix, "require_review", lambda *a: None)
    events = []
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", "fake")
    monkeypatch.setattr(hotfix, "remote", lambda *a, **kw: ("a" * 40).encode())

    def git(*args):
        if args[0] == "ls-remote":
            return "b" * 40 + "\trefs/heads/fix/x"
        if args[0] == "diff":
            return "tinyassets/example.py\ntests/test_example.py" if "--name-only" in args else ""
        if args[0] == "ls-tree":
            return "100644 blob deadbeef\ttinyassets/example.py"
        return ""

    monkeypatch.setattr(hotfix, "git", git)
    monkeypatch.setattr(hotfix, "targeted_tests", lambda *a: events.append(a))
    monkeypatch.setattr(
        hotfix,
        "run",
        lambda *a: (
            json.dumps({"number": 12, "headRefOid": "b" * 40}).encode()
            if a[0] == "gh"
            else b"old"
            if a[-1].startswith("a" * 40)
            else b"new"
        ),
    )
    payload = hotfix.branch_payload("fix/x")
    assert events == [("b" * 40, ["tinyassets/example.py", "tests/test_example.py"])]
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        record = json.load(archive.extractfile("manifest.json"))
        assert record["commit"] == "b" * 40
        assert record["pr"] == 12
        assert archive.extractfile("tinyassets/example.py").read() == b"new"
        assert len(record["files"]) == 1


def test_embedded_sql_shape_change_refused(monkeypatch):
    monkeypatch.setattr(hotfix, "remote", lambda *a, **kw: ("a" * 40).encode())
    monkeypatch.setattr(
        hotfix,
        "git",
        lambda *a: (
            "b" * 40 + "\trefs/heads/fix/x"
            if a[0] == "ls-remote"
            else "tinyassets/example.py"
            if "--name-only" in a
            else '+db.execute("ALTER TABLE users ADD COLUMN x TEXT")'
            if a[0] == "diff"
            else ""
        ),
    )
    with pytest.raises(ValueError, match="storage-shape SQL"):
        hotfix.branch_payload("fix/x")


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(hotfix, "STATE", tmp_path)
    monkeypatch.setattr(hotfix, "lock", lambda: io.BytesIO())
    record = {
        "id": "hf-5206-first-admission",
        "commit": "b" * 40,
        "result": "passed",
        "containers": ["tinyassets-daemon", "tinyassets-broker"],
        "files": {"tinyassets/example.py": hotfix.digest(b"patched")},
        "applied_at": "2026-10-21",
        "active": True,
    }
    hotfix.save_record(record)
    original = tmp_path / (record["id"] + ".orig") / "tinyassets/example.py"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    return record


def test_legacy_list_revert_and_recovery(legacy, monkeypatch):
    calls = []
    monkeypatch.setattr(hotfix, "file_bytes", lambda *a: (b"patched", 0o644))
    monkeypatch.setattr(
        hotfix, "cp_into", lambda source, c, p: calls.append((c, source.read_bytes()))
    )
    monkeypatch.setattr(hotfix, "restart_targets", lambda *a: None)
    record = hotfix.records()[0]
    assert record["status"] == "active"
    hotfix.prepare_recovery(record)
    hotfix.host_restore()
    assert calls == [(c, b"patched") for c in legacy["containers"]]
    calls.clear()
    hotfix.host_revert(legacy["id"])
    assert calls == [(c, b"original") for c in legacy["containers"]]
    assert hotfix.records()[0]["status"] == "reverted"


@pytest.mark.parametrize("damage", ["json", "status", "files", "containers", "sha", "id"])
def test_unreadable_record_refuses_with_fix(legacy, damage):
    path = hotfix.STATE / (legacy["id"] + ".json")
    if damage == "json":
        path.write_text("{")
    else:
        if damage == "status":
            legacy.pop("active")
        elif damage == "sha":
            legacy["files"]["tinyassets/example.py"] = "not a sha"
        else:
            legacy[damage] = None
        path.write_text(json.dumps(legacy))
    with pytest.raises(ValueError, match="Fix .*retain id"):
        hotfix.records()


def proof_for(record, merge=None):
    return hotfix.base64.b64encode(
        json.dumps(
            {
                "revision": "c" * 40,
                "records": {record["id"]: {"key": hotfix.record_key(record), "merge": merge}},
            }
        ).encode()
    ).decode()


@pytest.mark.parametrize(
    "content,merged,allowed",
    [(b"patched", None, True), (b"newer", "d" * 40, True), (b"old", None, False)],
)
def test_candidate_content_or_squash_merge_and_archive(
    legacy, monkeypatch, content, merged, allowed
):
    record = hotfix.records()[0]
    proof = proof_for(record, merged)
    calls = []

    def run(*args, **kw):
        calls.append(args)
        return b"candidate" if args[:2] == ("docker", "create") else ("c" * 40).encode()

    monkeypatch.setattr(hotfix, "run", run)
    monkeypatch.setattr(
        hotfix, "file_bytes", lambda c, p: (content if c == "candidate" else b"patched", 0o644)
    )
    if not allowed:
        with pytest.raises(ValueError, match="candidate lacks hotfix"):
            hotfix.host_guard("image", proof)
        assert hotfix.records()[0]["status"] == "active"
    else:
        hotfix.host_guard("image", proof)
        assert (
            hotfix.STATE / record["id"] / "after/tinyassets/example.py"
        ).read_bytes() == b"patched"
        hotfix.host_archive(proof, "c" * 40)
        assert hotfix.records()[0]["status"] == "archived"
    assert ("docker", "rm", "candidate") in calls


@pytest.mark.parametrize("change", ["pending", "files", "revision"])
def test_host_guard_rechecks_proof_under_lock(legacy, monkeypatch, change):
    proof = proof_for(hotfix.records()[0])
    if change == "pending":
        legacy["status"] = "pending"
    elif change == "files":
        legacy["files"]["tinyassets/example.py"] = hotfix.digest(b"other")
    hotfix.save_record(legacy)
    monkeypatch.setattr(hotfix, "run", lambda *a: ("a" * 40).encode())
    with pytest.raises(ValueError, match="active hotfix set|revision differs"):
        hotfix.host_guard("image", proof)


@pytest.mark.parametrize(
    "state,base,ancestor,expected",
    [
        ("MERGED", "main", True, "d" * 40),
        ("OPEN", "main", True, None),
        ("MERGED", "other", True, None),
        ("MERGED", "main", False, None),
    ],
)
def test_runner_proves_pr_merge_not_branch_ancestry(
    legacy, monkeypatch, state, base, ancestor, expected
):
    legacy["pr"] = 5206
    hotfix.save_record(legacy)
    monkeypatch.setattr(hotfix, "remote", lambda *a: json.dumps(hotfix.records()).encode())
    monkeypatch.setattr(
        hotfix,
        "run",
        lambda *a: (
            b"patched"
            if a[0] == "git"
            else json.dumps(
                {"state": state, "baseRefName": base, "mergeCommit": {"oid": "d" * 40}}
            ).encode()
        ),
    )

    def git(*args):
        assert args == ("merge-base", "--is-ancestor", "d" * 40, "c" * 40)
        if not ancestor:
            raise hotfix.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(hotfix, "git", git)
    proof = json.loads(hotfix.base64.b64decode(hotfix.guard_revision("c" * 40)))
    assert proof["records"][legacy["id"]]["merge"] == expected


def test_merged_pr_without_hotfix_bytes_cannot_archive(legacy, monkeypatch):
    legacy["pr"] = 5206
    legacy["files"]["tinyassets/second.py"] = hotfix.digest(b"second fix")
    hotfix.save_record(legacy)
    monkeypatch.setattr(hotfix, "remote", lambda *a: json.dumps(hotfix.records()).encode())

    def run(*args):
        if args[0] == "gh":
            return json.dumps(
                {"state": "MERGED", "baseRefName": "main", "mergeCommit": {"oid": "d" * 40}}
            ).encode()
        if args[0] == "git":
            return b"patched" if args[-1].endswith(":tinyassets/example.py") else b"wrong"
        if args[:2] == ("docker", "create"):
            return b"candidate"
        if args[-1] == "tinyassets-daemon" and args[:2] == ("docker", "inspect"):
            return ("c" * 40).encode()
        return ("c" * 40).encode()

    monkeypatch.setattr(hotfix, "run", run)
    monkeypatch.setattr(
        hotfix, "git", lambda *a: pytest.fail("unverified merge reached ancestor check")
    )
    monkeypatch.setattr(hotfix, "file_bytes", lambda *a: (b"missing fix", 0o644))
    proof = hotfix.guard_revision("c" * 40)
    record = hotfix.records()[0]
    assert json.loads(hotfix.base64.b64decode(proof))["records"][record["id"]]["merge"] is None
    with pytest.raises(ValueError, match="candidate lacks hotfix"):
        hotfix.host_archive(proof, "c" * 40)
    assert hotfix.records()[0]["status"] == "active"


@pytest.mark.parametrize(
    "path",
    [
        "tinyassets/account_deletion.py",
        "tinyassets/action_policy.py",
        "tinyassets/agent_loop/box_tools.py",
        "tinyassets/agent_loop/box_ta.py",
        "tinyassets/agent_loop/tool_session.py",
        "tinyassets/example.py",
    ],
)
def test_canonical_gate_requires_receipt_without_private_path_exemptions(monkeypatch, path):
    monkeypatch.setattr(
        hotfix,
        "run",
        lambda *a: b'{"number":1,"body":"","headRefOid":"wrong","nameWithOwner":"org/repo"}',
    )
    with pytest.raises(ValueError, match="APPROVE receipt"):
        hotfix.require_review("fix", "b" * 40, [path])


def test_runtime_restart_reaches_boxes_then_both_services(monkeypatch):
    calls = []
    monkeypatch.setattr(hotfix, "run", lambda *a: calls.append(a))
    monkeypatch.setattr(hotfix, "wait_healthy", lambda c: calls.append(("healthy", c)))
    hotfix.restart_targets(["tinyassets-broker", "tinyassets-daemon"], True)
    assert calls == [
        ("systemctl", "restart", "boxhostd.service"),
        ("systemctl", "is-active", "--quiet", "boxhostd.service"),
        ("docker", "restart", "tinyassets-broker"),
        ("healthy", "tinyassets-broker"),
        ("docker", "restart", "tinyassets-daemon"),
        ("healthy", "tinyassets-daemon"),
    ]


def test_container_discovery_and_missing_file(monkeypatch):
    monkeypatch.setattr(
        hotfix,
        "run",
        lambda *a: (
            b"tinyassets-daemon\ttinyassets:1\ntinyassets-broker\tghcr.io/tinyassets:1\nlogs\tvector:1\n"
        ),
    )
    assert hotfix.running_containers() == ["tinyassets-broker", "tinyassets-daemon"]

    def absent(*args):
        raise hotfix.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(hotfix, "run", absent)
    assert not hotfix.container_has_file("broker", "tinyassets/example.py")


def test_restore_corrupt_payload_refuses_before_any_write(legacy, monkeypatch):
    calls = []
    monkeypatch.setattr(hotfix, "cp_into", lambda *a: calls.append(a))
    with pytest.raises(ValueError, match="saved hotfix bytes corrupt: restore"):
        hotfix.host_restore()
    assert not calls


def test_deploy_uses_shared_reader_for_guard_and_rollback():
    shell = (SCRIPT.parents[1] / "deploy/deploy_fail_safe.sh").read_text()
    workflow = (SCRIPT.parents[1] / ".github/workflows/deploy-prod.yml").read_text()
    assert '--host guard-image "$NEW_IMAGE" "${HOTFIX_PROOF:-}"' in shell
    assert "--host restore" in shell
    assert "scripts/hotfix.py" in workflow


@pytest.mark.parametrize("identifier", ["hf-5206-first-admission", "hf-5216-desktop-open"])
def test_both_host_record_ids_and_flat_originals(legacy, identifier, monkeypatch):
    old_id = legacy["id"]
    (hotfix.STATE / (old_id + ".json")).unlink()
    legacy["id"] = identifier
    hotfix.save_record(legacy)
    original = hotfix.STATE / (identifier + ".orig") / "example.py"
    original.parent.mkdir(exist_ok=True)
    original.write_bytes(b"original")
    record = hotfix.records()[0]
    assert record["pr"] == int(identifier.split("-")[1])
    assert (
        hotfix.original_path(record, record["files"][0], "tinyassets-daemon").read_bytes()
        == b"original"
    )


def test_api_failure_still_allows_content_proof(legacy, monkeypatch):
    monkeypatch.setattr(hotfix, "remote", lambda *a: json.dumps(hotfix.records()).encode())

    def unavailable(*args):
        raise hotfix.subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(hotfix, "run", unavailable)
    proof = json.loads(hotfix.base64.b64decode(hotfix.guard_revision("c" * 40)))
    assert proof["records"][legacy["id"]]["merge"] is None


def test_guard_checks_every_hotfixed_file(legacy, monkeypatch):
    legacy["files"]["tinyassets/second.py"] = hotfix.digest(b"second")
    hotfix.save_record(legacy)
    proof = proof_for(hotfix.records()[0])
    monkeypatch.setattr(
        hotfix,
        "run",
        lambda *a: b"candidate" if a[:2] == ("docker", "create") else ("c" * 40).encode(),
    )
    monkeypatch.setattr(hotfix, "file_bytes", lambda *a: (b"patched", 0o644))
    with pytest.raises(ValueError, match="second.py"):
        hotfix.host_guard("image", proof)


def test_missing_legacy_backup_gives_exact_repair(legacy):
    record = hotfix.records()[0]
    (hotfix.STATE / (record["id"] + ".orig") / "tinyassets/example.py").unlink()
    with pytest.raises(ValueError, match=r"missing original: restore .*example.py.*backup"):
        hotfix.original_path(record, record["files"][0], "tinyassets-daemon")


def test_prepare_recovery_tries_other_recorded_container(legacy, monkeypatch):
    def contents(container, path):
        if container == "tinyassets-daemon":
            raise hotfix.subprocess.CalledProcessError(1, ["docker", "cp"])
        return b"patched", 0o644

    monkeypatch.setattr(hotfix, "file_bytes", contents)
    hotfix.prepare_recovery(hotfix.records()[0])
    assert (hotfix.STATE / legacy["id"] / "after/tinyassets/example.py").read_bytes() == b"patched"


def test_health_supports_broker_without_healthcheck(monkeypatch):
    monkeypatch.setattr(hotfix, "run", lambda *a: b"running")
    hotfix.wait_healthy("tinyassets-broker")


def test_apply_ignores_container_without_file_and_absent_box_harness(tmp_path, monkeypatch):
    monkeypatch.setattr(hotfix, "STATE", tmp_path / "state")
    monkeypatch.setattr(hotfix, "BOX", tmp_path / "no-harness")
    monkeypatch.setattr(hotfix, "lock", lambda: io.BytesIO())
    monkeypatch.setattr(hotfix.sys, "stdin", io.TextIOWrapper(io.BytesIO(packed(b"old", b"new"))))
    monkeypatch.setattr(hotfix, "running_containers", lambda: ["daemon", "broker"])
    monkeypatch.setattr(hotfix, "container_has_file", lambda c, p: c == "daemon")
    monkeypatch.setattr(hotfix, "file_bytes", lambda *a: (b"old", 0o644))
    calls = []
    monkeypatch.setattr(hotfix, "cp_into", lambda source, c, p: calls.append(c))
    monkeypatch.setattr(hotfix, "restart_targets", lambda cs, boxes: calls.append((cs, boxes)))
    monkeypatch.setattr(hotfix, "smoke_checks", lambda *a: {"passed": True})
    hotfix.host_apply()
    assert calls == ["daemon", (["daemon"], False)]
    assert hotfix.records()[0]["files"][0]["containers"] == ["daemon"]
