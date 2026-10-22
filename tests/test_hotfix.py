"""Hotfix records and ancestry guard, without contacting a host."""

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
    live = {"tinyassets/example.py": b"old"}

    def fake_run(*args, **kwargs):
        if args[:2] == ("docker", "inspect"):
            return ("a" * 40 + "\n").encode()
        return b""

    def fake_docker_file(container, path):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            data = live[path]
            info = tarfile.TarInfo(Path(path).name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    monkeypatch.setattr(hotfix, "run", fake_run)
    monkeypatch.setattr(hotfix, "docker_file", fake_docker_file)
    monkeypatch.setattr(
        hotfix,
        "cp_into",
        lambda source, container, path: live.__setitem__(path, Path(source).read_bytes()),
    )
    monkeypatch.setattr(hotfix, "wait_healthy", lambda container: None)
    monkeypatch.setattr(hotfix, "BOX", tmp_path / "box")
    box_file = hotfix.BOX / "tinyassets/example.py"
    box_file.parent.mkdir(parents=True)
    box_file.write_bytes(b"box original")

    def smoke(*args):
        assert live["tinyassets/example.py"] == b"new"
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
        assert live["tinyassets/example.py"] == b"old"
        assert box_file.read_bytes() == b"box original"
        return
    hotfix.host_apply("tinyassets-daemon", True)
    identifier = capsys.readouterr().out.strip()
    record = json.loads((hotfix.STATE / f"{identifier}.json").read_text())
    assert record["status"] == "active"
    assert record["files"][0]["before_sha256"] == hotfix.digest(b"old")
    assert live["tinyassets/example.py"] == b"new"
    hotfix.host_revert(identifier)
    assert live["tinyassets/example.py"] == b"old"
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


def test_deploy_refusal_and_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(hotfix, "STATE", tmp_path / "hotfixes")
    monkeypatch.setattr(hotfix, "lock", lambda: io.BytesIO())
    record = {
        "id": "20261022T000000Z-12345678",
        "branch": "fix/signin",
        "commit": "b" * 40,
        "files": [],
        "status": "active",
    }
    hotfix.save_record(record)
    monkeypatch.setattr(
        hotfix, "remote", lambda *args, **kwargs: json.dumps(hotfix.records()).encode()
    )

    def fake_git(*args):
        if args[0] == "merge-base":
            raise hotfix.subprocess.CalledProcessError(1, args)
        return ""

    monkeypatch.setattr(hotfix, "git", fake_git)
    with pytest.raises(ValueError, match="does not contain active hotfix"):
        hotfix.guard_revision("c" * 40)
    monkeypatch.setattr(hotfix, "git", lambda *args: "")
    hotfix.guard_revision("c" * 40)
    monkeypatch.setattr(hotfix, "run", lambda *a, **kw: ("c" * 40).encode())
    hotfix.host_archive(["b" * 40], "c" * 40)
    assert json.loads((hotfix.STATE / (record["id"] + ".json")).read_text())["status"] == "archived"


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


@pytest.mark.parametrize(
    "status,expected,allowed",
    [
        ("active", "b" * 40, True),
        ("active", "", False),
        ("pending", "b" * 40, False),
    ],
)
def test_deploy_rechecks_records_under_lock(tmp_path, status, expected, allowed):
    shell = (SCRIPT.parents[1] / "deploy/deploy_fail_safe.sh").read_text(encoding="utf-8")
    block = shell.split("<<'HOTFIX_GUARD'\n", 1)[1].split("\nHOTFIX_GUARD", 1)[0]
    (tmp_path / "one.json").write_text(
        json.dumps({"id": "one", "status": status, "commit": "b" * 40})
    )
    proc = hotfix.subprocess.run(
        [hotfix.sys.executable, "-c", block, str(tmp_path), expected], capture_output=True
    )
    assert (proc.returncode == 0) is allowed
    assert '"$candidate_revision" != "${HOTFIX_IMAGE_REVISION:-}"' in shell


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
    monkeypatch.setattr(hotfix, "run", lambda *a: b"old" if a[-1].startswith("a" * 40) else b"new")
    payload = hotfix.branch_payload("fix/x")
    assert events == [("b" * 40, ["tinyassets/example.py", "tests/test_example.py"])]
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        record = json.load(archive.extractfile("manifest.json"))
        assert record["commit"] == "b" * 40
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


@pytest.mark.parametrize("corrupt", [False, True])
def test_deploy_rollback_restores_hotfix_bytes(tmp_path, monkeypatch, corrupt):
    shell = (SCRIPT.parents[1] / "deploy/deploy_fail_safe.sh").read_text(encoding="utf-8")
    block = shell.split("<<'HOTFIX_RESTORE'\n", 1)[1].split("\nHOTFIX_RESTORE", 1)[0]
    original = tmp_path / "one/after/tinyassets/example.py"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"bad" if corrupt else b"patched")
    (tmp_path / "one.json").write_text(
        json.dumps(
            {
                "id": "one",
                "status": "active",
                "files": [
                    {"path": "tinyassets/example.py", "after_sha256": hotfix.digest(b"patched")}
                ],
            }
        )
    )
    calls = []
    monkeypatch.setattr(hotfix.sys, "argv", ["-", str(tmp_path), "tinyassets-daemon"])
    monkeypatch.setattr(hotfix.subprocess, "run", lambda args, **kw: calls.append(args))
    if corrupt:
        with pytest.raises(SystemExit, match="corrupt"):
            exec(compile(block, "<rollback>", "exec"), {})
        assert not calls
    else:
        exec(compile(block, "<rollback>", "exec"), {})
        assert calls == [
            ["docker", "cp", str(original), "tinyassets-daemon:/app/tinyassets/example.py"],
            ["docker", "restart", "tinyassets-daemon"],
        ]
