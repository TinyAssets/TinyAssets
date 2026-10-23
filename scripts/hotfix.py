#!/usr/bin/env python3
"""Ship a pushed fix, preserving originals and reverting on red live smoke.

apply --branch <pushed-branch> [--files auto | path ...], revert <id>, list.
Requires DO_DROPLET_HOST, DO_SSH_USER, DO_SSH_KEY_PATH (default ~/.ssh/do_deploy),
and TINYASSETS_WIKI_CANARY_TOKEN. Floor changes also require gh and an approved PR.
New/deleted files, incompatible diffs and unbounded test selections deploy normally.
Deploys verify candidate bytes or the hotfix bytes in an included merged PR.
"""

import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATE = Path(os.environ.get("HOTFIX_STATE_DIR", "/var/lib/tinyassets-deploy")) / "hotfixes"
LOCK = Path(os.environ.get("HOTFIX_LOCK_FILE", "/var/lock/tinyassets-host-mutation.lock"))
BOX = Path(os.environ.get("HOTFIX_BOX_ROOT", "/opt/tinyassets/boxhostd/harness-current/app"))


def eligible(path):
    """Positive runtime allowlist; storage/host paths always deploy normally."""
    valid_file(path)
    lower = path.lower()
    denied = ("migration", "schema", "boxhostd", "gvisor")
    if any(word in lower for word in denied) or lower.startswith("tinyassets/storage/"):
        return False
    if PurePosixPath(path).name in {"models.py", "db.py", "database.py"}:
        return False
    return (path.startswith("tinyassets/") and path.endswith(".py")) or (
        path.startswith("tinyassets/onboarding/")
        and PurePosixPath(path).suffix in {".html", ".js", ".css", ".json", ".svg", ".png", ".ico"}
    )


def require_review(branch, commit, paths):
    # pr-scope-guard and drain_review_gate require a receipt for EVERY PR.
    # There is no longer a selective floor path list; use that canonical gate.
    from scripts.drain_review_gate import published_approval_urls, review_allows_merge

    pr = json.loads(run("gh", "pr", "view", branch, "--json", "number,body,headRefOid"))
    repo = json.loads(run("gh", "repo", "view", "--json", "nameWithOwner"))["nameWithOwner"]
    stream = []
    for endpoint in (
        f"issues/{pr['number']}/comments",
        f"pulls/{pr['number']}/reviews",
        f"pulls/{pr['number']}/comments",
    ):
        stream.append(
            run(
                "gh",
                "api",
                "--paginate",
                f"repos/{repo}/{endpoint}",
                "--jq",
                '.[] | {url: .html_url, association: .author_association, body: (.body // "")}',
            ).decode()
        )
    trusted = published_approval_urls("\n".join(stream), head=commit)
    if pr["headRefOid"] != commit or not review_allows_merge(
        head=commit,
        body=pr["body"],
        artifact_must_be_comment_on=(repo, pr["number"]),
        trusted_comment_urls=trusted,
    ):
        raise ValueError(
            "floor-class hotfix requires a Drain-Review APPROVE receipt for branch head"
        )


def targeted_tests(commit, changed):
    """Test the pushed tree, never dirty working-copy bytes; no full-suite fallback."""
    with tempfile.TemporaryDirectory(prefix="tinyassets-hotfix-tests-") as temp:
        checkout = Path(temp) / "tree"
        git("worktree", "add", "--detach", str(checkout), commit)
        try:
            selection = checkout / ".hotfix-affected.txt"
            report_path = Path(temp) / "junit.xml"
            subprocess.run(
                [
                    sys.executable,
                    "scripts/ci_required_tests.py",
                    "--ci-select",
                    "pr",
                    "--changed",
                    *changed,
                    "--out",
                    str(selection),
                ],
                cwd=checkout,
                check=True,
            )
            entries = selection.read_text().strip()
            if not entries or entries == "ALL":
                raise ValueError("no bounded affected selection; use the normal deploy")
            runner = [sys.executable, "scripts/ci_required_tests.py"]
            junit = str(report_path)
            if os.name == "nt":
                runner = [
                    sys.executable,
                    "scripts/linux_oracle.py",
                    "--required-runner",
                    "--out",
                    temp,
                    "--",
                ]
                junit = "/out/junit.xml"
            subprocess.run(
                runner + ["--affected", selection.name, "--profile", "affected", "--junit", junit],
                cwd=checkout,
                check=True,
            )
            report = ET.parse(report_path)
            if report.findall(".//failure") or report.findall(".//error"):
                raise ValueError("targeted tests failed (including quarantined failures)")
            if not any(case.find("skipped") is None for case in report.findall(".//testcase")):
                raise ValueError("targeted selection ran no tests; use the normal deploy")
            probe = checkout / "scripts/live_signin_probe.py"
            return (
                {p.name: p.read_text(encoding="utf-8") for p in probe.parent.glob("*.py")}
                if probe.exists()
                else {}
            )
        finally:
            git("worktree", "remove", "--force", str(checkout))


def run(*args, input=None):
    return subprocess.run(
        args, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True
    ).stdout


def git(*args):
    return run("git", "-C", str(ROOT), *args).decode().strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def valid_file(value):
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or str(path) != value
        or not re.fullmatch(r"[A-Za-z0-9_./-]+", value)
    ):
        raise ValueError(f"invalid repo path: {value}")
    return value


def ssh_target():
    host = os.environ.get("DO_DROPLET_HOST")
    user = os.environ.get("DO_SSH_USER")
    if not host or not user:
        raise ValueError("set DO_DROPLET_HOST and DO_SSH_USER")
    return f"{user}@{host}"


def ssh(*command, input=None):
    key = os.environ.get("DO_SSH_KEY_PATH", str(Path.home() / ".ssh" / "do_deploy"))
    return run(
        "ssh", "-i", key, "-o", "BatchMode=yes", ssh_target(), shlex.join(command), input=input
    )


def remote(*args, input=None):
    # The checked-out script is streamed on every invocation, so no old helper
    # on the host can silently implement a different record format.
    script = Path(__file__).read_bytes()
    payload = len(script).to_bytes(8, "big") + script + (input or b"")
    return ssh(
        "sudo",
        "python3",
        "-c",
        "import sys; __file__='/tmp/hotfix.py'; "
        "n=int.from_bytes(sys.stdin.buffer.read(8),'big'); "
        "exec(compile(sys.stdin.buffer.read(n), __file__, 'exec'))",
        "--host",
        *args,
        input=payload,
    )


def branch_payload(branch, paths=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch) or branch.startswith("-"):
        raise ValueError("invalid branch")
    pushed = git("ls-remote", "--heads", "origin", branch).split()
    if len(pushed) != 2 or pushed[1] != f"refs/heads/{branch}":
        raise ValueError(f"branch is not pushed: {branch}")
    commit = pushed[0]
    git("cat-file", "-e", f"{commit}^{{commit}}")
    base = remote("revision").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError(f"invalid deployed image revision: {base}")
    git("cat-file", "-e", f"{base}^{{commit}}")
    if not git("merge-base", "--is-ancestor", base, commit) == "":
        raise ValueError("deployed revision is not an ancestor of pushed branch")
    changed = git("diff", "--name-only", "--no-renames", base, commit, "--").splitlines()
    # Tests and documentation accompany the fix, but are not installed. Everything
    # else must be compatible, even when the operator selects only one file.
    runtime = [
        p
        for p in changed
        if not (p.startswith(("tests/", "docs/", "openspec/")) or p in {"AGENTS.md", "README.md"})
    ]
    refused = [p for p in runtime if not eligible(p)]
    if refused:
        raise ValueError(f"not hot-swappable; use the normal deploy: {refused}")
    patch = git("diff", "--unified=0", base, commit, "--", *runtime) if runtime else ""
    if any(
        re.search(r"\b(?:CREATE|ALTER|DROP)\s+(?:TABLE|INDEX|VIEW|TRIGGER)\b", line, re.I)
        for line in patch.splitlines()
        if line.startswith(("+", "-"))
    ):
        raise ValueError("storage-shape SQL changed; use the normal deploy")
    paths = runtime if paths is None or paths == ["auto"] else paths
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("name one or more distinct hot-swappable files")
    if not set(paths) <= set(runtime):
        raise ValueError(
            f"file not modified by pushed branch relative to deployed {base}: "
            f"{sorted(set(paths) - set(runtime))}"
        )
    for path in runtime:
        for ref in (base, commit):
            mode = git("ls-tree", ref, "--", path).split()
            if not mode or mode[0] not in {"100644", "100755"}:
                raise ValueError(
                    f"only existing regular files are hot-swappable; use the normal deploy: {path}"
                )
    require_review(branch, commit, runtime)
    probe_sources = targeted_tests(commit, changed)
    pr = json.loads(run("gh", "pr", "view", branch, "--json", "number,headRefOid"))
    if pr["headRefOid"] != commit:
        raise ValueError("PR head changed; retry")
    if git("ls-remote", "--heads", "origin", branch).split()[0] != commit:
        raise ValueError("branch head changed during targeted tests; retry")
    manifest = {
        "pr": pr["number"],
        "branch": branch,
        "commit": commit,
        "deployed_commit": base,
        "operator": os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
        "time": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": [],
    }
    # Secrets travel only on SSH stdin, never in argv, records, or saved backups.
    smoke_env = {
        k: v
        for k, v in os.environ.items()
        if k.startswith("TINYASSETS_CANARY_") or k == "TINYASSETS_WIKI_CANARY_TOKEN"
    }
    if not smoke_env.get("TINYASSETS_WIKI_CANARY_TOKEN"):
        raise ValueError("TINYASSETS_WIKI_CANARY_TOKEN is required for authenticated smoke")
    manifest["smoke_env"] = smoke_env
    # Probe CLI runs on the host. Include its sibling helpers without installing
    # any scripts into the live application; the temporary tree is discarded.
    manifest["probe_sources"] = probe_sources
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        for path in paths:
            before = run("git", "-C", str(ROOT), "show", f"{base}:{path}")
            after = run("git", "-C", str(ROOT), "show", f"{commit}:{path}")
            if before == after:
                raise ValueError(f"unchanged file: {path}")
            manifest["files"].append(
                {"path": path, "before_sha256": digest(before), "after_sha256": digest(after)}
            )
            info = tarfile.TarInfo(path)
            info.size = len(after)
            archive.addfile(info, io.BytesIO(after))
        raw = json.dumps(manifest, sort_keys=True).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    return out.getvalue()


def record_key(record):
    return digest(json.dumps(record, sort_keys=True).encode())


def guard_revision(revision):
    """Runner attests merged hotfix bytes; the host checks actual image bytes."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("invalid candidate revision")
    active = json.loads(remote("list"))
    proof = {"revision": revision, "records": {}}
    for record in active:
        if record["status"] != "active":
            raise ValueError("deploy refused: unfinished hotfix; revert it first")
        merged = None
        if record.get("pr"):
            try:
                pr = json.loads(
                    run(
                        "gh",
                        "pr",
                        "view",
                        str(record["pr"]),
                        "--json",
                        "state,baseRefName,mergeCommit",
                    )
                )
                if pr["state"] == "MERGED" and pr["baseRefName"] == "main":
                    sha = pr["mergeCommit"]["oid"]
                    if all(
                        digest(run("git", "-C", str(ROOT), "show", f"{sha}:{entry['path']}"))
                        == entry["after_sha256"]
                        for entry in record["files"]
                    ):
                        git("merge-base", "--is-ancestor", sha, revision)
                        merged = sha
            except (subprocess.CalledProcessError, KeyError, TypeError, ValueError):
                # GitHub/history may be unavailable. This removes only the PR
                # exemption: the host still checks every applied file hash.
                pass
        proof["records"][record["id"]] = {"key": record_key(record), "merge": merged}
    return base64.b64encode(json.dumps(proof, sort_keys=True).encode()).decode()


def lock():
    import fcntl

    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = LOCK.open("a+")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def docker_file(container, path):
    return run("docker", "cp", f"{container}:/app/{path}", "-")


def cp_into(source, container, path):
    run("docker", "cp", str(source), f"{container}:/app/{path}")


def save_bytes(path, data, mode=0o644):
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(mode)


def wait_healthy(container):
    until = time.monotonic() + int(os.environ.get("HOTFIX_HEALTH_TIMEOUT", "180"))
    while time.monotonic() < until:
        state = (
            run(
                "docker",
                "inspect",
                "-f",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                container,
            )
            .decode()
            .strip()
        )
        if state in {"healthy", "running"}:
            return
        if state == "unhealthy":
            raise RuntimeError(f"{container} unhealthy after restart")
        time.sleep(2)
    raise RuntimeError(f"{container} health timed out")


def smoke_checks(env, probe_sources):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    token = env.get("TINYASSETS_WIKI_CANARY_TOKEN")
    if not token:
        raise ValueError("authenticated smoke token missing")
    opener = urllib.request.build_opener(NoRedirect)
    results = {}
    for path, expected, authenticated in (
        ("/app", 200, False),
        ("/mcp", 401, False),
        ("/mcp/pulse", 200, True),
    ):
        headers = {"Authorization": f"Bearer {token}"} if authenticated else {}
        request = urllib.request.Request("https://tinyassets.io" + path, headers=headers)
        try:
            with opener.open(request, timeout=30) as response:
                status = response.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        results[path] = status
        if status != expected:
            raise RuntimeError(f"smoke {path}: expected {expected}, got {status}")
    if probe_sources:
        with tempfile.TemporaryDirectory(prefix="hotfix-signin-") as temp:
            scripts = Path(temp) / "scripts"
            scripts.mkdir()
            for name, source in probe_sources.items():
                if PurePosixPath(name).name != name or not name.endswith(".py"):
                    raise ValueError("invalid probe source")
                (scripts / name).write_text(source, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(scripts / "live_signin_probe.py")],
                cwd=temp,
                env={**os.environ, **env},
                capture_output=True,
                timeout=180,
            )
            # Probe output may contain account details; persist the result only.
            results["signin"] = result.returncode
            if result.returncode:
                raise RuntimeError("live sign-in probe failed")
    else:
        results["signin"] = "not present"
    return {"passed": True, "checks": results}


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValueError("id must contain only letters, numbers, underscores and hyphens")
    return value


def read_record(path):
    """Normalize both shipped and hand-written records; never skip unknown state."""
    try:
        record = json.loads(path.read_text())
        valid_id(record["id"])
        if path.stem != record["id"]:
            raise ValueError("id must match the record filename")
        if "status" not in record:
            if type(record.get("active")) is not bool:
                raise ValueError("provide boolean active or explicit status")
            record["status"] = "active" if record["active"] else "reverted"
        if record["status"] not in {"active", "pending", "reverted", "archived"}:
            raise ValueError("status must be active, pending, reverted or archived")
        if isinstance(record["files"], dict):
            record["files"] = [
                {"path": name, "after_sha256": sha} for name, sha in record["files"].items()
            ]
            record["legacy"] = True
            match = re.fullmatch(r"hf-([1-9][0-9]*)-.+", record["id"])
            if match:
                record.setdefault("pr", int(match[1]))
        if not isinstance(record["files"], list) or not record["files"]:
            raise ValueError("files must be a nonempty path-to-sha256 map or entry list")
        paths = set()
        for entry in record["files"]:
            valid_file(entry["path"])
            if entry["path"] in paths:
                raise ValueError("duplicate file path")
            paths.add(entry["path"])
            if not re.fullmatch(r"[0-9a-f]{64}", entry["after_sha256"]):
                raise ValueError("each file needs its applied SHA256 in after_sha256")
        containers = record.get(
            "containers", [record["container"]] if "container" in record else []
        )
        if (
            not isinstance(containers, list)
            or not containers
            or any(
                not isinstance(c, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", c)
                for c in containers
            )
        ):
            raise ValueError("containers must be a nonempty list of Docker names or ids")
        record["containers"] = containers
        for entry in record["files"]:
            entry.setdefault("containers", containers)
            if not entry["containers"] or not set(entry["containers"]) <= set(containers):
                raise ValueError("file containers must name recorded containers")
        if record.get("pr") is not None and (type(record["pr"]) is not int or record["pr"] < 1):
            raise ValueError("pr must be a positive PR number")
        return record
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise ValueError(
            f"cannot read hotfix record {path}: {exc}. Fix {path}: retain id, "
            "containers, files (path -> applied sha256) and boolean active, or restore "
            "the valid record from backup; retain <id>.orig/ originals. Do not delete "
            "an active record to bypass the guard."
        ) from exc


def records():
    return [read_record(p) for p in sorted(STATE.glob("*.json"))] if STATE.exists() else []


def file_bytes(container, path):
    with tarfile.open(fileobj=io.BytesIO(docker_file(container, path))) as archive:
        members = archive.getmembers()
        if len(members) != 1 or not members[0].isfile():
            raise ValueError(f"not a regular file: {container}:/app/{path}")
        return archive.extractfile(members[0]).read(), members[0].mode & 0o777


def running_containers():
    """Discover services by their image, not a fixed daemon-only name."""
    rows = run("docker", "ps", "--format", "{{.Names}}\t{{.Image}}").decode().splitlines()
    found = [row.split("\t")[0] for row in rows if "tinyassets" in row.split("\t", 1)[1].lower()]
    if not found:
        raise ValueError("no running containers using a tinyassets image")
    return sorted(found)


def container_has_file(container, path):
    try:
        run("docker", "exec", container, "test", "-f", f"/app/{path}")
        return True
    except subprocess.CalledProcessError as exc:
        if exc.returncode == 1:
            return False
        raise


def restart_targets(containers, boxes):
    # The service closes all awake owner daemons before exiting; subsequent
    # admission boots them from the updated harness. Restart it before broker
    # registration, which must reconnect to the new control listener.
    if boxes:
        run("systemctl", "restart", "boxhostd.service")
        run("systemctl", "is-active", "--quiet", "boxhostd.service")
    for container in containers:
        run("docker", "restart", container)
        wait_healthy(container)


def original_path(record, entry, container):
    root = STATE / record["id"]
    if record.get("legacy"):
        root = STATE / (record["id"] + ".orig")
        candidates = [root / container / entry["path"], root / entry["path"]]
        # Early hand-written fixes used basename-only backups.
        if len({PurePosixPath(f["path"]).name for f in record["files"]}) == len(record["files"]):
            candidates.append(root / PurePosixPath(entry["path"]).name)
    elif record.get("version") == 2:
        candidates = [root / "containers" / container / entry["path"]]
    else:
        candidates = [root / entry["path"]]
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise ValueError(
        f"missing original: restore {candidates[0]} from the pre-hotfix backup before reverting"
    )


def prepare_recovery(record):
    """Capture legacy applied bytes BEFORE deployment discards writable layers."""
    for entry in record["files"]:
        target = STATE / record["id"] / "after" / entry["path"]
        if target.is_file() and digest(target.read_bytes()) == entry["after_sha256"]:
            continue
        data = None
        for container in entry["containers"]:
            try:
                candidate, mode = file_bytes(container, entry["path"])
            except subprocess.CalledProcessError:
                continue
            if digest(candidate) == entry["after_sha256"]:
                data = candidate
                break
        if data is None:
            raise ValueError(
                f"restore applied bytes with SHA256 {entry['after_sha256']} to {target}; "
                "no recorded container holds them; deployment refused"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        save_bytes(target, data, mode)


def host_guard(image, encoded, archive=False):
    """Called under deployment's lock, or by archive under our own lock."""
    active = [r for r in records() if r["status"] in {"active", "pending"}]
    if not active:
        return
    try:
        proof = json.loads(base64.b64decode(encoded, validate=True))
        expected = proof["records"]
        if set(expected) != {r["id"] for r in active} or any(
            r["status"] != "active" or expected[r["id"]]["key"] != record_key(r) for r in active
        ):
            raise ValueError("active hotfix set changed or is pending; rerun guard")
        revision = (
            run(
                "docker",
                "image",
                "inspect",
                "-f",
                '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
                image,
            )
            .decode()
            .strip()
        )
        if revision != proof["revision"]:
            raise ValueError("candidate image revision differs from proof; rerun guard")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"deploy refused: invalid hotfix proof: {exc}; rerun hotfix.py guard"
        ) from exc
    candidate = run("docker", "create", "--entrypoint", "/bin/true", image).decode().strip()
    try:
        for record in active:
            if not expected[record["id"]]["merge"]:
                for entry in record["files"]:
                    try:
                        data, _ = file_bytes(candidate, entry["path"])
                    except subprocess.CalledProcessError as exc:
                        raise ValueError(
                            f"candidate missing hotfixed file {entry['path']}"
                        ) from exc
                    if digest(data) != entry["after_sha256"]:
                        raise ValueError(
                            f"deploy refused: candidate lacks hotfix {record['id']} "
                            f"at {entry['path']}; include its applied bytes or merged PR"
                        )
            if not archive:
                prepare_recovery(record)
        if archive:
            for record in active:
                record.update(
                    status="archived",
                    active=False,
                    archived_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                )
                save_record(record)
    finally:
        run("docker", "rm", candidate)


def host_restore():
    active = [r for r in records() if r["status"] == "active"]
    targets, boxes = set(), False
    # Verify every saved payload before changing any runtime.
    for record in active:
        for entry in record["files"]:
            source = STATE / record["id"] / "after" / entry["path"]
            if not source.is_file() or digest(source.read_bytes()) != entry["after_sha256"]:
                raise ValueError(
                    f"saved hotfix bytes corrupt: restore {source} "
                    f"with SHA256 {entry['after_sha256']}"
                )
    for record in active:
        for entry in record["files"]:
            source = STATE / record["id"] / "after" / entry["path"]
            for container in entry["containers"]:
                cp_into(source, container, entry["path"])
                targets.add(container)
            if entry.get("box_before_sha256"):
                shutil.copy2(source, BOX / entry["path"])
                boxes = True
    if targets:
        restart_targets(sorted(targets), boxes)


def save_record(record):
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / f".{record['id']}.tmp"
    with tmp.open("w") as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, STATE / f"{record['id']}.json")


def host_apply(container=None, also_boxes=True):
    guard = lock()
    with tarfile.open(fileobj=io.BytesIO(sys.stdin.buffer.read())) as archive:
        names = archive.getnames()
        if names.count("manifest.json") != 1:
            raise ValueError("missing manifest")
        manifest = json.load(archive.extractfile("manifest.json"))
        smoke_env = manifest.pop("smoke_env", {})
        probe_sources = manifest.pop("probe_sources", {})
        files = manifest["files"]
        if (
            not files
            or len(names) != len(files) + 1
            or set(names) != {"manifest.json", *(f["path"] for f in files)}
        ):
            raise ValueError("archive contains unexpected or duplicate files")
        if any(not member.isfile() for member in archive):
            raise ValueError("archive contains non-file")
        if any(not eligible(f["path"]) for f in files):
            raise ValueError("not hot-swappable; use the normal deploy")
        if any(
            {f["path"] for f in r["files"]} & {f["path"] for f in files}
            for r in records()
            if r["status"] in ("active", "pending")
        ):
            raise ValueError("a named file already has an active hotfix")
        containers = running_containers()
        identifier = (
            dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        )
        saved = STATE / identifier
        saved.mkdir(parents=True)
        manifest.update(id=identifier, version=2, containers=containers, status="pending")
        touched = []
        box_touched = []
        try:
            for entry in files:
                path = valid_file(entry["path"])
                after = archive.extractfile(path).read()
                if digest(after) != entry["after_sha256"]:
                    raise ValueError(f"payload digest mismatch: {path}")
                entry["containers"] = [c for c in containers if container_has_file(c, path)]
                if not entry["containers"]:
                    raise ValueError(f"no running tinyassets container contains {path}")
                for target in entry["containers"]:
                    before, mode = file_bytes(target, path)
                    if digest(before) != entry["before_sha256"]:
                        raise ValueError(f"live file differs from deployed commit: {target}:{path}")
                    original = saved / "containers" / target / path
                    original.parent.mkdir(parents=True, exist_ok=True)
                    save_bytes(original, before, mode)
                patched = saved / "after" / path
                patched.parent.mkdir(parents=True, exist_ok=True)
                save_bytes(patched, after, mode)
                box_path = BOX / path
                if box_path.is_symlink():
                    raise ValueError(f"box harness file is a symlink: {path}")
                if also_boxes and box_path.is_file():
                    original = saved / "boxes" / path
                    original.parent.mkdir(parents=True, exist_ok=True)
                    save_bytes(original, box_path.read_bytes(), box_path.stat().st_mode & 0o777)
                    entry["box_before_sha256"] = digest(original.read_bytes())
            save_record(manifest)
            for entry in files:
                path = entry["path"]
                for target in entry["containers"]:
                    touched.append((target, path))
                    cp_into(saved / "after" / path, target, path)
                if entry.get("box_before_sha256"):
                    box_touched.append(path)
                    shutil.copy2(saved / "after" / path, BOX / path)
            restart_targets(sorted({c for c, _ in touched}), bool(box_touched))
            manifest["smoke"] = smoke_checks(smoke_env, probe_sources)
            manifest["status"] = "active"
            save_record(manifest)
            print(identifier)
        except BaseException as exc:
            manifest["smoke"] = {"passed": False, "error": str(exc)}
            # Only persist a pending record after mutation began. Preflight
            # failures cannot leave an invalid/incomplete record blocking deploy.
            if touched or box_touched:
                save_record(manifest)
                for target, path in touched:
                    cp_into(saved / "containers" / target / path, target, path)
                for path in box_touched:
                    shutil.copy2(saved / "boxes" / path, BOX / path)
                restart_targets(sorted({c for c, _ in touched}), bool(box_touched))
                manifest["status"] = "reverted"
                save_record(manifest)
                print(f"hotfix {identifier}: automatically reverted", file=sys.stderr)
            else:
                shutil.rmtree(saved)
            raise
    guard.close()


def host_revert(identifier):
    guard = lock()
    valid_id(identifier)
    record = read_record(STATE / f"{identifier}.json")
    if record["status"] not in {"active", "pending"}:
        raise ValueError("hotfix is not active or pending")
    originals = []
    box_originals = []
    for entry in record["files"]:
        name = entry["path"]
        for container in entry["containers"]:
            source = original_path(record, entry, container)
            before = digest(source.read_bytes())
            if entry.get("before_sha256") and before != entry["before_sha256"]:
                raise ValueError(f"saved original corrupt: {source}")
            live, _ = file_bytes(container, name)
            if digest(live) not in {before, entry["after_sha256"]}:
                raise ValueError(
                    f"live file changed: {container}:{name}; refuse to overwrite newer content"
                )
            originals.append((source, container, name))
        if entry.get("box_before_sha256"):
            source = STATE / identifier / "boxes" / name
            if digest(source.read_bytes()) != entry["box_before_sha256"]:
                raise ValueError(f"saved box original corrupt: {source}")
            if digest((BOX / name).read_bytes()) not in {
                entry["box_before_sha256"],
                entry["after_sha256"],
            }:
                raise ValueError(f"box file changed: {name}; refuse to overwrite newer content")
            box_originals.append((source, name))
    record["status"] = "pending"
    save_record(record)
    for source, container, name in originals:
        cp_into(source, container, name)
    for source, name in box_originals:
        shutil.copy2(source, BOX / name)
    restart_targets(sorted({c for _, c, _ in originals}), bool(box_originals))
    record.update(
        status="reverted", active=False, reverted_at=dt.datetime.now(dt.timezone.utc).isoformat()
    )
    save_record(record)
    guard.close()


def host_archive(proof, revision):
    guard = lock()
    current = (
        run(
            "docker",
            "inspect",
            "-f",
            '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
            "tinyassets-daemon",
        )
        .decode()
        .strip()
    )
    if current != revision:
        raise ValueError("deployed image changed; refusing archive")
    image = run("docker", "inspect", "-f", "{{.Image}}", "tinyassets-daemon").decode().strip()
    host_guard(image, proof, archive=True)
    guard.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--branch", required=True)
    apply.add_argument("--files", nargs="+", default=["auto"])
    revert = sub.add_parser("revert")
    revert.add_argument("id")
    sub.add_parser("list")
    sub.add_parser("revision")
    sub.add_parser("guard").add_argument("--revision", required=True)
    sub.add_parser("archive").add_argument("--revision", required=True)
    args = parser.parse_args()
    if args.action == "apply":
        payload = branch_payload(args.branch, args.files)
        print(
            remote("apply", "tinyassets-daemon", "1", input=payload).decode(),
            end="",
        )
    elif args.action == "guard":
        print(guard_revision(args.revision))
    elif args.action in ("revert", "list", "revision", "archive"):
        argv = [args.action]
        if args.action == "revert":
            argv.append(args.id)
        if args.action == "archive":
            argv += [args.revision, guard_revision(args.revision)]
        print(remote(*argv).decode(), end="")


def host_main():
    action = sys.argv[2]
    if action == "revision":
        print(
            run(
                "docker",
                "inspect",
                "-f",
                '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
                "tinyassets-daemon",
            )
            .decode()
            .strip()
        )
    elif action == "apply":
        host_apply(sys.argv[3], sys.argv[4] == "1")
    elif action == "revert":
        host_revert(sys.argv[3])
    elif action == "list":
        print(
            json.dumps(
                [r for r in records() if r["status"] in {"active", "pending"}], sort_keys=True
            )
        )
    elif action == "archive":
        host_archive(sys.argv[4], sys.argv[3])
    elif action == "guard-image":
        host_guard(sys.argv[3], sys.argv[4])
    elif action == "restore":
        host_restore()


if __name__ == "__main__":
    try:
        if sys.argv[1:2] == ["--host"]:
            host_main()
        else:
            main()
    except (ValueError, OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr.decode(errors="replace").strip(), file=sys.stderr)
        print(f"hotfix: {exc}", file=sys.stderr)
        sys.exit(1)
