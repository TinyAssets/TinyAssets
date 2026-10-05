"""The platform refreshes a deposited subscription document before launch.

The live defect these pin (founder's subscription, every turn since 2026-09-24):
the launch copy is a throwaway, the CLI's refresh rotated a single-use refresh
token inside it, the rotation was deleted with the copy, and every later launch
replayed a spent token. Each test drives the real caller -- the shared refresh
core, the real cleanup path, the real vault materializer, the real router loop --
rather than the helper it happens to call.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import threading
import time
from unittest.mock import patch

import pytest

from tests.test_provider_served_router import _RecordingProvider, _served_context
from tinyassets.exceptions import ProviderAuthenticationError
from tinyassets.providers.model_policy import ModelRef

OWNER = "owner-1"
UID = "u-owner"


def _jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


#: An identity token shaped like the one a real sign-in stores: it names the
#: issuer and the client the credential was issued to, which is where the refresh
#: endpoint comes from. Nothing in the platform names the source.
ID_TOKEN = _jwt({"iss": "https://sign-in.example.net", "client_id": "client-1"})


def _document(
    *, access: str = "a-1", refresh: str = "r-1", last_refresh: str = "",
    id_token: str = ID_TOKEN,
) -> str:
    doc = {
        "auth_mode": "subscription",
        "tokens": {"id_token": id_token, "access_token": access, "refresh_token": refresh},
        # A key no field of this module knows about: a rotation must preserve it,
        # because the launch copy has to stay the document the CLI reads.
        "unknown_future_field": "keep-me",
    }
    if last_refresh:
        doc["last_refresh"] = last_refresh
    return base64.b64encode(json.dumps(doc).encode("utf-8")).decode("ascii")


def _deposit(universe_dir, encoded: str, *, owner: str = OWNER, uid: str = UID) -> None:
    from tinyassets.credential_vault import write_credential_vault

    write_credential_vault(
        universe_dir,
        [{
            "credential_type": "llm_subscription",
            "service": "codex",
            "auth_json_b64": encoded,
        }],
        owner_user_id=owner,
        universe_id=uid,
    )


def _stored_tokens(universe_dir) -> dict:
    from tinyassets.credential_vault import load_credential_vault

    record = next(
        r for r in load_credential_vault(universe_dir)
        if r.get("credential_type") == "llm_subscription"
    )
    raw = base64.b64decode(record["auth_json_b64"])
    return json.loads(raw.decode("utf-8"))


def _endpoint_from_the_credential(monkeypatch):
    """Let the issuer named BY the credential publish its own token endpoint.

    The platform holds no endpoint for any source: it reads the issuer off the
    stored identity token and asks that issuer's RFC 8414 metadata. Patched at
    the discovery seam so the resolution under test is the real one.
    """
    from tinyassets.connection_oauth import discovery

    seen: list[str] = []

    def metadata(issuer):
        seen.append(issuer)
        return discovery.ServerMetadata(
            issuer=issuer,
            authorization_endpoint=f"{issuer}/authorize",
            token_endpoint=f"{issuer}/token",
            registration_endpoint="",
            iss_parameter_supported=False,
            scopes_supported=None,
            code_challenge_methods=("S256",),
            grant_types=("refresh_token",),
            response_types=("code",),
        )

    monkeypatch.setattr(discovery, "fetch_server_metadata", metadata)
    return seen


def _universe(tmp_path):
    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    _deposit(universe_dir, _document(last_refresh="2020-01-01T00:00:00Z"))
    return universe_dir


# --------------------------------------------------------------------------- #
# 1. Single-flight: concurrent launches spend the refresh token ONCE.
# --------------------------------------------------------------------------- #


def test_concurrent_launches_spend_the_refresh_token_once(tmp_path, monkeypatch):
    """The live bug's mechanism: two launches, one single-use refresh token.

    Both threads enter with the SAME stale stored document, so both would have
    spent it. The winner writes the rotation; the loser's re-read inside the locks
    sees a fresh document and does not spend anything.
    """
    from tinyassets import subscription_refresh

    universe_dir = _universe(tmp_path)
    spent: list[str] = []
    barrier = threading.Barrier(2)

    def fake_spend(document, *, token_url, client_id):
        spent.append(document.refresh_token)
        # Hold inside the spend so the second thread is definitely waiting on the
        # lock rather than merely losing a race by arriving late.
        time.sleep(0.3)
        return subscription_refresh._rebuild(
            document, access_token="a-2", refresh_token="r-2", id_token="i-2",
        )

    monkeypatch.setattr(subscription_refresh, "_spend", fake_spend)
    results: dict[int, object] = {}

    def run(index):
        barrier.wait()
        try:
            results[index] = subscription_refresh.refresh_before_launch(
                universe_dir=universe_dir, service="codex",
                owner_user_id=OWNER, universe_id=UID,
                token_url="https://auth.example.net/token", client_id="c-1",
            )
        except BaseException as exc:  # noqa: BLE001 - reported as the result
            results[index] = exc

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    assert spent == ["r-1"], "the single-use refresh token was spent more than once"
    assert sorted(bool(r) for r in results.values()) == [False, True]
    tokens = _stored_tokens(universe_dir)["tokens"]
    assert tokens == {"id_token": "i-2", "access_token": "a-2", "refresh_token": "r-2"}
    # The cross-process lock is on the path, asserted by its ARTEFACT. Measured:
    # deleting either lock leaves this test green, because the vault's exclusive
    # admission already serializes two threads and the re-read above catches the
    # loser. The locks' own job is keeping that BOUNDED admission uncontended, and
    # a same-process test cannot see it -- so the lock file's existence is what is
    # pinned here rather than a concurrency effect this rig cannot observe.
    from tinyassets.credential_refresh import lock_directory

    assert list(lock_directory(universe_dir).glob("*.lock")), "no refresh lock was taken"


def test_a_rotation_preserves_every_other_key_of_the_stored_document(tmp_path, monkeypatch):
    from tinyassets import subscription_refresh

    universe_dir = _universe(tmp_path)
    monkeypatch.setattr(
        subscription_refresh, "_spend",
        lambda d, **_: subscription_refresh._rebuild(
            d, access_token="a-2", refresh_token="r-2", id_token=""),
    )
    assert subscription_refresh.refresh_before_launch(
        universe_dir=universe_dir, service="codex", owner_user_id=OWNER, universe_id=UID,
        token_url="https://auth.example.net/token", client_id="c-1",
    )
    document = _stored_tokens(universe_dir)
    assert document["unknown_future_field"] == "keep-me"
    assert document["auth_mode"] == "subscription"
    # A source that rotates nothing leaves the old identity token valid, so it is
    # kept rather than blanked.
    assert document["tokens"]["id_token"] == ID_TOKEN
    assert document["last_refresh"] != "2020-01-01T00:00:00Z"


# --------------------------------------------------------------------------- #
# 2. Freshness: what is refreshed, and what is left alone.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("stamp,access,expected", [
    # Past the platform's age threshold -> refreshed.
    ("2020-01-01T00:00:00Z", "opaque", True),
    # Stamped just now -> left alone, so an ordinary turn does no network work.
    ("", "opaque", False),
    # Expiring access token -> refreshed whatever the stamp says.
    ("", "expiring", True),
    # Neither a readable expiry nor a readable stamp -> NOT refreshed. Spending a
    # single-use refresh token on a guess is the one irreversible move here.
    ("not-a-date", "opaque", False),
])
def test_only_a_document_provably_stale_is_refreshed(
    tmp_path, monkeypatch, stamp, access, expected,
):
    from tinyassets import subscription_refresh

    now = time.time()
    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    _deposit(universe_dir, _document(
        access=_jwt({"exp": now + 10}) if access == "expiring" else "opaque",
        last_refresh=stamp or time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now)),
    ))
    calls: list[str] = []

    def fake_spend(document, **_):
        calls.append(document.refresh_token)
        return subscription_refresh._rebuild(
            document, access_token="a-2", refresh_token="r-2", id_token="")

    monkeypatch.setattr(subscription_refresh, "_spend", fake_spend)
    rotated = subscription_refresh.refresh_before_launch(
        universe_dir=universe_dir, service="codex", owner_user_id=OWNER, universe_id=UID,
        token_url="https://auth.example.net/token", client_id="c-1", now=now,
    )
    assert rotated is expected and bool(calls) is expected


def test_the_refresh_is_spent_where_the_credential_says_it_was_issued(tmp_path, monkeypatch):
    """No source's endpoint is compiled into the platform.

    The issuer and client come off the stored identity token and the token
    endpoint off that issuer's own metadata, so the substrate carries no
    per-source knowledge (`scripts/check_channel_agnostic.py` measures this) and a
    credential already deposited still refreshes -- which a field added at deposit
    time would not.
    """
    from tinyassets import subscription_refresh

    universe_dir = _universe(tmp_path)
    seen = _endpoint_from_the_credential(monkeypatch)
    asked: list[tuple[str, str]] = []

    def fake_spend(document, *, token_url, client_id):
        asked.append((token_url, client_id))
        return subscription_refresh._rebuild(
            document, access_token="a-2", refresh_token="r-2", id_token="")

    monkeypatch.setattr(subscription_refresh, "_spend", fake_spend)
    assert subscription_refresh.refresh_before_launch(
        universe_dir=universe_dir, service="codex", owner_user_id=OWNER, universe_id=UID,
    ) is True
    assert seen == ["https://sign-in.example.net"]
    assert asked == [("https://sign-in.example.net/token", "client-1")]


def test_a_credential_that_does_not_say_where_it_came_from_is_not_refreshed(
    tmp_path, monkeypatch,
):
    """Spending a single-use refresh token against a guessed URL is not done."""
    from tinyassets import subscription_refresh

    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    _deposit(universe_dir, _document(
        id_token="not-a-jwt", last_refresh="2020-01-01T00:00:00Z"))
    _endpoint_from_the_credential(monkeypatch)
    monkeypatch.setattr(subscription_refresh, "_spend", lambda *a, **k: pytest.fail(
        "nothing may be spent without an issuer the credential names"))
    assert subscription_refresh.refresh_before_launch(
        universe_dir=universe_dir, service="codex", owner_user_id=OWNER, universe_id=UID,
    ) is False


def test_a_path_backed_record_is_not_migrated_by_a_refresh(tmp_path, monkeypatch):
    """Writing the document inline would clear the record's path fields."""
    from tinyassets import subscription_refresh
    from tinyassets.credential_vault import load_credential_vault, write_credential_vault

    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    home = universe_dir / "codex-auth"
    home.mkdir()
    (home / "auth.json").write_bytes(json.dumps({
        "tokens": {"access_token": "a-1", "refresh_token": "r-1"},
        "last_refresh": "2020-01-01T00:00:00Z",
    }).encode())
    write_credential_vault(
        universe_dir,
        [{"credential_type": "llm_subscription", "service": "codex", "codex_home": str(home)}],
        owner_user_id=OWNER, universe_id=UID,
    )
    monkeypatch.setattr(subscription_refresh, "_spend", lambda *a, **k: pytest.fail(
        "a path-backed record must not be refreshed into an inline one"))
    assert subscription_refresh.refresh_before_launch(
        universe_dir=universe_dir, service="codex", owner_user_id=OWNER, universe_id=UID,
        token_url="https://auth.example.net/token", client_id="c-1",
    ) is False
    record = next(
        r for r in load_credential_vault(universe_dir)
        if r.get("credential_type") == "llm_subscription"
    )
    assert record.get("codex_home") == str(home) and "auth_json_b64" not in record


# --------------------------------------------------------------------------- #
# 3. A rotation made DURING a run is persisted, not deleted with the copy.
# --------------------------------------------------------------------------- #


def _materialize(universe_dir):
    """The real materializer, so the home under test is the one launches read."""
    from tinyassets.credential_vault import ensure_codex_home_from_vault

    home = ensure_codex_home_from_vault(universe_dir)
    assert home is not None
    return home


def _deposit_with_home(universe_dir, encoded: str):
    """A record that NAMES its home, which is the launch-writable shape."""
    from tinyassets.credential_vault import write_credential_vault

    home = universe_dir / "sign-in-home"
    home.mkdir(exist_ok=True)
    from tinyassets.credential_vault import llm_subscription_credential_record

    write_credential_vault(
        universe_dir,
        [{
            **llm_subscription_credential_record(
                service="codex", auth_json_b64=encoded),
            "codex_home": str(home),
        }],
        owner_user_id=OWNER, universe_id=UID,
    )
    return home


def test_a_rotation_the_cli_made_on_disk_is_adopted_into_the_vault(tmp_path):
    """The other half of removing the stale overwrite.

    Leaving the newer document only on disk means the platform refresh, which
    reads the VAULT, spends a refresh token that document has already replaced.
    """
    from tinyassets.subscription_refresh import adopt_newer_on_disk_document

    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    _deposit_with_home(universe_dir, _document(last_refresh="2026-09-01T00:00:00Z"))
    home = _materialize(universe_dir)
    (home / "auth.json").write_text(json.dumps({
        "auth_mode": "subscription",
        "tokens": {"id_token": "i-9", "access_token": "a-9", "refresh_token": "r-9"},
        "last_refresh": "2026-09-20T00:00:00Z",
    }), encoding="utf-8")

    assert adopt_newer_on_disk_document(
        universe_dir=universe_dir, service="codex", owner_user_id=None, universe_id=UID,
    ) is True
    assert _stored_tokens(universe_dir)["tokens"]["refresh_token"] == "r-9"


@pytest.mark.parametrize("disk_stamp,reason", [
    ("2026-08-01T00:00:00Z", "an OLDER on-disk document must not replace the vault's"),
    ("", "an unstamped document is never preferred over a stamped one"),
])
def test_an_older_or_unstamped_on_disk_document_is_not_adopted(tmp_path, disk_stamp, reason):
    from tinyassets.subscription_refresh import adopt_newer_on_disk_document

    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    _deposit_with_home(
        universe_dir, _document(refresh="r-vault", last_refresh="2026-09-01T00:00:00Z"))
    home = _materialize(universe_dir)
    document = {"tokens": {"access_token": "a-9", "refresh_token": "r-disk"}}
    if disk_stamp:
        document["last_refresh"] = disk_stamp
    (home / "auth.json").write_text(json.dumps(document), encoding="utf-8")

    assert adopt_newer_on_disk_document(
        universe_dir=universe_dir, service="codex", owner_user_id=None, universe_id=UID,
    ) is False, reason
    assert _stored_tokens(universe_dir)["tokens"]["refresh_token"] == "r-vault"


def test_the_sealed_launch_copy_cannot_hold_a_rotation(tmp_path):
    """Why there is no recovery hook on the cleanup path: the copy is read-only.

    Pinned as a test because the absence of that hook is a DECISION, and this is
    the fact it rests on. If a future change makes the copy writable, this goes
    red and the discard is back on the table.
    """
    import sqlite3

    from tinyassets.credential_vault import (
        adopt_llm_subscription_custody,
        cleanup_llm_credential_snapshot,
        snapshot_llm_subscription_credential,
    )
    from tinyassets.storage import db_path

    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    _deposit(universe_dir, _document())
    conn = sqlite3.connect(db_path(tmp_path), isolation_level=None)
    try:
        conn.execute("BEGIN")
        custody = adopt_llm_subscription_custody(
            conn, universe_dir=universe_dir, owner_user_id=OWNER,
            universe_id=UID, service="codex",
        )
        conn.commit()
    finally:
        conn.close()
    snapshot = snapshot_llm_subscription_credential(
        universe_dir=universe_dir, custody=custody,
    )
    with pytest.raises((PermissionError, OSError)):
        (snapshot.directory / "auth.json").write_bytes(b"{}")
    cleanup_llm_credential_snapshot(snapshot)
    assert not snapshot.directory.exists()


# --------------------------------------------------------------------------- #
# 4. A stale vault copy never overwrites a newer on-disk document.
# --------------------------------------------------------------------------- #


def test_the_vault_never_overwrites_a_provably_newer_on_disk_document(tmp_path):
    """`ensure_codex_home_from_vault` is the real caller; it wrote the OLD bytes.

    It had no notion of which document was newer, so a document the CLI had
    refreshed was replaced by the older vault copy and the next launch replayed a
    spent refresh token. "Provably" is the whole rule: see the companion test for
    why an unstamped document cannot be allowed to win.
    """
    from tinyassets.credential_vault import ensure_codex_home_from_vault

    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    _deposit(universe_dir, _document(
        access="stale", refresh="r-stale", last_refresh="2026-09-01T00:00:00Z"))

    home = ensure_codex_home_from_vault(universe_dir)
    assert home is not None
    auth_file = home / "auth.json"
    assert json.loads(auth_file.read_text())["tokens"]["refresh_token"] == "r-stale"

    # What the CLI writes after refreshing: new tokens AND a new stamp.
    auth_file.write_text(json.dumps({
        "tokens": {"access_token": "a-new", "refresh_token": "r-new"},
        "last_refresh": "2026-09-20T00:00:00Z",
    }), encoding="utf-8")

    assert ensure_codex_home_from_vault(universe_dir) == home
    assert json.loads(auth_file.read_text())["tokens"]["refresh_token"] == "r-new"


def test_an_owners_redeposit_still_reaches_the_materialized_home(tmp_path):
    """The other side of the same rule, and why it is not "never overwrite".

    An owner re-depositing IS a rotation and must land on disk. An unstamped
    on-disk document therefore never wins -- otherwise a re-deposit would be
    ignored in favour of whatever the CLI happened to leave.
    """
    from tinyassets.credential_vault import ensure_codex_home_from_vault

    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    _deposit(universe_dir, _document(refresh="r-first", last_refresh="2026-09-01T00:00:00Z"))
    home = ensure_codex_home_from_vault(universe_dir)
    assert home is not None
    # An unstamped document on disk: not evidence of anything, so it loses.
    (home / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "a-x", "refresh_token": "r-unstamped"}}),
        encoding="utf-8",
    )
    _deposit(universe_dir, _document(refresh="r-second", last_refresh="2026-09-25T00:00:00Z"))

    assert ensure_codex_home_from_vault(universe_dir) == home
    stored = json.loads((home / "auth.json").read_text())
    assert stored["tokens"]["refresh_token"] == "r-second"


# --------------------------------------------------------------------------- #
# 5. A finished sign-in falls back within the turn instead of stopping it.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("excerpt,terminal", [
    ("stream error: refresh token was already used", True),
    ("token request failed: invalid_grant", True),
    ("you are not logged in; run codex login", True),
    ("stream disconnected before completion", False),
    ("the model produced no output", False),
    # NOT terminal despite naming auth: an ordinary failure that mentions an auth
    # header must not become a request for the owner to sign in again.
    ("request failed: missing authorization header on a proxied call", False),
])
def test_a_finished_signin_is_told_apart_from_an_outage(excerpt, terminal):
    from tinyassets.providers.codex_provider import _terminal_auth_failure

    assert _terminal_auth_failure(excerpt) is terminal


def test_the_interactive_served_launch_refreshes_before_it_pins_anything(tmp_path, monkeypatch):
    """Codex refute-review, P1 #7: this is THE path the outage is on.

    Wiring only the workflow lanes left the founder's own served turn never
    calling the refresh at all, so the P0 was untouched. Asserted by driving the
    real `authorize_served_provider_call_async` and recording the ORDER: the
    refresh must land before custody is resolved and before the credential is
    snapshotted, because both pin the record digest a refresh moves.
    """
    import asyncio

    from tinyassets import provider_assignment
    from tinyassets.auth.middleware import revoke_provider_request

    _, _, capability, context = _served_context(tmp_path)
    order: list[str] = []

    def refreshed(**kwargs):
        order.append(f"refresh:{kwargs.get('launching')}")

    def snapshotted(**_kwargs):
        order.append("snapshot")
        raise PermissionError("stop here; the ordering is what is under test")

    # Patched at their DEFINING modules: both are function-local imports in
    # `provider_assignment`, so the name is bound from the source at call time and
    # patching the caller's namespace would silently do nothing.
    from tinyassets import credential_vault, subscription_refresh

    monkeypatch.setattr(
        subscription_refresh, "refresh_deposited_subscriptions", refreshed, raising=True,
    )
    monkeypatch.setattr(
        credential_vault, "snapshot_llm_subscription_credential", snapshotted, raising=True,
    )

    async def drive():
        manager = provider_assignment.authorize_served_provider_call_async(
            tmp_path, universe_dir=context.universe_dir,
            request_carrier=context.provider_request, role="writer",
            operation="converse", model_selection=ModelRef("codex", ""),
        )
        with contextlib.suppress(Exception):
            async with manager:
                pass

    try:
        asyncio.run(drive())
    finally:
        revoke_provider_request(capability)

    assert order, "the served launch path never called the refresh"
    assert order[0] == "refresh:codex", (
        "the refresh must run FIRST and name the source this call selected; got "
        f"{order}"
    )


def test_an_unrecognized_selection_refreshes_nothing(tmp_path, monkeypatch):
    """An existing test caught this: the selection must not steer a refresh.

    `test_async_unaccepted_selection_refused_before_discovery` passes a bare
    object to pin that an unaccepted selection is refused before discovery. Reading
    a field off it turned that refusal into an AttributeError -- and the deeper
    point is that the selection decides WHICH source is refreshed and which one may
    fail the launch, so an unvalidated one must steer neither.
    """
    import asyncio

    from tinyassets import provider_assignment, subscription_refresh
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import ProviderAuthorityHeldError

    _, _, capability, context = _served_context(tmp_path)
    monkeypatch.setattr(
        subscription_refresh, "refresh_deposited_subscriptions",
        lambda **_k: pytest.fail("nothing may be refreshed for an unrecognized selection"),
    )

    async def drive():
        manager = provider_assignment.authorize_served_provider_call_async(
            tmp_path, universe_dir=context.universe_dir,
            request_carrier=context.provider_request, role="writer",
            operation="converse", model_selection=object(),
        )
        async with manager:
            pass

    try:
        with pytest.raises(ProviderAuthorityHeldError):
            asyncio.run(drive())
    finally:
        revoke_provider_request(capability)


def test_a_finished_signin_is_not_a_provider_outage(tmp_path):
    """The router must not buy the source a cooldown for a finished sign-in.

    A plain `ProviderUnavailableError` (what the launch path raised) cools the
    source for 120s, so the owner's NEXT turn skipped it too. Typed as a sign-in
    failure it is marked for reconnect instead, and the attempt is classified so
    the turn coordinator can act on it.
    """
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import AllProvidersExhaustedError
    from tinyassets.providers.router import ProviderRouter

    _, _, capability, context = _served_context(tmp_path)

    class Refusing(_RecordingProvider):
        async def complete(self, *args, **kwargs):
            self.calls += 1
            raise ProviderAuthenticationError(
                "the stored sign-in is no longer accepted: "
                "refresh token was already used"
            )

    refusing = Refusing("codex")
    router = ProviderRouter(providers={"codex": refusing})
    try:
        with patch.object(router._quota, "cooldown") as cooldown:
            with pytest.raises(AllProvidersExhaustedError) as caught:
                asyncio.run(router.call(
                    "writer", "hello", "system", universe_context=context,
                    operation="converse",
                ))
        assert refusing.calls == 1
        assert cooldown.call_count == 0, "a finished sign-in is not a provider outage"
        assert caught.value.failure_class == "auth_invalid"
        assert [a.skip_class for a in caught.value.attempts] == ["auth_invalid"]
    finally:
        revoke_provider_request(capability)


class _Selection:
    """The minimum a coordinator candidate is, for the advance decision."""

    def __init__(self, connection_id):
        self.connection_id = connection_id

    def __eq__(self, other):
        return getattr(other, "connection_id", None) == self.connection_id

    def __hash__(self):
        return hash(self.connection_id)


def _coordinator(candidate, *, execution_kind="engine_inference"):
    """A real AgentTurnCoordinator with only its collaborators stubbed.

    The method under test is the real one: what is replaced is the plan that says
    which model is next and the journal-backed turn, neither of which decides
    whether a sign-in failure may advance.
    """
    from types import SimpleNamespace

    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator

    coordinator = AgentTurnCoordinator.__new__(AgentTurnCoordinator)
    coordinator.plan = SimpleNamespace(order=lambda owner, uid, exhaustion: SimpleNamespace(
        candidates=() if candidate is None else (SimpleNamespace(ref=candidate),),
    ))
    coordinator._budget_skipped = set()
    coordinator.capacity_recovery = False
    coordinator.capacity_switch = None
    coordinator._text_only = False
    coordinator.adapter = SimpleNamespace(has_candidate_order=False)
    coordinator.owner = OWNER
    from dataclasses import make_dataclass
    from pathlib import Path

    # A dataclass, because the real method carries the selection forward with
    # `dataclasses.replace`: a namespace would make the test pass for a method
    # that never rebuilt the context.
    context_type = make_dataclass("_Context", ["universe_dir", "model_selection"])
    coordinator.context = context_type(Path("base") / UID, _Selection("codex"))
    coordinator.turn = SimpleNamespace(state="ready")
    coordinator.visited = set()
    coordinator.exhaustion = ()
    coordinator.spent_attempts = []
    coordinator.execution_kind = execution_kind
    coordinator.retrying_capacity = False
    coordinator.free_sibling_retries = 0
    return coordinator


def _exhausted(*, failure_class="auth_invalid", side_effect_state="none"):
    from tinyassets.exceptions import AllProvidersExhaustedError
    from tinyassets.providers.router import ProviderAttemptDiagnostic

    attempt = ProviderAttemptDiagnostic(
        provider="codex", status="failed", skip_class=failure_class,
        detail="Provider reported a sign-in failure", failure_class=failure_class,
        side_effect_state=side_effect_state,
    )
    return AllProvidersExhaustedError(
        "served provider exhausted", attempts=[attempt], failure_class=failure_class,
    )


def test_a_finished_signin_advances_the_turn_to_the_next_allowed_model(tmp_path):
    """The live consequence: the turn answers instead of stopping.

    Only a CAPACITY exhaustion advanced the turn, so a finished sign-in ended it.
    The advance goes through the same `_next_candidate`, so it can only reach a
    model already in the owner's accepted order -- it never widens authority.
    """
    coordinator = _coordinator(_Selection("api_key_http:other"))
    assert coordinator._next_after_signin(_exhausted()) is True
    assert coordinator.context.model_selection == _Selection("api_key_http:other")
    # The dead source is excluded for the rest of the turn, account-wide: a
    # finished sign-in is the whole connection's, never one model's.
    assert [e.scope for e in coordinator.exhaustion] == ["account"]
    assert len(coordinator.spent_attempts) == 1


def test_the_turn_does_not_advance_when_no_other_allowed_model_remains(tmp_path):
    coordinator = _coordinator(None)
    assert coordinator._next_after_signin(_exhausted()) is False


def test_the_turn_does_not_advance_past_a_round_that_may_have_acted(tmp_path):
    """A native round that may have committed must not be replayed elsewhere."""
    coordinator = _coordinator(_Selection("api_key_http:other"))
    assert coordinator._next_after_signin(
        _exhausted(side_effect_state="committed")) is False


@pytest.mark.parametrize("failure_class", ["provider_error", "rate_limited", None])
def test_only_a_signin_failure_advances_on_this_path(failure_class):
    """Every other class keeps its existing owner: this path must not absorb it."""
    coordinator = _coordinator(_Selection("api_key_http:other"))
    assert coordinator._next_after_signin(
        _exhausted(failure_class=failure_class)) is False


# --------------------------------------------------------------------------- #
# 6. No secret in any result, log or exception.
# --------------------------------------------------------------------------- #


def test_no_token_reaches_a_result_log_or_exception(tmp_path, monkeypatch, caplog):
    from tinyassets import subscription_refresh
    from tinyassets.credential_refresh import RefreshRejected

    secrets = ("r-1", "a-1", ID_TOKEN)
    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    _deposit(universe_dir, _document(last_refresh="2020-01-01T00:00:00Z"))

    def refusing(document, **_):
        assert document.refresh_token == "r-1"  # it WAS read; it must not be echoed
        raise RefreshRejected("the stored sign-in is no longer accepted; sign in again")

    monkeypatch.setattr(subscription_refresh, "_spend", refusing)
    _endpoint_from_the_credential(monkeypatch)
    with caplog.at_level("DEBUG"):
        with pytest.raises(ProviderAuthenticationError) as caught:
            subscription_refresh.refresh_deposited_subscriptions(
                base_path=tmp_path, universe_dir=universe_dir,
                owner_user_id=OWNER, universe_id=UID, launching="codex",
            )
    rendered = f"{caught.value!r} {caught.value} {caplog.text}"
    for secret in secrets:
        assert secret not in rendered
    # The owner is still told which source, and what to do about it.
    assert "codex" in str(caught.value) and "sign in again" in str(caught.value)


def test_another_sources_dead_signin_does_not_fail_this_launch(tmp_path, monkeypatch):
    """Codex refute-review, P1 #2: the owner has more than one source on purpose.

    Raising for ANY deposited source let a second, unrelated dead credential kill
    a launch that was never going to use it. Only the launching source may.
    """
    from tinyassets import subscription_refresh
    from tinyassets.credential_refresh import RefreshRejected
    from tinyassets.credential_vault import write_credential_vault

    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    write_credential_vault(
        universe_dir,
        [
            {"credential_type": "llm_subscription", "service": "codex",
             "auth_json_b64": _document(refresh="r-dead", last_refresh="2020-01-01T00:00:00Z")},
            {"credential_type": "llm_subscription", "service": "claude",
             "auth_json_b64": _document(refresh="r-live", last_refresh="2020-01-01T00:00:00Z")},
        ],
        owner_user_id=OWNER, universe_id=UID,
    )
    _endpoint_from_the_credential(monkeypatch)
    asked: list[str] = []

    def refusing(document, **_):
        asked.append(document.refresh_token)
        if document.refresh_token == "r-dead":
            raise RefreshRejected("the stored sign-in is no longer accepted; sign in again")
        return subscription_refresh._rebuild(
            document, access_token="a-2", refresh_token="r-live", id_token="")

    monkeypatch.setattr(subscription_refresh, "_spend", refusing)

    # The launch is using the OTHER, healthy source: the dead one must not stop it.
    subscription_refresh.refresh_deposited_subscriptions(
        base_path=tmp_path, universe_dir=universe_dir,
        owner_user_id=OWNER, universe_id=UID, launching="claude",
    )
    # Both were still attempted -- a stale document costs whoever launches next a
    # turn, so refreshing it is right; only the RAISE is scoped.
    assert sorted(asked) == ["r-dead", "r-live"]

    # ...and when the launch IS that source, it does raise.
    with pytest.raises(ProviderAuthenticationError):
        subscription_refresh.refresh_deposited_subscriptions(
            base_path=tmp_path, universe_dir=universe_dir,
            owner_user_id=OWNER, universe_id=UID, launching="codex",
        )


def test_the_refresh_token_is_sent_through_the_hardened_transport(tmp_path, monkeypatch):
    """Codex refute-review, P1 #1: the endpoint comes from an UNSIGNED token.

    A plain client would have been a token-exfiltration path. The refresh must go
    through the same SSRF-hardened, secret-scrubbing transport the `oauth2`
    connection refresh uses.
    """
    from tinyassets.connection_oauth import transport
    from tinyassets.subscription_refresh import _Document, _spend

    seen: dict = {}

    def request_json(method, url, *, form=None, json_body=None, secrets=()):
        seen.update(method=method, url=url, form=form, secrets=secrets)
        return 200, {"access_token": "a-2", "refresh_token": "r-2"}

    monkeypatch.setattr(transport, "request_json", request_json)
    document = _Document(
        text=json.dumps({"tokens": {"access_token": "a-1", "refresh_token": "r-1"}}),
        access_token="a-1", refresh_token="r-1", id_token="i-1",
        last_refresh="", expires_at=None,
    )
    _spend(document, token_url="https://sign-in.example.net/token", client_id="client-1")

    assert seen["method"] == "POST"
    assert seen["url"] == "https://sign-in.example.net/token"
    assert seen["form"]["grant_type"] == "refresh_token"
    # Every value this request carries is declared as a secret, so the transport
    # scrubs it out of any detail it produces.
    assert set(seen["secrets"]) == {"r-1", "a-1", "i-1"}


def test_a_non_https_endpoint_is_refused_before_anything_is_spent(monkeypatch):
    from tinyassets.connection_oauth import transport
    from tinyassets.credential_refresh import RefreshUnavailable
    from tinyassets.subscription_refresh import _Document, _spend

    monkeypatch.setattr(transport, "request_json", lambda *a, **k: pytest.fail(
        "nothing may be sent to a non-https endpoint"))
    document = _Document(
        text="{}", access_token="a-1", refresh_token="r-1", id_token="",
        last_refresh="", expires_at=None,
    )
    with pytest.raises(RefreshUnavailable):
        _spend(document, token_url="http://sign-in.example.net/token", client_id="c-1")


def test_an_unreadable_stored_document_is_never_echoed(tmp_path):
    """Codex refute-review, P1 #6: `from None` clears __cause__, NOT __context__.

    A `JSONDecodeError`'s `doc` attribute is the WHOLE credential document, so a
    raise inside the handler hands every token in it to anything that walks the
    context chain. My previous version of this test read only the context's
    `repr`, which omits `.doc` -- so it passed while the leak was real. It now
    walks the whole chain and reads every attribute of every link.
    """
    from tinyassets.credential_refresh import RefreshError
    from tinyassets.subscription_refresh import _parse

    blob = base64.b64encode(b'{"tokens": {"access_token": "s3cr3t-in-broken-json"').decode()
    with pytest.raises(RefreshError) as caught:
        _parse(blob)

    def links(exc):
        seen = []
        while exc is not None and exc not in seen:
            seen.append(exc)
            exc = exc.__cause__ or exc.__context__
        return seen

    chain = links(caught.value)
    assert len(chain) == 1, "no exception carrying the document may remain in the chain"
    rendered = " ".join(
        f"{link!r} {link} " + " ".join(
            str(getattr(link, name, "")) for name in ("doc", "object", "args", "detail")
        )
        for link in chain
    )
    assert "s3cr3t" not in rendered


def test_the_endpoint_comes_from_the_document_read_under_the_locks(tmp_path, monkeypatch):
    """Codex refute-review, P1 #2: credential and endpoint must share one read.

    Resolving the endpoint BEFORE the locks and capturing it let this through:
    read credential A, the owner deposits credential B from a different issuer,
    the locks are taken and B is re-read -- and B's refresh token went to A's
    endpoint.
    """
    from tinyassets import subscription_refresh

    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    first = _jwt({"iss": "https://issuer-a.example.net", "client_id": "client-a"})
    second = _jwt({"iss": "https://issuer-b.example.net", "client_id": "client-b"})
    _deposit(universe_dir, _document(
        refresh="r-a", id_token=first, last_refresh="2020-01-01T00:00:00Z"))
    _endpoint_from_the_credential(monkeypatch)
    sent: list[tuple[str, str, str]] = []

    def fake_spend(document, *, token_url, client_id):
        sent.append((document.refresh_token, token_url, client_id))
        return subscription_refresh._rebuild(
            document, access_token="a-2", refresh_token="r-2", id_token="")

    monkeypatch.setattr(subscription_refresh, "_spend", fake_spend)

    # The record is replaced by a DIFFERENT issuer's credential after the first
    # read, which is the read the endpoint used to be resolved from.
    original_read = subscription_refresh._stored
    swapped = {"done": False}

    def swapping_read(universe, key):
        result = original_read(universe, key)
        if not swapped["done"]:
            swapped["done"] = True
            _deposit(universe_dir, _document(
                refresh="r-b", id_token=second, last_refresh="2020-01-01T00:00:00Z"))
        return result

    monkeypatch.setattr(subscription_refresh, "_stored", swapping_read)
    subscription_refresh.refresh_before_launch(
        universe_dir=universe_dir, service="codex", owner_user_id=OWNER, universe_id=UID,
    )
    assert sent, "nothing was spent at all"
    token, url, client = sent[-1]
    assert (token, url, client) == (
        "r-b", "https://issuer-b.example.net/token", "client-b",
    ), "a credential was spent at another issuer's endpoint"


def test_an_unsaveable_rotation_is_terminal_not_transient(tmp_path, monkeypatch):
    """Codex refute-review, P1 #2: the token HAS been spent by then.

    Reported as transient this read as "try later" about a credential that is
    already gone -- the source rotated it and the replacement was not stored, so
    what is in the vault is dead and only signing in again fixes it.
    """
    from tinyassets import credential_refresh
    from tinyassets.credential_refresh import RefreshRejected, refresh_credential

    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    _deposit(universe_dir, _document())
    spent: list[str] = []

    real_hold = credential_refresh._hold_vault

    def failing_hold(universe, deadline, subject):
        stack, _ = real_hold(universe, deadline, subject)

        def refuse(*_a, **_k):
            raise OSError("storage is gone")

        return stack, refuse

    monkeypatch.setattr(credential_refresh, "_hold_vault", failing_hold)
    with pytest.raises(RefreshRejected) as caught:
        refresh_credential(
            universe_dir=universe_dir, lock_id="llm_subscription::codex",
            owner_user_id=OWNER, universe_id=UID,
            read=lambda: "stored", stale=lambda _current: True,
            spend=lambda _current: spent.append("once") or "rotated",
            records=lambda _fresh: [{
                "credential_type": "llm_subscription", "service": "codex",
                "auth_json_b64": _document(),
            }],
            wait_seconds=0.2,
        )
    assert spent == ["once"]
    assert "sign in again" in str(caught.value)


def test_the_two_newest_wins_comparators_agree_on_a_fresh_deposit(tmp_path):
    """Codex refute-review, P1 #4: adoption must not undo a re-deposit.

    A newly deposited document with no internal stamp, whose RECORD is stamped
    today, against a yesterday-stamped document on disk. The materializer prefers
    the deposit via the record fallback; adoption must reach the same answer, or
    it restores yesterday's credential over today's.
    """
    from tinyassets.credential_vault import ensure_codex_home_from_vault
    from tinyassets.subscription_refresh import adopt_newer_on_disk_document

    universe_dir = tmp_path / UID
    universe_dir.mkdir()
    home = _deposit_with_home(universe_dir, _document(refresh="r-old", last_refresh=""))
    (home / "auth.json").write_text(json.dumps({
        "tokens": {"access_token": "a-y", "refresh_token": "r-yesterday"},
        "last_refresh": "2020-01-01T00:00:00Z",
    }), encoding="utf-8")
    # The record's own deposited_at is now, so the deposit is the newer of the two.
    _deposit_with_home(universe_dir, _document(refresh="r-new", last_refresh=""))

    assert adopt_newer_on_disk_document(
        universe_dir=universe_dir, service="codex", owner_user_id=None, universe_id=UID,
    ) is False, "adoption restored an older document over a fresh deposit"
    assert _stored_tokens(universe_dir)["tokens"]["refresh_token"] == "r-new"
    ensure_codex_home_from_vault(universe_dir)
    assert json.loads((home / "auth.json").read_text())["tokens"]["refresh_token"] == "r-new"
