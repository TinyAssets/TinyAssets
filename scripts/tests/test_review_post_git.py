"""The reviewer's git reads through the poster, the only command a reviewer may run:
fixed arguments, a sha in the PR's history, a path inside the repository, and no
config, pager, external diff or textconv driver that could run a command. Real git
in a temporary repository whose own config is hostile."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SCRIPTS))
import review_post as rp  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


@pytest.fixture
def pr(tmp_path, monkeypatch):
    """main at A, the PR's head B on it, an unrelated commit C; a repo config and a
    global config that would each run a command if git honoured them."""
    repo = tmp_path / "main"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\n")
    (repo / ".gitattributes").write_text("* diff=evil\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "A")
    a = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", a)
    (repo / "a.txt").write_text("one\ntwo\n")
    _git(repo, "commit", "-q", "-am", "B")
    b = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "--orphan", "other")
    _git(repo, "commit", "-q", "-m", "C")
    c = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "--detach", a)
    marker = tmp_path / "ran"
    for key, value in (("diff.external", f"touch {marker}"),
                       ("diff.evil.textconv", f"touch {marker}; cat"),
                       ("core.pager", f"touch {marker}; cat")):
        _git(repo, "config", key, value)
    # Global and environment config that would show in the output if git read it:
    # diff.noprefix drops the `a/` and `b/` of a diff's header.
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[diff]\n\tnoprefix = true\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_EXTERNAL_DIFF", f"touch {marker}")
    monkeypatch.setattr(rp, "_CHECKOUT", repo)
    return rp.git_reader(rp.resolve_tool("git")), a, b, c, marker


def test_the_reads_answer_and_run_no_driver(pr):
    run_git, a, b, c, marker = pr
    assert rp.git_read(run_git, "show", b, path="a.txt") == "one\ntwo\n"
    assert "+two" in rp.git_read(run_git, "diff", b)
    assert "diff --git a/a.txt b/a.txt" in rp.git_read(run_git, "diff", b)
    assert "+two" in rp.git_read(run_git, "diff", b, path="a.txt")
    assert rp.git_read(run_git, "grep", b, pattern="two") == f"{b}:a.txt:2:two\n"
    assert rp.git_read(run_git, "grep", b, pattern="absent") == ""
    assert rp.git_read(run_git, "log", b).split()[0] == b
    assert not marker.exists()


def test_a_commit_outside_the_prs_history_or_not_a_sha_is_refused(pr):
    run_git, a, b, c, marker = pr
    assert rp.checked_commit(run_git, b, b) == b
    assert rp.checked_commit(run_git, a, b) == a
    for bad in (c, "f" * 40, "HEAD", "--output=/tmp/x", b[:12]):
        with pytest.raises(rp.Refused):
            rp.checked_commit(run_git, bad, b)
    with pytest.raises(rp.Refused, match="not fetched"):
        rp.checked_commit(run_git, b, "e" * 40)


@pytest.mark.parametrize("path", ["../main/a.txt", "/etc/passwd", "a/../../x", "-p",
                                  "--output=/tmp/x", "", ".", "a\nb"])
def test_a_path_outside_the_repository_or_like_a_flag_is_refused(path):
    with pytest.raises(rp.Refused):
        rp.checked_path(path)


def test_config_from_the_environment_is_not_read(pr, monkeypatch):
    """This session's environment carries GIT_CONFIG_KEY_n variables (names read
    2026-11-28); the reads strip every GIT_ variable."""
    run_git, a, b, c, marker = pr
    for key, value in (("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_KEY_0", "diff.noprefix"),
                       ("GIT_CONFIG_VALUE_0", "true")):
        monkeypatch.setenv(key, value)
    run_git = rp.git_reader(rp.resolve_tool("git"))
    assert "diff --git a/a.txt b/a.txt" in rp.git_read(run_git, "diff", b)


def test_a_pattern_cannot_become_a_flag(pr):
    run_git, a, b, c, marker = pr
    # `-e` makes the pattern a pattern, so a flag's text is searched for, not obeyed.
    assert rp.git_read(run_git, "grep", b, pattern="--no-index") == ""
    assert rp.git_read(run_git, "grep", b, pattern="--version") == ""
    with pytest.raises(rp.Refused):
        rp.git_read(run_git, "grep", b, pattern="")
    with pytest.raises(rp.Refused):
        rp.git_read(run_git, "show", b)


def test_the_command_line_reads_the_prs_head_from_github_first(pr, capsys):
    run_git, a, b, c, marker = pr

    def get(path: str) -> object:
        assert path == "repos/o/r/pulls/7"
        return {"head": {"sha": b}}
    argv = ["show", "--repo", "o/r", "--pr", "7", "--commit", b, "--path", "a.txt"]
    assert rp.run(argv, get=get, git=run_git) == 0
    assert capsys.readouterr().out == "one\ntwo\n"
    assert rp.run([*argv[:-1], "../x"], get=get, git=run_git) == 2
    assert rp.run([*argv[:6], c, "--path", "a.txt"], get=get, git=run_git) == 2
    assert "not in PR's history" in capsys.readouterr().err
