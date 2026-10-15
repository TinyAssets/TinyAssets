"""Phase 7.4 v1 — git author identity resolution.

Covers the narrow v1 contract in :mod:`tinyassets.identity`:

- ``TINYASSETS_GIT_AUTHOR`` env var overrides everything (verbatim).
- Otherwise ``actor`` arg or ``UNIVERSE_SERVER_USER`` → slugged
  ``TinyAssets User <slug@users.noreply.tinyassets.local>``.
- Empty/None/invalid slug → ``anonymous``.

Also pins the ``git_bridge.commit(author=None)`` default so the
composite helpers can omit the argument and let identity resolve.
"""

from __future__ import annotations

import pytest

from tinyassets.identity import git_author

_DOMAIN = "users.noreply.tinyassets.local"
_DISPLAY = "TinyAssets User"


@pytest.fixture(autouse=True)
def _clean_identity_env(monkeypatch: pytest.MonkeyPatch):
    """Every test starts without TinyAssets identity env vars set."""
    monkeypatch.delenv("TINYASSETS_GIT_AUTHOR", raising=False)
    monkeypatch.delenv("UNIVERSE_SERVER_USER", raising=False)
    yield



def _sign_in(user_id: str) -> None:
    """Bind the subject the author is derived from."""
    from tinyassets.auth import middleware as _mw
    from tinyassets.auth.provider import Identity as _Identity

    _mw._current_identity.set(_Identity(user_id=user_id, username=user_id))


def test_no_actor_and_nobody_bound_refuses():
    """It used to fall back to an "anonymous" slug. A commit has an author or
    it does not happen (founder, 2026-09-02)."""
    import pytest

    from tinyassets.auth.middleware import auth_middleware

    auth_middleware(None)
    with pytest.raises(PermissionError, match="authenticated actor"):
        git_author()


def test_universe_server_user_env_slugs_into_composite(monkeypatch):
    # The bound principal, not the environment: an env var names nobody.
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    _sign_in("alice")
    assert git_author() == f"{_DISPLAY} <alice@{_DOMAIN}>"


def test_spaces_and_caps_are_slugified(monkeypatch):
    # The bound principal, not the environment: an env var names nobody.
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "Alice Smith")
    _sign_in("Alice Smith")
    assert git_author() == f"{_DISPLAY} <alice-smith@{_DOMAIN}>"


def test_special_chars_stripped_by_slug(monkeypatch):
    # The bound principal, not the environment: an env var names nobody.
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "  User@#$Name  ")
    _sign_in("  User@#$Name  ")
    assert git_author() == f"{_DISPLAY} <user-name@{_DOMAIN}>"


def test_workflow_git_author_env_is_verbatim(monkeypatch):
    monkeypatch.setenv(
        "TINYASSETS_GIT_AUTHOR", "Alice Real <alice@example.com>",
    )
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "would-be-ignored")
    assert git_author() == "Alice Real <alice@example.com>"


def test_workflow_git_author_env_strips_surrounding_whitespace(monkeypatch):
    monkeypatch.setenv(
        "TINYASSETS_GIT_AUTHOR", "  Bob <b@x.io>  ",
    )
    assert git_author() == "Bob <b@x.io>"


def test_workflow_git_author_blank_falls_through_to_composite(monkeypatch):
    monkeypatch.setenv("TINYASSETS_GIT_AUTHOR", "   ")
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "charlie")
    _sign_in("charlie")
    assert git_author() == f"{_DISPLAY} <charlie@{_DOMAIN}>"


def test_actor_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "env-user")
    assert git_author(actor="bob") == f"{_DISPLAY} <bob@{_DOMAIN}>"


def test_actor_empty_string_falls_back_to_the_bound_principal(monkeypatch):
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "env-user")
    # Empty actor is "no actor provided", not "explicitly nobody" -- so the
    # BOUND principal is used. The env var names nobody and never did confer
    # identity; it just used to be read here (founder, 2026-09-02).
    _sign_in("bound-user")
    assert git_author(actor="") == f"{_DISPLAY} <bound-user@{_DOMAIN}>"


def test_actor_none_falls_back_to_the_bound_principal(monkeypatch):
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "env-user")
    _sign_in("bound-user")
    assert git_author(actor=None) == f"{_DISPLAY} <bound-user@{_DOMAIN}>"


def test_actor_whitespace_only_falls_back_to_the_bound_principal():
    # Pure whitespace is "no actor provided". It used to slug to "anonymous";
    # there is no such author, so the bound principal is used.
    _sign_in("bound-user")
    assert git_author(actor="   ") == f"{_DISPLAY} <bound-user@{_DOMAIN}>"


# ─── git_bridge.commit default author wiring ──────────────────────────────


def test_git_bridge_commit_resolves_default_author(monkeypatch, tmp_path):
    """Calling commit() without ``author`` routes through git_author()."""
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available")

    from tinyassets import git_bridge
    from tinyassets.control_plane import binding as binding_module

    git_bridge.invalidate_cache()
    # Owner git runs only in an admitted owner box: the repository sits in a
    # command center under the data root, and the call carries its binding.
    center = tmp_path / "data" / "cc-author"
    center.mkdir(parents=True)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(center.parent))
    repo = center / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main"], cwd=str(repo),
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "ci@example.invalid"], cwd=str(repo),
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "CI Bot"], cwd=str(repo),
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=str(repo),
        check=True, capture_output=True,
    )
    (repo / "seed.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "seed.txt"], cwd=str(repo),
        check=True, capture_output=True,
    )

    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    _sign_in("alice")   # the author comes from the bound principal
    monkeypatch.delenv("TINYASSETS_GIT_AUTHOR", raising=False)

    # No author argument — default identity should be used
    with binding_module.use_binding(binding_module.file_binding(center)):
        result = git_bridge.commit("seed commit", repo_path=repo)
    assert result.ok, result.error

    logged = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>"], cwd=str(repo),
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert logged == f"{_DISPLAY} <alice@{_DOMAIN}>"
    git_bridge.invalidate_cache()
