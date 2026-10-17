"""A sign-in must never adopt a handle the caller chose.

The store no longer holds RAW WorkOS refresh tokens (they are sealed --
test_refresh_session_seal.py), but the file modes and the no-adoption rule still
have to hold, so these keep running against the sealed store. Two properties
matter, and both were broken:

1. The `authorization_code` grant wrote the NEW user's refresh token under whatever
   `session_ref` arrived in the request body, validated for SHAPE only. Plant a handle in
   a victim's localStorage, wait for them to sign in, then renew with it: their session.
2. The files were written with `os.replace` and no chmod, so they landed at the process
   umask. Observed 0644 on production -- world-readable inside the container.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from tinyassets import onboarding
from tinyassets.onboarding import session_store


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    # The seal key is a process singleton: without the reset, a key minted for a
    # previous tmp dir seals records this test cannot open.
    session_store._reset_for_tests()
    yield tmp_path
    session_store._reset_for_tests()


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX modes: os.chmod on Windows only toggles the read-only bit, so this "
    "asserts nothing there. Production and CI are Linux, which is where it counts.",
)
def test_a_stored_session_is_not_readable_by_anyone_else():
    handle = onboarding._mint_refresh_session("rt_secret_value", owner="owner")
    # Resolve the file the way the code does. This test was written when the handle
    # WAS the filename; the digest rename left it pointing at a path that no longer
    # exists, and because it skips on Windows the local run never noticed. CI on
    # Linux did. Ask the store for the path now, so the next rename cannot repeat it.
    path = session_store._record_path(handle)
    assert path.exists(), "the store did not write where the code reads"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0, (
        f"refresh token file is group/other readable: {oct(mode)}"
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory modes")
def test_the_store_directory_is_not_traversable_by_anyone_else():
    onboarding._refresh_store_dir()
    d = onboarding._refresh_store_dir()
    mode = stat.S_IMODE(d.stat().st_mode)
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0, (
        f"refresh store directory is group/other accessible: {oct(mode)}"
    )


def test_the_token_is_written_before_the_file_is_ever_world_readable(tmp_path):
    """Not 'chmod after replace' -- the window in between is the whole problem.

    The write moved into the sealed store, and `_write_refresh_session` (a write
    under a CALLER-supplied handle) was deleted rather than moved, so this reads
    the one remaining writer.
    """
    src = (
        __import__("pathlib").Path(session_store.__file__).read_text(encoding="utf-8")
    )
    body = src.split("def _write_record")[1].split("\ndef ")[0]
    chmod_at = body.index("chmod")
    replace_at = body.index("os.replace")
    assert chmod_at < replace_at, (
        "the mode must be narrowed BEFORE the atomic swap, or the token file exists "
        "at the umask default for a window"
    )


def test_a_new_signin_never_adopts_a_caller_supplied_handle():
    """The fixation. Reading the route source because the takeover lives in a branch,
    not in a helper -- a helper-level test would pass while the route stayed wrong."""
    src = (
        __import__("pathlib").Path(onboarding.__file__).read_text(encoding="utf-8")
    )
    assert "may_reuse_handle" in src, "the reuse guard is gone"
    guard = src.split("may_reuse_handle = ")[1].split("\n")[0]
    # This originally asserted the guard was `grant == "refresh_token"`. That rule
    # was insufficient and the assertion enshrined it: a refresh can succeed off the
    # victim's cookie while the presented handle proved nothing. The real invariant
    # is the credential SOURCE (test_session_credential_source.py).
    assert "refresh_came_from_handle" in guard, (
        "a handle may only be reused when THAT handle supplied the credential the "
        "exchange actually succeeded with"
    )


def test_a_presented_handle_is_dropped_when_a_new_signin_mints_its_own():
    """Otherwise a token stays renewable under an identifier a third party may know."""
    src = (
        __import__("pathlib").Path(onboarding.__file__).read_text(encoding="utf-8")
    )
    tail = src.split("may_reuse_handle = ")[1]
    mint_branch = tail.split("else:")[1].split("elif")[0]
    assert "_drop_refresh_session(session_ref)" in mint_branch


def test_the_round_trip_still_works():
    handle = onboarding._mint_refresh_session("rt_abc", owner="owner")
    assert onboarding._read_refresh_session(handle) == "rt_abc"
    onboarding._drop_refresh_session(handle)
    assert onboarding._read_refresh_session(handle) == ""


def test_an_expired_session_yields_nothing_and_is_removed():
    import time

    handle = onboarding._mint_refresh_session("rt_abc", owner="owner")
    # The filename is a keyed digest now, not the handle -- the handle is a bearer
    # credential and must not be published by a directory listing.
    path = session_store._record_path(handle)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["exp"] = int(time.time()) - 1
    path.write_text(json.dumps(record), encoding="utf-8")
    assert onboarding._read_refresh_session(handle) == ""
    assert not path.exists()
