"""The app's refresh-token store is sealed, handle-bound, and rotates.

Every assertion here observes an outcome the PLAINTEXT store failed:

- it wrote the raw WorkOS refresh token to a JSON file any reader of the data dir
  could open (``{"rt": ...}``), so a record was portable between handles;
- the token endpoint filed the token under a handle the CALLER chose;
- the handle never changed, so one capture lasted the full 7-day TTL;
- the seal key, once introduced, would have been inherited by every provider
  subprocess spawned from this process.

The endpoint harness is imported from ``test_onboarding_session_refresh`` rather
than rebuilt -- a second copy of the AuthKit fake is a second thing to drift.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import time

import httpx
import pytest

from tests.test_onboarding_session_refresh import _drive, _ok
from tinyassets import onboarding
from tinyassets.onboarding import session_store

CODE_EXCHANGE = {
    "code": "c",
    "code_verifier": "v",
    "redirect_uri": "https://tinyassets.io/app",
}


def test_refresh_owner_is_authenticated_and_legacy_handles_cannot_renew():
    handle = session_store.mint("RT-owner", owner="alice")
    path = session_store._record_path(handle)
    record = json.loads(path.read_text())
    record["owner"] = "bob"
    path.write_text(json.dumps(record))
    assert session_store.read(handle) == ("", "")

    legacy = session_store.mint("RT-legacy", owner="alice")
    path = session_store._record_path(legacy)
    record = json.loads(path.read_text())
    record.pop("owner")
    record["v"] = 1
    path.write_text(json.dumps(record))
    assert session_store.read(legacy) == ("", "")


def test_revoked_handle_cannot_rotate_back_into_a_session():
    handle = session_store.mint("RT-owner", owner="alice")
    session_store.revoke_owner("alice")
    assert session_store.rotate(handle, "RT-new") == ""
    assert session_store.read(handle) == ("", "")


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """A fresh data dir AND a fresh key singleton for every test.

    Without the reset the first test's ephemeral key seals records the next test
    cannot open, and the one-time legacy discard would run for the first tmp dir
    only.
    """
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv(session_store.SEAL_KEY_ENV, raising=False)
    session_store._reset_for_tests()
    yield tmp_path
    session_store._reset_for_tests()


def _records() -> list:
    return sorted(session_store.store_dir().glob("*.json"))


# --- 1. sealed at rest --------------------------------------------------------


def test_the_stored_token_is_nowhere_in_the_file():
    """The plaintext store wrote `{"rt": "<the token>"}`. Nothing may carry it."""
    token = "RT-" + secrets.token_urlsafe(24)
    handle = session_store.mint(token, owner="owner")
    files = _records()
    assert len(files) == 1, files
    blob = files[0].read_bytes()
    assert token.encode("utf-8") not in blob, "the refresh token is on disk in clear"
    record = json.loads(blob.decode("utf-8"))
    assert "rt" not in record, record
    assert set(record) == {"v", "kind", "owner", "nonce", "ct", "exp"}
    assert record["kind"] == "session"
    assert token not in json.dumps(record)
    # ...and it still opens for its own handle.
    assert session_store.read(handle) == (token, handle)


def test_no_file_under_the_store_contains_the_token_bytes():
    """Belt and braces: scan the whole directory, including any stray temp file."""
    token = "RT-" + secrets.token_urlsafe(24)
    handle = session_store.mint(token, owner="owner")
    session_store.rotate(handle, token + "-next")
    for path in session_store.store_dir().iterdir():
        assert token.encode("utf-8") not in path.read_bytes(), path


# --- 2. handle binding --------------------------------------------------------


def test_a_record_copied_onto_another_handle_does_not_open():
    """AAD is the handle. Without it, moving the file moves the session."""
    victim_token = "RT-victim"
    victim = session_store.mint(victim_token, owner="owner")
    attacker = secrets.token_urlsafe(32)
    assert session_store.valid_handle(attacker)
    source = session_store._record_path(victim)
    target = session_store._record_path(attacker)
    target.write_bytes(source.read_bytes())
    assert session_store.read(attacker) == ("", "")
    # The victim's own handle is untouched by the failed theft.
    assert session_store.read(victim) == (victim_token, victim)


# --- 3. fixation --------------------------------------------------------------


def test_regression_guard_a_signin_still_never_adopts_a_caller_handle(
    monkeypatch, tmp_path
):
    """Pre-existing behaviour (#2624/#2627), re-asserted against the sealed store.

    This is a REGRESSION GUARD, not evidence for the sealing work: it passed
    before this change too. What is new here is the store it runs against, so it
    proves the rewrite did not reopen the fixation hole -- nothing more.
    """
    planted = secrets.token_urlsafe(32)
    status, doc, _, _ = _drive(
        dict(CODE_EXCHANGE, session_ref=planted),
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a1", "RT1"),
        data_dir=str(tmp_path),
    )
    assert status == 200
    assert doc["session_ref"] != planted
    assert not session_store._record_path(planted).exists()
    assert session_store.read(planted) == ("", "")
    # The minted handle is the only live session.
    assert session_store.read(doc["session_ref"])[0] == "RT1"
    assert len(_records()) == 1


def test_a_refresh_that_succeeded_off_the_cookie_does_not_adopt_the_handle(
    monkeypatch, tmp_path
):
    """The 2026-08-28 correction, restated against the new store: the presented
    handle resolved to nothing, so the rotated token must not land under it."""
    planted = secrets.token_urlsafe(32)
    status, doc, _, _ = _drive(
        {"grant_type": "refresh_token", "session_ref": planted},
        cookie="ta_rt=COOKIE-RT",
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a1", "RT-NEW"),
        data_dir=str(tmp_path),
    )
    assert status == 200
    assert doc["session_ref"] != planted
    assert not session_store._record_path(planted).exists()


# --- 4. rotation and grace ----------------------------------------------------


def test_a_refresh_rotates_the_handle_and_the_old_one_lives_only_in_grace(
    monkeypatch, tmp_path
):
    dd = str(tmp_path)
    _, doc, _, _ = _drive(
        CODE_EXCHANGE,
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a1", "RT1"),
        data_dir=dd,
    )
    first = doc["session_ref"]
    assert len(_records()) == 1

    status, doc, _, calls = _drive(
        {"grant_type": "refresh_token", "session_ref": first},
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a2", "RT2"),
        data_dir=dd,
    )
    assert status == 200
    assert calls[0]["refresh_token"] == "RT1"
    second = doc["session_ref"]
    assert second != first, "the handle must not survive a rotation unchanged"

    assert session_store.read(second) == ("RT2", second)
    # The old handle follows ONE hop during grace -- and resolves to the SUCCESSOR
    # handle, so the next rotation starts from the live record, not the stale one.
    assert session_store.read(first) == ("RT2", second)
    # Exactly two records: the grace tombstone and the live one. A third would
    # mean the grace read minted instead of following.
    assert len(_records()) == 2

    later = time.time() + session_store.GRACE_SECONDS + 1
    monkeypatch.setattr(session_store.time, "time", lambda: later)
    assert session_store.read(first) == ("", "")
    assert session_store.read(second) == ("RT2", second)


def test_the_old_handle_is_dead_at_the_endpoint_once_grace_expires(
    monkeypatch, tmp_path
):
    """Past grace, presenting the old handle must not renew anyone's session."""
    dd = str(tmp_path)
    _, doc, _, _ = _drive(
        CODE_EXCHANGE,
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a1", "RT1"),
        data_dir=dd,
    )
    first = doc["session_ref"]
    _drive(
        {"grant_type": "refresh_token", "session_ref": first},
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a2", "RT2"),
        data_dir=dd,
    )
    later = time.time() + session_store.GRACE_SECONDS + 1
    monkeypatch.setattr(session_store.time, "time", lambda: later)

    def _boom(_form):
        raise AssertionError("a dead handle must not reach AuthKit")

    status, doc, _, calls = _drive(
        {"grant_type": "refresh_token", "session_ref": first},
        monkeypatch=monkeypatch,
        upstream=_boom,
        data_dir=dd,
    )
    assert (status, doc, calls) == (401, {"error": "no_refresh_token"}, [])


def test_logout_kills_the_successor_too(monkeypatch, tmp_path):
    """Otherwise signing out leaves the CURRENT session renewable."""
    dd = str(tmp_path)
    _, doc, _, _ = _drive(
        CODE_EXCHANGE,
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a1", "RT1"),
        data_dir=dd,
    )
    first = doc["session_ref"]
    _, doc, _, _ = _drive(
        {"grant_type": "refresh_token", "session_ref": first},
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a2", "RT2"),
        data_dir=dd,
    )
    second = doc["session_ref"]
    # A tab that never saw the rotation signs out with the OLD handle.
    _drive(
        {"grant_type": "logout", "session_ref": first},
        monkeypatch=monkeypatch,
        upstream=lambda f: httpx.Response(500),
        data_dir=dd,
    )
    assert session_store.read(first) == ("", "")
    assert session_store.read(second) == ("", "")
    assert _records() == []


def test_logout_revokes_only_that_independent_session_chain():
    """One device signing out must not silently sign out another device."""
    first = session_store.mint("RT-device-one", owner="owner")
    first_successor = session_store.rotate(first, "RT-device-one-rotated")
    second = session_store.mint("RT-device-two", owner="owner")

    session_store.drop(first)

    assert session_store.read(first) == ("", "")
    assert session_store.read(first_successor) == ("", "")
    assert session_store.read(second) == ("RT-device-two", second)


def test_authkit_returning_no_new_token_reuses_the_current_handle_without_writing(
    monkeypatch, tmp_path
):
    dd = str(tmp_path)
    _, doc, _, _ = _drive(
        CODE_EXCHANGE,
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a1", "RT1"),
        data_dir=dd,
    )
    first = doc["session_ref"]
    before = session_store._record_path(first).read_bytes()
    status, doc, _, _ = _drive(
        {"grant_type": "refresh_token", "session_ref": first},
        monkeypatch=monkeypatch,
        upstream=lambda f: httpx.Response(
            200, json={"access_token": "a2", "expires_in": 300}
        ),
        data_dir=dd,
    )
    assert (status, doc["session_ref"]) == (200, first)
    assert session_store._record_path(first).read_bytes() == before
    assert len(_records()) == 1


# --- 5. key hygiene -----------------------------------------------------------


def test_the_key_is_scrubbed_at_IMPORT_before_any_store_call(monkeypatch):
    """Import scrubs the configured seal key before any child can inherit it."""
    import importlib

    from tinyassets.providers import base as provider_base

    raw = base64.urlsafe_b64encode(b"K" * 32).decode("ascii")
    monkeypatch.setenv(session_store.SEAL_KEY_ENV, raw)
    monkeypatch.delenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", raising=False)
    session_store._reset_for_tests()
    assert os.environ.get(session_store.SEAL_KEY_ENV) == raw

    importlib.reload(session_store)  # the import IS the arming event

    assert session_store._key == b"K" * 32, "the key must be loaded by import alone"
    assert session_store.SEAL_KEY_ENV not in os.environ
    child_env = provider_base.subprocess_env_without_api_keys()
    assert isinstance(child_env, dict), "api-key providers must be disabled here"
    assert session_store.SEAL_KEY_ENV not in child_env
    # ...and it is the CONFIGURED key, not a fresh ephemeral that happens to work.
    handle = session_store.mint("RT-configured", owner="owner")
    assert "RT-configured" not in session_store._record_path(handle).read_text()
    session_store._key = None
    monkeypatch.setenv(session_store.SEAL_KEY_ENV, raw)
    assert session_store.read(handle) == ("RT-configured", handle)


def test_arm_is_idempotent_and_callable_before_the_routes_exist(monkeypatch):
    """The daemon calls `arm()` first thing in main(); the onboarding route is
    flag-gated, so a dark deployment must still scrub the environment."""
    raw = base64.urlsafe_b64encode(b"A" * 32).decode("ascii")
    monkeypatch.setenv(session_store.SEAL_KEY_ENV, raw)
    session_store._reset_for_tests()
    session_store.arm()
    assert session_store.SEAL_KEY_ENV not in os.environ
    first = session_store.seal_key()
    session_store.arm()
    assert session_store.seal_key() is first


def test_a_hex_seal_key_is_accepted_too(monkeypatch):
    monkeypatch.setenv(session_store.SEAL_KEY_ENV, ("ab" * 32))
    assert session_store.seal_key() == bytes.fromhex("ab" * 32)
    assert session_store.SEAL_KEY_ENV not in os.environ


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-key",
        "$(whoami)" + "A" * 34,  # a `$` used to survive the lenient decoder
        "A" * 42,  # too short
        "A" * 44,  # 44 unpadded chars = 33 bytes
        "A" * 41 + "+/",  # standard-base64 alphabet, not base64url
        "A" * 40 + "+/=",
        "ab" * 31,  # 62 hex chars
        "zz" * 32,  # 64 chars, not hex
        " ",
    ],
)
def test_a_key_that_is_not_exactly_32_bytes_of_base64url_or_hex_is_rejected(bad):
    """Lenient parsing accepted junk and turned it into a key nobody chose."""
    assert session_store._decode_key(bad) is None


def test_a_malformed_configured_key_fails_the_daemon_loudly(monkeypatch):
    """Hard Rule 8. Falling back to an ephemeral key here would look healthy
    while silently logging every user out on each restart."""
    import importlib

    monkeypatch.setenv(session_store.SEAL_KEY_ENV, "not-a-key")
    session_store._reset_for_tests()
    with pytest.raises(RuntimeError, match=session_store.SEAL_KEY_ENV):
        importlib.reload(session_store)
    # Rejected is not the same as retained: the value is popped before the parse.
    assert session_store.SEAL_KEY_ENV not in os.environ
    importlib.reload(session_store)  # leave a working module for the next test
    assert session_store._key is not None


# --- 6. ephemeral key ---------------------------------------------------------


def test_an_unconfigured_key_warns_once_and_still_round_trips(monkeypatch, caplog):
    monkeypatch.delenv(session_store.SEAL_KEY_ENV, raising=False)
    with caplog.at_level(logging.WARNING, logger="tinyassets.onboarding"):
        handle = session_store.mint("RT-ephemeral", owner="owner")
        assert session_store.read(handle) == ("RT-ephemeral", handle)
        session_store.rotate(handle, "RT-ephemeral-2")
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and session_store.SEAL_KEY_ENV in r.getMessage()
    ]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    assert "restart" in warnings[0].getMessage()


def test_records_sealed_with_a_lost_ephemeral_key_are_unreadable(monkeypatch):
    """The honest cost of no configured key: a restart ends every session.

    Not a silent degradation to plaintext, and not a crash -- the handle simply
    stops resolving and the app re-logs-in. What is left behind is sealed and no
    longer addressable (the filename digest is keyed too); it ages out at its TTL.
    """
    handle = session_store.mint("RT-ephemeral", owner="owner")
    session_store._reset_for_tests()  # a new process = a new ephemeral key
    assert session_store.read(handle) == ("", "")
    orphans = _records()
    assert len(orphans) == 1
    assert b"RT-ephemeral" not in orphans[0].read_bytes()


# --- 7. legacy plaintext store ------------------------------------------------


def test_the_legacy_plaintext_store_is_destroyed_never_migrated(tmp_path, caplog):
    from tinyassets.storage import data_dir

    legacy = data_dir() / "app_refresh_sessions"
    legacy.mkdir(parents=True, exist_ok=True)
    handle = secrets.token_urlsafe(32)
    (legacy / f"{handle}.json").write_text(
        json.dumps({"rt": "RT-LEGACY", "exp": int(time.time()) + 10_000}),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="tinyassets.onboarding"):
        assert session_store.read(handle) == ("", "")
    assert not legacy.exists(), "the plaintext store must be removed, not migrated"
    assert any("legacy plaintext" in r.getMessage() for r in caplog.records)
    # Nothing was carried across.
    assert _records() == []


def test_the_legacy_store_is_gone_before_the_endpoint_can_serve_anyone(
    monkeypatch, tmp_path
):
    from tinyassets.storage import data_dir

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    legacy = data_dir() / "app_refresh_sessions"
    legacy.mkdir(parents=True, exist_ok=True)
    stale = secrets.token_urlsafe(32)
    (legacy / f"{stale}.json").write_text(
        json.dumps({"rt": "RT-LEGACY", "exp": int(time.time()) + 10_000}),
        encoding="utf-8",
    )

    def _boom(_form):
        raise AssertionError("a legacy handle must not reach AuthKit")

    status, doc, _, calls = _drive(
        {"grant_type": "refresh_token", "session_ref": stale},
        monkeypatch=monkeypatch,
        upstream=_boom,
        data_dir=str(tmp_path),
    )
    assert (status, doc, calls) == (401, {"error": "no_refresh_token"}, [])
    assert not legacy.exists()


# --- the store is where the endpoint actually reads and writes -----------------


def test_the_endpoint_has_no_write_under_a_caller_handle_primitive():
    """`_write_refresh_session(handle, token)` was the bug's shape. It must not
    exist for a future caller to reach for."""
    assert not hasattr(onboarding, "_write_refresh_session")
    import pathlib

    src = pathlib.Path(onboarding.__file__).read_text(encoding="utf-8")
    assert "_write_refresh_session" not in src


def test_the_endpoint_rotates_from_the_store_handle_not_the_request_body():
    import pathlib

    src = pathlib.Path(onboarding.__file__).read_text(encoding="utf-8")
    guard = src.split("may_reuse_handle = ")[1].split("\n")[0]
    assert "current_handle" in guard and "session_ref" not in guard, guard
    assert "_session_store.rotate(current_handle, refresh_token)" in src


# --- 8. the rotation tombstone names no live credential -----------------------


def test_a_rotation_never_writes_the_successor_handle_in_plaintext():
    """The round-2 ship blocker.

    The tombstone used to carry `superseded_by: <new handle>` OUTSIDE the
    ciphertext and live until the session's 7-day `exp`. A data-dir reader could
    harvest those values and POST the newest one to /app/token: the original
    cross-user disclosure, delivered as directly usable bearer handles. The
    successor now lives inside the seal, under the OLD handle's AAD.
    """
    first = session_store.mint("RT1", owner="owner")
    second = session_store.rotate(first, "RT2")
    assert second and second != first

    for path in session_store.store_dir().iterdir():
        blob = path.read_bytes()
        assert second.encode("ascii") not in blob, f"successor handle in clear: {path}"
        assert first.encode("ascii") not in blob, f"handle in clear: {path}"

    record = json.loads(session_store._record_path(first).read_text(encoding="utf-8"))
    assert set(record) == {"v", "kind", "owner", "nonce", "ct", "exp"}
    assert record["kind"] == "tombstone"
    # Nothing handle-shaped anywhere in the plaintext fields.
    for key, value in record.items():
        if key in ("nonce", "ct"):
            continue
        assert not session_store.valid_handle(str(value)), (key, value)


def test_the_tombstone_expires_at_the_end_of_grace_not_at_the_session_ttl():
    """It used to inherit the 7-day `exp`, so a dead handle's record sat on disk
    for a week. Its `exp` IS the grace deadline now, so the sweep removes it."""
    now = int(time.time())
    first = session_store.mint("RT1", owner="owner")
    session_store.rotate(first, "RT2")
    tomb = json.loads(session_store._record_path(first).read_text(encoding="utf-8"))
    assert now <= tomb["exp"] <= now + session_store.GRACE_SECONDS + 2
    assert tomb["exp"] < now + session_store.REFRESH_SESSION_TTL


def test_a_tombstone_does_not_open_under_another_handle():
    """AAD binds the successor to the OLD handle: moving the tombstone file does
    not move the pointer."""
    first = session_store.mint("RT1", owner="owner")
    session_store.rotate(first, "RT2")
    attacker = secrets.token_urlsafe(32)
    session_store._record_path(attacker).write_bytes(
        session_store._record_path(first).read_bytes()
    )
    assert session_store.read(attacker) == ("", "")


def test_a_record_relabelled_to_the_other_kind_fails_closed():
    """`kind` is the one plaintext field left, so it is bound into the AAD.

    The direction that matters is tombstone -> session: without the kind in the
    AAD that record decrypts fine and `read` hands back the tombstone's payload
    -- i.e. the successor handle -- as if it were a refresh token. (The reverse
    relabel fails on the payload format alone, so on its own it proves nothing
    about the AAD; it is asserted second only as a completeness check.)
    """
    first = session_store.mint("RT1", owner="owner")
    second = session_store.rotate(first, "RT2")
    tomb_path = session_store._record_path(first)
    tomb = json.loads(tomb_path.read_text(encoding="utf-8"))
    assert tomb["kind"] == "tombstone"
    tomb["kind"] = "session"
    tomb_path.write_text(json.dumps(tomb), encoding="utf-8")

    token, handle = session_store.read(first)
    assert (token, handle) == ("", "")
    assert second not in token, "the successor handle leaked out as a refresh token"

    session_path = session_store._record_path(second)
    record = json.loads(session_path.read_text(encoding="utf-8"))
    record["kind"] = "tombstone"
    session_path.write_text(json.dumps(record), encoding="utf-8")
    assert session_store.read(second) == ("", "")


def test_two_rotations_inside_grace_kill_the_oldest_handle():
    """One hop, never a chain: the oldest handle lands on a tombstone and stops."""
    first = session_store.mint("RT1", owner="owner")
    second = session_store.rotate(first, "RT2")
    third = session_store.rotate(second, "RT3")
    assert session_store.read(first) == ("", "")
    assert session_store.read(second) == ("RT3", third)
    assert session_store.read(third) == ("RT3", third)


# --- 9. a store that cannot be made safe refuses to serve ---------------------


def test_a_legacy_store_that_cannot_be_deleted_returns_503_and_retries(
    monkeypatch, tmp_path
):
    """Adaptation C: never memoize a deletion that did not happen.

    Marking the directory initialised before the rmtree would turn one failed
    deletion into a permanent silent skip -- the plaintext store would stay on
    disk and every later request would sail past it, which is the disclosure this
    module exists to end.
    """
    import shutil

    from tinyassets.storage import data_dir

    legacy = data_dir() / "app_refresh_sessions"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "old.json").write_text(
        json.dumps({"rt": "RT-LEGACY", "exp": int(time.time()) + 10_000}),
        encoding="utf-8",
    )
    real_rmtree = shutil.rmtree

    def _refuse(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(shutil, "rmtree", _refuse)

    def _boom(_form):
        raise AssertionError("must not reach AuthKit with an unsafe store")

    status, doc, cookies, calls = _drive(
        CODE_EXCHANGE,
        monkeypatch=monkeypatch,
        upstream=_boom,
        data_dir=str(tmp_path),
    )
    assert (status, doc, cookies, calls) == (
        503,
        {"error": "session_store_unavailable"},
        [],
        [],
    )
    assert legacy.exists(), "the plaintext store must still be there to retry"

    monkeypatch.setattr(shutil, "rmtree", real_rmtree)
    status, doc, _, _ = _drive(
        CODE_EXCHANGE,
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a1", "RT1"),
        data_dir=str(tmp_path),
    )
    assert status == 200 and len(doc["session_ref"]) == 43
    assert not legacy.exists(), "the retry must actually happen, not be memoized away"


# --- 10. tampering, garbage, and the clock boundary ---------------------------


def test_a_tampered_ciphertext_is_refused_and_the_record_removed():
    handle = session_store.mint("RT-x", owner="owner")
    path = session_store._record_path(handle)
    record = json.loads(path.read_text(encoding="utf-8"))
    raw = bytearray(base64.urlsafe_b64decode(record["ct"]))
    raw[0] ^= 0x01
    record["ct"] = base64.urlsafe_b64encode(bytes(raw)).decode("ascii")
    path.write_text(json.dumps(record), encoding="utf-8")
    assert session_store.read(handle) == ("", "")
    assert not path.exists(), "a record that can never open must not be left behind"


def test_a_truncated_record_is_refused_and_removed():
    handle = session_store.mint("RT-x", owner="owner")
    path = session_store._record_path(handle)
    path.write_bytes(b'{"v": 1, "kind": "sess')
    assert session_store.read(handle) == ("", "")
    assert not path.exists()


def test_a_record_whose_json_is_not_an_object_is_refused():
    handle = session_store.mint("RT-x", owner="owner")
    path = session_store._record_path(handle)
    path.write_text('["not", "an", "object"]', encoding="utf-8")
    assert session_store.read(handle) == ("", "")


def test_the_expiry_boundary_is_inclusive_of_now(monkeypatch):
    """`exp == now` is still live; `exp == now - 1` is gone. An off-by-one here
    either logs people out a second early or serves a second past the deadline."""
    handle = session_store.mint("RT-x", owner="owner")
    path = session_store._record_path(handle)
    record = json.loads(path.read_text(encoding="utf-8"))
    now = int(time.time())
    monkeypatch.setattr(session_store.time, "time", lambda: float(now))

    record["exp"] = now
    path.write_text(json.dumps(record), encoding="utf-8")
    assert session_store.read(handle) == ("RT-x", handle)

    record["exp"] = now - 1
    path.write_text(json.dumps(record), encoding="utf-8")
    assert session_store.read(handle) == ("", "")
    assert not path.exists()


def test_a_refresh_that_loses_the_rotation_race_does_not_damage_the_winner(
    monkeypatch, tmp_path
):
    """The race is DOCUMENTED, not fixed -- and it is not this store's to fix.

    Two requests that read the same handle before either rotates both present the
    SAME refresh token. AuthKit refresh tokens are single-use, so it accepts one
    and refuses the other; the loser gets 401 `refresh_failed` and its page
    renews again or re-logs-in. That is pre-existing behaviour shared with the
    HttpOnly-cookie path, and only AuthKit knows which token it burned.

    What MUST hold is that the loser's failure costs the winner nothing: no
    cleared cookie, no dropped record, no dead handle.
    """
    dd = str(tmp_path)
    _, doc, _, _ = _drive(
        CODE_EXCHANGE,
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a1", "RT1"),
        data_dir=dd,
    )
    first = doc["session_ref"]
    _, doc, _, _ = _drive(
        {"grant_type": "refresh_token", "session_ref": first},
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a2", "RT2"),
        data_dir=dd,
    )
    winner = doc["session_ref"]

    status, doc, cookies, _ = _drive(
        {"grant_type": "refresh_token", "session_ref": first},
        monkeypatch=monkeypatch,
        upstream=lambda f: httpx.Response(400, json={"error": "invalid_grant"}),
        data_dir=dd,
    )
    assert (status, doc) == (401, {"error": "refresh_failed"})
    assert cookies == [], "a lost race must not clear the winner's cookie"

    assert session_store.read(winner) == ("RT2", winner)
    status, doc, _, calls = _drive(
        {"grant_type": "refresh_token", "session_ref": winner},
        monkeypatch=monkeypatch,
        upstream=lambda f: _ok("a3", "RT3"),
        data_dir=dd,
    )
    assert status == 200 and calls[0]["refresh_token"] == "RT2"


# --- 11. the daemon startup path ----------------------------------------------


def test_the_daemon_arms_the_seal_as_the_first_thing_main_does():
    """Ordering inside one function, so this reads the source of that function.

    A source test is weak evidence in general -- it cannot notice a runtime
    substitution. It is the right shape HERE because the property is positional:
    `arm()` must precede every other statement in `main()`, and the only way to
    observe that at runtime would be to boot the daemon. The runtime half of this
    property is covered by
    `test_the_key_is_scrubbed_at_IMPORT_before_any_store_call`, which proves the
    import alone arms the store; this asserts the daemon reaches that import
    before it starts a thread, builds the app, or hands off to uvicorn.
    """
    import inspect

    from tinyassets import universe_server

    body = inspect.getsource(universe_server.main)
    arm_at = body.index("_session_seal.arm()")
    for later in ("prepare_children", "uvicorn", "create_streamable_http_app", "logger.info"):
        assert arm_at < body.index(later), (
            f"{later!r} runs before the seal key is scrubbed from os.environ; "
            "anything spawned in that window inherits the key"
        )


def test_the_onboarding_package_cannot_be_imported_without_arming():
    """The route is flag-gated but the module is not: importing the package that
    serves the token endpoint is itself the arming event."""
    import pathlib

    src = pathlib.Path(onboarding.__file__).read_text(encoding="utf-8")
    assert "from tinyassets.onboarding import session_store as _session_store" in src
    store_src = pathlib.Path(session_store.__file__).read_text(encoding="utf-8")
    assert store_src.rstrip().endswith("arm()"), (
        "arm() must be the last module-level statement, so importing the module "
        "loads the key and scrubs the environment"
    )


# --- 9. authenticated deadlines, malformed metadata, continuous sweep (round 3) --


def test_editing_the_outer_exp_cannot_extend_a_session(monkeypatch):
    """The plaintext `exp` is a sweep hint. The deadline that admits a record is
    sealed with it; pushing the hint ten years out buys an attacker with write
    access to the data dir nothing once the sealed deadline has passed."""
    handle = session_store.mint("RT-live", owner="owner")
    path = session_store._record_path(handle)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["exp"] = int(time.time()) + 10 * 365 * 24 * 3600
    path.write_text(json.dumps(record), encoding="utf-8")
    later = time.time() + session_store.REFRESH_SESSION_TTL + 1
    monkeypatch.setattr(session_store.time, "time", lambda: later)
    assert session_store.read(handle) == ("", "")
    assert not path.exists()


def test_editing_the_outer_exp_cannot_extend_a_tombstone(monkeypatch):
    old = session_store.mint("RT-1", owner="owner")
    new = session_store.rotate(old, "RT-2")
    path = session_store._record_path(old)
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["kind"] == "tombstone"
    record["exp"] = int(time.time()) + 10 * 365 * 24 * 3600
    path.write_text(json.dumps(record), encoding="utf-8")
    later = time.time() + session_store.GRACE_SECONDS + 1
    monkeypatch.setattr(session_store.time, "time", lambda: later)
    assert session_store.read(old) == ("", "")
    assert session_store.read(new) == ("RT-2", new)


@pytest.mark.parametrize(
    "field,value",
    [("exp", "not-an-int"), ("exp", None), ("exp", 1.5), ("exp", True), ("v", "1"), ("v", None)],
)
def test_malformed_metadata_fails_closed_instead_of_raising(field, value):
    """A record whose metadata is not exactly what we wrote is not ours: it is
    deleted and the read returns nothing -- never a ValueError that turns one bad
    file into a standing 500 on the token endpoint."""
    handle = session_store.mint("RT-m", owner="owner")
    path = session_store._record_path(handle)
    record = json.loads(path.read_text(encoding="utf-8"))
    record[field] = value
    path.write_text(json.dumps(record), encoding="utf-8")
    assert session_store.read(handle) == ("", "")
    assert not path.exists()


def test_expired_tombstones_are_swept_without_a_restart(monkeypatch):
    old = session_store.mint("RT-1", owner="owner")
    session_store.rotate(old, "RT-2")
    tomb = session_store._record_path(old)
    assert tomb.exists()
    later = time.time() + session_store.GRACE_SECONDS + session_store._SWEEP_INTERVAL + 1
    monkeypatch.setattr(session_store.time, "time", lambda: later)
    session_store.mint("RT-3", owner="owner")  # any store use after the interval sweeps
    assert not tomb.exists()


def test_a_real_child_process_does_not_inherit_the_key(monkeypatch):
    """Not a dictionary inspection: an actual child process, spawned with the env
    the provider layer would hand it, reports the key absent."""
    import subprocess
    import sys

    monkeypatch.setenv(
        session_store.SEAL_KEY_ENV,
        base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    )
    session_store._reset_for_tests()
    session_store.arm()
    from tinyassets.providers import base as provider_base

    env = provider_base.subprocess_env_without_api_keys() or os.environ.copy()
    out = subprocess.run(
        [sys.executable, "-c", "import os; print('TINYASSETS_SESSION_SEAL_KEY' in os.environ)"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert out.stdout.strip() == "False", out
