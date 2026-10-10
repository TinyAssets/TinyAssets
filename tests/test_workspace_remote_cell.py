"""One workspace operation inside the owner's cell (``workspace_remote_cell``).

Real git, a real local repository standing in for the remote, and the real
no-follow helpers: everything the cell does to a repository runs here. What is
NOT here is the cell itself -- the bubblewrap entry, the mapper and the
loopback forwarder are the oracle's (``deploy/role_git.py``); this is the body
that runs inside it.

The route is modelled the way the daemon builds it: the cell is handed git
OPTIONS (``url.<somewhere>.insteadOf=https://<host>/<repo>.git``) and it builds
the canonical https URL itself, so a URL it did not build reaches nothing.
That is exactly how the production rewrite works -- only the right-hand side is
a path here instead of a broker route, because a test process runs no proxy.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tinyassets import workspace_remote_cell as cell
from tinyassets.workspace_git import WorkspaceGitError, create_bundle, run_git_in_cell

GIT = shutil.which("git")
pytestmark = [
    pytest.mark.skipif(GIT is None, reason="the cell is git; there is none here"),
    pytest.mark.skipif(os.name != "posix", reason="the cell's helpers are POSIX-only"),
]

HOST = "github.com"
REPO = "owner/name"
CENTER = "universe-cell"
LEASE = "e" * 32


def _git(*args: str, cwd: Path, home: Path) -> str:
    done = subprocess.run(
        [GIT, *args], cwd=str(cwd), check=True, capture_output=True, text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
             "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
             "LANG": "C.UTF-8"},
    )
    return done.stdout.strip()


@pytest.fixture()
def rig(tmp_path: Path):
    """A command center with a prepared pool directory, and a bare 'remote'."""
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "--quiet", "--initial-branch=main", ".", cwd=work, home=home)
    (work / "README.md").write_text("hello\n")
    _git("add", "README.md", cwd=work, home=home)
    _git("commit", "--quiet", "-m", "first", cwd=work, home=home)
    head = _git("rev-parse", "HEAD", cwd=work, home=home)
    remote = tmp_path / "remote.git"
    _git("clone", "--quiet", "--bare", str(work), str(remote), cwd=tmp_path, home=home)

    center = tmp_path / "data" / CENTER
    # The DAEMON prepares and labels these (workspace_owner_pool.prepare); a
    # test process owns everything it makes, so a plain mkdir is the same
    # starting state the cell would find.
    (center / "workspaces" / "scratch").mkdir(parents=True)
    scratch = tmp_path / "cell-tmp"
    scratch.mkdir()
    return type("Rig", (), {
        "center": center, "remote": remote, "scratch": scratch, "home": home,
        "work": work, "head": head,
    })()


def _options(rig) -> list[str]:
    return ["-c", f"url.{rig.remote}.insteadOf=https://{HOST}/{REPO}.git"]


def _perform(rig, request: dict) -> dict:
    fd = os.open(rig.center, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return cell.perform(request, root_fd=fd, root_path=rig.center,
                            scratch=rig.scratch, git_binary=GIT,
                            path=os.environ.get("PATH", "/usr/bin:/bin"))
    finally:
        os.close(fd)


def _checkout_request(rig, **over) -> dict:
    request = {
        "op": "checkout", "options": _options(rig), "timeout_s": 120.0,
        "host": HOST, "repo": REPO, "ref": "main", "storage": "scratch",
        "lease_parent": ["workspaces", "scratch"], "lease_name": LEASE,
        "checkout_ref": "tiny/u/checkout",
    }
    request.update(over)
    return request


# --------------------------------------------------------------------------- #
# checkout
# --------------------------------------------------------------------------- #


def test_a_checkout_clones_through_the_route_and_populates_the_lease(rig) -> None:
    answer = _perform(rig, _checkout_request(rig))

    assert answer["ok"] is True, answer
    assert answer["resolved_sha"] == rig.head
    assert answer["bytes"] > 0
    assert answer["lease"] == f"workspaces/scratch/{LEASE}"
    assert answer["content"] == "repo"
    repo = rig.center / "workspaces" / "scratch" / LEASE / "repo"
    assert (repo / "README.md").read_text() == "hello\n"
    assert (repo / ".git").is_dir()


def test_the_lease_the_cell_makes_is_owned_by_the_cell_and_daemon_reachable(rig) -> None:
    """A node sandbox mounts a workspace only when its owner owns it, and the
    daemon still has to open, measure and reclaim it -- which is what the group
    bits (the inherited ACL mask) are for."""
    _perform(rig, _checkout_request(rig))
    lease = rig.center / "workspaces" / "scratch" / LEASE
    for path in (lease, lease / "repo"):
        info = path.stat()
        assert info.st_uid == os.getuid()
        assert stat.S_IMODE(info.st_mode) == 0o770, oct(stat.S_IMODE(info.st_mode))


def test_the_populated_repository_carries_no_remote_and_no_host_path(rig) -> None:
    """The workspace's ``.git`` must not tell user code where anything is, and
    must not hold a remote a later git could push to."""
    _perform(rig, _checkout_request(rig))
    repo = rig.center / "workspaces" / "scratch" / LEASE / "repo"
    config = (repo / ".git" / "config").read_text()
    assert "remote" not in config
    assert str(rig.remote) not in config
    assert str(rig.scratch) not in config
    branches = _git("branch", "--format=%(refname:short)", cwd=repo, home=rig.home)
    assert branches.splitlines() == ["tiny/u/checkout"]


def test_the_cells_working_directory_is_gone_before_it_answers(rig) -> None:
    """It held the bare clone and the bundle, inside the lease the pool
    reserved -- and a lease published beside that material is a lease
    published beside the thing it was supposed to replace."""
    _perform(rig, _checkout_request(rig))
    lease = rig.center / "workspaces" / "scratch" / LEASE
    assert not (lease / cell.CELL_DIR).exists()
    assert sorted(p.name for p in lease.iterdir()) == ["repo"]


def test_a_working_directory_a_killed_cell_left_behind_does_not_wedge_the_lease(rig) -> None:
    lease = rig.center / "workspaces" / "scratch" / LEASE
    lease.mkdir()
    leftover = lease / cell.CELL_DIR
    (leftover / "src.git").mkdir(parents=True)
    (leftover / "out.bundle").write_bytes(b"half a bundle")
    # The lease name is taken, so this run gets its own; the point is that the
    # leftover is removed rather than refused.
    answer = _perform(rig, _checkout_request(rig, lease_name="f" * 32))
    assert answer["ok"] is True, answer
    second = rig.center / "workspaces" / "scratch" / ("f" * 32)
    assert not (second / cell.CELL_DIR).exists()


def test_a_scratch_lease_name_must_be_unguessable(rig) -> None:
    """Its parent has two writers -- the daemon and this owner -- so the name
    is what keeps either from targeting the other's."""
    answer = _perform(rig, _checkout_request(rig, lease_name="short"))
    assert answer["ok"] is False
    assert "16" in answer["error"] or "hex" in answer["error"]
    assert not (rig.center / "workspaces" / "scratch" / "short").exists()


def test_a_permanent_generation_is_a_plain_name_under_its_repository_key(rig) -> None:
    """``'1'`` is not 16 hex characters, and the entropy rule protects nothing
    inside a directory the daemon prepared for exactly one owner."""
    (rig.center / "workspaces" / "github.com--owner--name").mkdir()
    answer = _perform(rig, _checkout_request(
        rig, storage="universe", lease_parent=["workspaces", "github.com--owner--name"],
        lease_name="1"))
    assert answer["ok"] is True, answer
    assert answer["lease"] == "workspaces/github.com--owner--name/1"


def test_a_pool_directory_the_daemon_did_not_prepare_is_a_refusal(rig) -> None:
    """The cell creates the lease and nothing above it: a missing pool
    directory means the daemon never labelled one for this owner."""
    answer = _perform(rig, _checkout_request(
        rig, lease_parent=["workspaces", "not-prepared"]))
    assert answer["ok"] is False
    assert not (rig.center / "workspaces" / "not-prepared").exists()


def test_a_ref_the_remote_does_not_have_is_a_classified_refusal(rig) -> None:
    answer = _perform(rig, _checkout_request(rig, ref="no-such-branch"))
    assert answer["ok"] is False
    assert answer["stderr_class"] in ("transport", "not_found", "other", "verification")
    assert "clone failed" in answer["error"]


def test_a_url_the_route_does_not_rewrite_reaches_nothing(rig) -> None:
    """The cell builds ``https://<host>/<repo>.git`` and only the route makes
    that resolve. A request for another repository rewrites to nothing, and in
    production there is no network under it at all."""
    answer = _perform(rig, _checkout_request(rig, repo="owner/elsewhere"))
    assert answer["ok"] is False


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #


def test_a_create_makes_the_lease_and_runs_no_git(rig) -> None:
    answer = _perform(rig, {
        "op": "create", "options": [], "timeout_s": 60.0, "storage": "scratch",
        "lease_parent": ["workspaces", "scratch"], "lease_name": LEASE,
    })
    assert answer == {"ok": True, "lease": f"workspaces/scratch/{LEASE}", "bytes": 0,
                      "content": "repo"}
    lease = rig.center / "workspaces" / "scratch" / LEASE
    assert sorted(p.name for p in lease.iterdir()) == ["repo"]
    assert list((lease / "repo").iterdir()) == []


# --------------------------------------------------------------------------- #
# push
# --------------------------------------------------------------------------- #


def _export(rig, lease: Path, message: str) -> str:
    """What the jail leaves behind: one bundle carrying the export ref."""
    repo = lease / "repo"
    (repo / "NEW.md").write_text(message)
    _git("add", "NEW.md", cwd=repo, home=rig.home)
    _git("commit", "--quiet", "-m", message, cwd=repo, home=rig.home)
    sha = _git("rev-parse", "HEAD", cwd=repo, home=rig.home)
    export = repo / cell.EXPORT_DIR
    export.mkdir(exist_ok=True)
    scratch = rig.scratch / f"export-{sha}"
    scratch.mkdir()
    home = rig.scratch / f"export-home-{sha}"
    home.mkdir()
    create_bundle(repo, sha, export / f"{sha}.bundle", home_dir=home,
                  path=os.environ.get("PATH", "/usr/bin:/bin"), scratch_dir=scratch,
                  git_binary=GIT, runner=run_git_in_cell)
    return sha


def _push_request(rig, sha: str, **over) -> dict:
    request = {
        "op": "push", "options": _options(rig), "timeout_s": 120.0,
        "host": HOST, "repo": REPO, "remote_ref": "refs/heads/tiny/u/slug",
        "commit_sha": sha, "lease_parent": ["workspaces", "scratch"],
        "lease_name": LEASE, "reconcile_only": False,
        "max_bundle_bytes": cell.MAX_BUNDLE_BYTES,
    }
    request.update(over)
    return request


def test_a_push_sends_the_jails_bundle_to_an_exact_ref(rig) -> None:
    _perform(rig, _checkout_request(rig))
    lease = rig.center / "workspaces" / "scratch" / LEASE
    sha = _export(rig, lease, "second")

    answer = _perform(rig, _push_request(rig, sha))

    assert answer["ok"] is True, answer
    assert (answer["resolved_sha"], answer["remote_ref"]) == (sha, "refs/heads/tiny/u/slug")
    assert answer["head_ref"] == "refs/heads/main"
    landed = _git("rev-parse", "refs/heads/tiny/u/slug", cwd=rig.remote, home=rig.home)
    assert landed == sha
    assert not (lease / cell.CELL_DIR).exists(), "the import and the verify repo are gone"


def test_a_push_never_targets_the_remotes_default_branch(rig) -> None:
    _perform(rig, _checkout_request(rig))
    lease = rig.center / "workspaces" / "scratch" / LEASE
    sha = _export(rig, lease, "second")

    answer = _perform(rig, _push_request(rig, sha, remote_ref="refs/heads/main"))

    assert answer["ok"] is False
    assert answer["stderr_class"] == "protected"
    assert _git("rev-parse", "refs/heads/main", cwd=rig.remote, home=rig.home) == rig.head


def test_a_push_whose_bundle_is_not_the_named_commit_is_refused(rig) -> None:
    _perform(rig, _checkout_request(rig))
    lease = rig.center / "workspaces" / "scratch" / LEASE
    sha = _export(rig, lease, "second")
    # Rename the bundle so the request names a commit the file does not carry.
    other = "b" * 40
    (lease / "repo" / cell.EXPORT_DIR / f"{sha}.bundle").rename(
        lease / "repo" / cell.EXPORT_DIR / f"{other}.bundle")

    answer = _perform(rig, _push_request(rig, other))

    assert answer["ok"] is False
    assert answer["stderr_class"] == "verification"
    with pytest.raises(subprocess.CalledProcessError):
        _git("rev-parse", "refs/heads/tiny/u/slug", cwd=rig.remote, home=rig.home)


def test_a_push_with_no_bundle_in_the_lease_is_a_verification_refusal(rig) -> None:
    _perform(rig, _checkout_request(rig))
    answer = _perform(rig, _push_request(rig, rig.head))
    assert answer["ok"] is False
    assert answer["stderr_class"] == "verification"
    assert "export bundle could not be read" in answer["error"]


def test_reconcile_only_asks_the_remote_and_pushes_nothing(rig) -> None:
    _perform(rig, _checkout_request(rig))
    lease = rig.center / "workspaces" / "scratch" / LEASE
    sha = _export(rig, lease, "second")

    missing = _perform(rig, _push_request(rig, sha, reconcile_only=True))
    assert missing == {
        "ok": False,
        "error": "the push outcome was lost and the remote does not hold this commit",
        "stderr_class": "non_fast_forward", "observed_sha": "",
        "remote_ref": "refs/heads/tiny/u/slug", "reconciled": True,
    }

    assert _perform(rig, _push_request(rig, sha))["ok"] is True
    settled = _perform(rig, _push_request(rig, sha, reconcile_only=True))
    assert settled == {"ok": True, "resolved_sha": sha,
                       "remote_ref": "refs/heads/tiny/u/slug", "reconciled": True,
                       "bytes": 0}


# --------------------------------------------------------------------------- #
# ls_remote
# --------------------------------------------------------------------------- #


def test_ls_remote_reports_the_head_ref_and_an_absent_ref_as_empty(rig) -> None:
    answer = _perform(rig, {
        "op": "ls_remote", "options": _options(rig), "timeout_s": 60.0,
        "host": HOST, "repo": REPO, "remote_ref": "refs/heads/tiny/u/slug",
    })
    assert answer == {"ok": True, "head_ref": "refs/heads/main", "observed_sha": "",
                      "bytes": 0}


def test_ls_remote_reports_a_ref_the_remote_holds(rig) -> None:
    _git("update-ref", "refs/heads/tiny/u/slug", rig.head, cwd=rig.remote, home=rig.home)
    answer = _perform(rig, {
        "op": "ls_remote", "options": _options(rig), "timeout_s": 60.0,
        "host": HOST, "repo": REPO, "remote_ref": "refs/heads/tiny/u/slug",
    })
    assert answer["observed_sha"] == rig.head


# --------------------------------------------------------------------------- #
# the request the cell accepts
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("request_over,reason", [
    ({"op": "provision"}, "no known workspace operation"),
    ({"extra": "field"}, "exactly its own fields"),
    ({"options": ""}, "bounded git option"),
    ({"options": ["--upload-pack=sh\0"]}, "bounded git option"),
    ({"timeout_s": 0}, "timeout_s is outside"),
    ({"timeout_s": 100000}, "timeout_s is outside"),
    ({"host": "not a host"}, "request.host"),
    ({"repo": "../../etc"}, "request.repo"),
    ({"repo": "owner"}, "request.repo"),
    ({"ref": "-oops"}, "request.ref"),
    ({"lease_parent": []}, "lease_parent"),
    ({"lease_parent": ["workspaces", "../up"]}, "lease_parent"),
    ({"lease_name": "../up"}, "lease_name"),
    ({"storage": "elsewhere"}, "request.storage"),
    ({"checkout_ref": "a..b"}, "request.checkout_ref"),
])
def test_a_request_outside_the_grammar_runs_nothing(rig, request_over, reason) -> None:
    request = _checkout_request(rig)
    request.update(request_over)
    answer = _perform(rig, request)
    assert answer["ok"] is False
    assert answer["stderr_class"] == "bad_argument"
    assert reason in answer["error"], answer["error"]
    assert not (rig.center / "workspaces" / "scratch" / LEASE).exists()


def test_a_remote_operation_without_route_options_is_refused(rig) -> None:
    """Without the rewrite the canonical URL reaches nothing, so running git
    at all would be a request for an error message instead of a refusal."""
    answer = _perform(rig, _checkout_request(rig, options=[]))
    assert answer["ok"] is False
    assert "egress route options" in answer["error"]


def test_the_validator_is_the_one_grammar_and_it_is_exported(rig) -> None:
    with pytest.raises(WorkspaceGitError) as caught:
        cell.validate({"op": "checkout"})
    assert caught.value.code == "bad_argument"
    assert cell.validate(_checkout_request(rig))["op"] == "checkout"
