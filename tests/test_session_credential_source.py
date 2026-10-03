"""Handle reuse is licensed by the credential SOURCE, not the grant name.

The first attempt at this gated on `grant == "refresh_token"`. Codex showed that is
not enough: a refresh can succeed off the victim's HttpOnly cookie while the presented
handle resolved to nothing, and the rotated token then gets filed under the handle the
CALLER chose. The grant name was right; nothing had been proven.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tinyassets import onboarding
from tinyassets.onboarding import session_store

SRC = pathlib.Path(onboarding.__file__).read_text(encoding="utf-8")
STORE_SRC = pathlib.Path(session_store.__file__).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    # The seal key is a process singleton and now also keys the filename digest.
    session_store._reset_for_tests()
    yield tmp_path
    session_store._reset_for_tests()


# --- the credential-source invariant ----------------------------------------


def test_reuse_is_keyed_on_where_the_credential_came_from():
    guard = SRC.split("may_reuse_handle = ")[1].split("\n")[0]
    assert "refresh_came_from_handle" in guard, guard
    assert "grant ==" not in guard, (
        "the grant NAME is not evidence: a refresh can succeed off the cookie while "
        "the presented handle proved nothing"
    )


def test_the_source_flag_is_set_before_the_cookie_fallback():
    """Order is the whole property. Set it after, and a cookie refresh looks proven."""
    body = SRC.split('if grant == "refresh_token":')[1].split("token_form")[0]
    set_at = body.index("refresh_came_from_handle = bool(refresh)")
    cookie_at = body.index("_REFRESH_COOKIE")
    assert set_at < cookie_at, (
        "the flag must capture whether the HANDLE supplied the credential, which is "
        "only true before the cookie fallback runs"
    )


def test_the_flag_defaults_false_so_authorization_code_can_never_reuse():
    head = SRC.split("grant = str(data.get")[1].split('if grant == "logout"')[0]
    assert "refresh_came_from_handle = False" in head, (
        "an authorization_code exchange must reach the tail with the flag already "
        "False -- there is nothing for it to prove"
    )


# --- the handle is not the filename -----------------------------------------


def test_the_bearer_handle_is_never_used_as_a_filename():
    """Listing the directory would otherwise hand out live credentials."""
    for src in (SRC, STORE_SRC):
        assert 'f"{handle}.json"' not in src, (
            "the handle IS a bearer credential; using it as a filename publishes "
            "every live session to anything that can list the directory"
        )
    # This used to count `_handle_path_key(handle)` call sites in the route module,
    # which only worked while every access built its own path. The store has ONE
    # path builder, so the stronger statement is that it is the only one.
    assert STORE_SRC.count('f"{_handle_path_key(handle)}.json"') == 1
    assert STORE_SRC.count("store_dir() / ") == 1, (
        "every record path must be built in the one _record_path() helper"
    )


def test_the_stored_filename_does_not_contain_the_handle():
    handle = onboarding._mint_refresh_session("rt_secret")
    names = [p.name for p in session_store.store_dir().iterdir()]
    assert names, "nothing was written"
    assert all(handle not in n for n in names), names
    assert all(re.fullmatch(r"[0-9a-f]{64}\.json", n) for n in names), names


def test_the_digest_is_keyed_when_a_key_is_configured(monkeypatch):
    """Otherwise a directory listing is one sha256 of a guess away from the handle.

    Keyed with the SEAL key now, not the billing entitlement key: the store has
    its own key, and reusing an unrelated one for this was cross-purpose.
    """
    monkeypatch.setenv(session_store.SEAL_KEY_ENV, "a" * 64)
    session_store._reset_for_tests()
    a = onboarding._handle_path_key("H" * 43)
    monkeypatch.setenv(session_store.SEAL_KEY_ENV, "b" * 64)
    session_store._reset_for_tests()
    b = onboarding._handle_path_key("H" * 43)
    assert a != b, "the digest must depend on the key"


def test_an_unconfigured_deployment_still_does_not_publish_the_handle(monkeypatch):
    """The ephemeral key keeps the digest keyed even with nothing configured."""
    monkeypatch.delenv(session_store.SEAL_KEY_ENV, raising=False)
    session_store._reset_for_tests()
    key = onboarding._handle_path_key("H" * 43)
    assert "H" * 43 not in key
    assert re.fullmatch(r"[0-9a-f]{64}", key)


def test_two_handles_do_not_collide():
    a = onboarding._mint_refresh_session("rt_a")
    b = onboarding._mint_refresh_session("rt_b")
    assert onboarding._read_refresh_session(a) == "rt_a"
    assert onboarding._read_refresh_session(b) == "rt_b"
