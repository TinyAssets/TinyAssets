"""Founder-home resolution and first-contact universe conversation.

`get_status` is a pure read. Omitted-scope authenticated `converse` resolves or
atomically creates the founder's home, loads its soul, and returns its own voice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity
from tinyassets.ids import is_universe_serial

_RESERVED = {"wiki", "output", "runs", "lance"}


class _StaticAuthProvider(AuthProvider):
    """Resolve-always provider (like WorkOS): anon reads, authed founder writes."""

    def __init__(self, identity: Identity | None) -> None:
        self.identity = identity

    def resolve_token(self, token: str) -> Identity | None:
        return self.identity if token == "ok" else None

    def is_auth_required(self) -> bool:
        return False

    def resolve_always_writes(self) -> bool:
        return True

    def register_client(self, metadata: dict) -> dict:
        return {"client_id": "t", **metadata}

    def create_authorization(self, *a, **k) -> str:  # noqa: ANN002, ANN003
        return "c"

    def exchange_code(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        return None


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch) -> Path:
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    return base


@pytest.fixture(autouse=True)
def _reset_auth():
    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _login(sub: str = "founder-1", caps: list[str] | None = None) -> None:
    ident = Identity(
        user_id=sub, username=sub,
        capabilities=caps or ["read", "write", "costly", "submit_request", "list"],
    )
    set_provider(_StaticAuthProvider(ident))
    auth_middleware("ok")


def _universe_dirs(base: Path) -> list[Path]:
    return [
        p for p in base.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in _RESERVED
    ]


def _serial_dirs(base: Path) -> list[Path]:
    return [p for p in _universe_dirs(base) if is_universe_serial(p.name)]


def _create_via_action(base, monkeypatch):
    """Create a universe the explicit way (ledgered MCP route)."""
    from tinyassets.api import universe as universe_api

    monkeypatch.setattr(universe_api, "_base_path", lambda: base)
    out = json.loads(universe_api._universe_impl(action="create_universe"))
    assert out.get("error") is None, out
    return out["universe_id"]


def _capture_universe_reply(monkeypatch, reply: str) -> dict:
    """Stub only the external engine while keeping real persona assembly."""
    import tinyassets.universe_intelligence as intelligence

    captured: dict = {}

    def fake_call_provider(prompt, system="", *, role="writer", **kwargs):
        if "strict JSON" in system:
            return "{}"
        captured.update(
            prompt=prompt,
            system=system,
            role=role,
            universe_context=kwargs.get("universe_context"),
        )
        return reply

    monkeypatch.setattr(intelligence, "call_provider", fake_call_provider)
    return captured



def _own_universes_as(actor_id: str = "user_01TESTOWNER") -> None:
    """Authenticate, because a universe must now belong to someone.

    Creation used to succeed for an anonymous caller and return `founder_id: ""` —
    a universe nobody owned. The founder's rule (2026-08-28) forbids that state, and
    it is enforced in `_action_create_universe` since 2026-08-29, so a test that
    creates a universe has to say who it belongs to.
    """
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import AuthProvider, Identity

    identity = Identity(
        user_id=actor_id,
        username=actor_id,
        capabilities=[
            "tinyassets.universe.read",
            "tinyassets.universe.write",
            "tinyassets.universe.admin",
            "tinyassets.universe.create",
            # create_universe is a COSTLY action — it provisions storage — so the
            # scope gate wants this one too. Omitting it produced a confusing
            # "Missing OAuth scope" rather than an ownership refusal.
            "tinyassets.universe.costly",
        ],
    )

    class _Static(AuthProvider):
        def resolve_token(self, token: str):
            return identity if token == "ok" else None

        def is_auth_required(self) -> bool:
            return True

        def register_client(self, metadata: dict) -> dict:
            return {"client_id": "test-client", **metadata}

        def create_authorization(self, *_a, **_kw) -> str:
            raise NotImplementedError

        def exchange_code(self, *_a, **_kw) -> dict:
            raise NotImplementedError

    set_provider(_Static())
    auth_middleware("ok")


def _assert_reads_as_outage_not_onboarding(out: dict) -> None:
    """The guarantee these tests exist for, asserted directly.

    They used to look for the router's "All providers exhausted" text. #2756 /
    #2758 deliberately stopped surfacing it: the router says "exhausted" for
    every all-attempts-failed reason there is, and the founder went and checked
    his Codex usage over a turn that had actually hit a platform bug
    (``universe_server.py``, ``_MISLEADING_ROUTER_TELLS``). In these fixtures no
    provider is even TRIED -- every attempt is ``status="skipped"`` -- so there
    is no exhaustion to report and the vague notice is the honest one; a
    separate guard in ``test_a_failed_turn_says_what_actually_happened.py``
    requires it for this shape. What must still hold is the distinguishability
    BUG-038/039 needs: the turn is NOT parked as onboarding, and an owner who
    already has a credential is never sent to attach one.
    """
    assert out.get("status") != "held", out
    assert "reply" not in out, out
    assert out.get("reason") != "setup_required", out
    assert "setup_paths" not in out, out
    assert out.get("error"), out

def test_get_status_without_home_is_repeatable_and_side_effect_free(
    data_dir, monkeypatch,
):
    from tinyassets.api.status import get_status
    from tinyassets.daemon_server import get_founder_home

    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    (data_dir / "release-state.json").write_text(
        json.dumps({"git_sha": "candidate-sha"}),
        encoding="utf-8",
    )
    _login("founder-1")
    first = json.loads(get_status())
    second = json.loads(get_status())

    assert first == second
    assert first["first_contact"]["event"] == "no_universe_yet"
    assert first["active_host"] == {
        "host_id": "host",
        "served_llm_type": "any",
        "llm_endpoint_bound": "ollama",
        "api_key_providers_enabled": False,
    }
    assert first["release_state"]["git_sha"] == "candidate-sha"
    assert get_founder_home(data_dir, "founder-1") == ""
    assert _serial_dirs(data_dir) == []


def test_get_status_does_not_first_create_or_mutate_new_universe_soul(
    data_dir, monkeypatch
):
    from tinyassets.api.status import get_status

    _login("founder-1")
    uid = _create_via_action(data_dir, monkeypatch)
    udir = data_dir / uid
    soul_paths = sorted(
        p for p in udir.rglob("*")
        if p.is_file() and (p.suffix == ".md" or "soul_versions" in p.parts)
    )
    assert soul_paths
    assert (udir / "soul.md").is_file()  # creation, not status, seeded it
    before = {p.relative_to(udir): p.read_bytes() for p in soul_paths}

    first = json.loads(get_status(universe_id=uid))
    second = json.loads(get_status(universe_id=uid))
    after = {p.relative_to(udir): p.read_bytes() for p in soul_paths}

    assert first["universe_id"] == uid
    assert second["universe_id"] == uid
    assert after == before


def test_first_connect_new_founder_births_seed_and_returns_universe_voice(
    data_dir, monkeypatch
):
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.universe_server import converse

    _login("founder-1")
    captured = _capture_universe_reply(
        monkeypatch,
        "I am here. I do not have a name yet—who are you?",
    )

    out = json.loads(converse(message="Hello"))

    assert out["reply"].startswith("I ")
    uid = out["universe_id"]
    assert is_universe_serial(uid)
    assert get_founder_home(data_dir, "founder-1") == uid
    assert (data_dir / uid / "soul.md").is_file()
    assert "first person" in captured["system"].lower()
    assert "name yet" in captured["system"].lower()
    assert not (data_dir / ".active_universe").exists()


def test_first_connect_existing_founder_loads_learned_home_voice(
    data_dir, monkeypatch
):
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.soul_edit import apply_soul_edit
    from tinyassets.universe_server import converse

    _login("founder-1")
    uid = _create_via_action(data_dir, monkeypatch)
    apply_soul_edit(
        data_dir / uid,
        changes={"identity.md": "# Identity\n\nI am Aetheria.\n"},
        agent_id="main", source="founder conversation",
        context="The founder named the universe Aetheria.",
        name="Aetheria",
    )
    captured = _capture_universe_reply(monkeypatch, "I am Aetheria. Welcome back.")

    out = json.loads(converse(message="Hello again"))

    assert out == {"reply": "I am Aetheria. Welcome back.", "universe_id": uid}
    assert get_founder_home(data_dir, "founder-1") == uid
    assert len(_serial_dirs(data_dir)) == 1
    assert "You are Aetheria." in captured["system"]


def test_first_connect_without_a_principal_cannot_birth_or_reach_universe(data_dir):
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.universe_server import converse

    auth_middleware(None)
    out = json.loads(converse(message="Hello"))

    assert out.get("auth_required") is True
    assert "reply" not in out
    assert get_founder_home(data_dir, "unbound") == ""
    assert _serial_dirs(data_dir) == []


def test_first_connect_cannot_target_another_founders_universe(
    data_dir, monkeypatch
):
    from tinyassets.universe_server import converse

    _login("founder-A")
    home_a = _create_via_action(data_dir, monkeypatch)
    _login("founder-B")
    _capture_universe_reply(monkeypatch, "I should never be reached.")

    out = json.loads(converse(message="Hello", graph_id=home_a))

    assert out.get("auth_scope_required") is True
    assert "reply" not in out


def test_founder_home_set_get_roundtrip(data_dir):
    from tinyassets.daemon_server import get_founder_home, set_founder_home

    assert get_founder_home(data_dir, "founder-1") == ""
    set_founder_home(data_dir, founder_sub="founder-1", universe_id="u-01x")
    assert get_founder_home(data_dir, "founder-1") == "u-01x"
    # anonymous / empty never has a home
    assert get_founder_home(data_dir, "anonymous") == ""
    assert get_founder_home(data_dir, "") == ""


def test_claim_founder_home_serializes_single_home(data_dir):
    # The atomic serialization primitive behind concurrent first-contact: the
    # first claim wins; a later claim (a racing worker) gets the already-bound id
    # back — never its own candidate — so no second universe is ever minted.
    from tinyassets.daemon_server import claim_founder_home, get_founder_home
    from tinyassets.ids import new_universe_id

    first, second = new_universe_id(), new_universe_id()
    assert claim_founder_home(data_dir, "founder-1", first) == first
    assert claim_founder_home(data_dir, "founder-1", second) == first  # loser adopts
    assert get_founder_home(data_dir, "founder-1") == first
    # anonymous / empty candidate never claims
    assert claim_founder_home(data_dir, "anonymous", new_universe_id()) == ""
    assert claim_founder_home(data_dir, "founder-1", "") == ""


def test_ensure_founder_home_births_complete_seed(data_dir):
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import get_founder_home

    _login("founder-1")
    uid = ensure_founder_home(data_dir, "founder-1")

    assert is_universe_serial(uid)
    assert get_founder_home(data_dir, "founder-1") == uid
    assert (data_dir / uid / "soul.md").is_file()


def test_auto_birth_is_idempotent(data_dir):
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import get_founder_home

    _login("founder-1")
    first = ensure_founder_home(data_dir, "founder-1")
    ensure_founder_home(data_dir, "founder-1")
    ensure_founder_home(data_dir, "founder-1")
    assert get_founder_home(data_dir, "founder-1") == first
    assert [p.name for p in _serial_dirs(data_dir)] == [first]


def test_auto_birth_is_ledgered(data_dir):
    # The auto-birth create routes through the ledgered dispatch, same as an
    # explicit create — the new universe records a create_universe ledger entry.
    from tinyassets.api.first_contact import ensure_founder_home

    _login("founder-1")
    uid = ensure_founder_home(data_dir, "founder-1")
    ledger = data_dir / uid / "ledger.json"
    assert ledger.is_file()
    entries = json.loads(ledger.read_text(encoding="utf-8"))
    assert any(e.get("action") == "create_universe" for e in entries)


def test_ensure_home_materializes_pending_reserved_id(data_dir):
    # A racing worker reserved the home id (atomic claim) but has not finished
    # creating the dir yet. ensure_founder_home must ADOPT the reserved id and
    # materialize it — never mint a second universe under a fresh id.
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import claim_founder_home
    from tinyassets.ids import new_universe_id

    _login("founder-1")
    reserved = new_universe_id()
    assert claim_founder_home(data_dir, "founder-1", reserved) == reserved
    assert not (data_dir / reserved).is_dir()          # reserved, not yet on disk
    got = ensure_founder_home(data_dir, "founder-1")
    assert got == reserved
    assert (data_dir / reserved / "soul.md").is_file()
    assert [p.name for p in _serial_dirs(data_dir)] == [reserved]


def test_ensure_home_returns_existing_no_double_birth(data_dir):
    from tinyassets.api.first_contact import ensure_founder_home

    _login("founder-1")
    a_home = ensure_founder_home(data_dir, "founder-1")
    b_home = ensure_founder_home(data_dir, "founder-1")   # a racer / a retry
    assert is_universe_serial(a_home)
    assert b_home == a_home
    assert len(_serial_dirs(data_dir)) == 1


def test_concurrent_first_contact_births_single_home(data_dir):
    # Real thread race: N first-contact home resolutions fire at once on
    # a FRESH data dir. Must yield exactly one home with zero errors — this guards
    # both the atomic home claim AND the serialized schema/migration init (a naive
    # version intermittently raised `duplicate column name` / `database is locked`
    # from concurrent initialize_author_server; Codex 2026-07-15 finding).
    import threading

    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import get_founder_home

    ident = Identity(
        user_id="founder-race", username="founder-race",
        capabilities=["read", "write", "costly", "submit_request", "list"],
    )
    provider = _StaticAuthProvider(ident)
    set_provider(provider)

    n = 6
    barrier = threading.Barrier(n)
    results: list[str] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        # Each thread starts with a fresh contextvar context — authenticate it so
        # current_identity() resolves to the founder inside creation.
        set_provider(provider)
        auth_middleware("ok")
        try:
            barrier.wait(timeout=15)          # release all threads together
            out = ensure_founder_home(data_dir, "founder-race")
            with lock:
                results.append(out)
        except Exception as exc:              # capture the race, don't swallow it
            with lock:
                errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []                        # no duplicate-column / db-locked race
    home = get_founder_home(data_dir, "founder-race")
    assert is_universe_serial(home)
    assert len(_serial_dirs(data_dir)) == 1    # exactly ONE universe, never two
    assert set(results) == {home}
    # Exactly ONE create_universe ledger row — materialization is serialized, so
    # racing workers never double-create under the shared reserved id.
    ledger = json.loads((data_dir / home / "ledger.json").read_text(encoding="utf-8"))
    assert len([e for e in ledger if e.get("action") == "create_universe"]) == 1


def test_first_contact_birth_failure_is_graceful(data_dir, monkeypatch):
    # If creation fails after mkdir, conversation entry must not return a broken
    # home. The partial dir is rolled back and the retained binding self-heals.
    from tinyassets.api import universe as universe_api
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import get_founder_home

    real_seed = universe_api.seed_okf_bundle

    def _boom(*a, **k):
        raise OSError("seed failed mid-bundle")

    monkeypatch.setattr(universe_api, "seed_okf_bundle", _boom)

    _login("founder-1")
    assert ensure_founder_home(data_dir, "founder-1") == ""
    assert _serial_dirs(data_dir) == []                         # partial dir rolled back
    # No COMPLETE home exists even if a home id was reserved (self-heals on retry).
    bound = get_founder_home(data_dir, "founder-1")
    if bound:
        assert not (data_dir / bound / "soul.md").is_file()

    # Recovery: restore only the seed; the next conversation entry materializes
    # the home under the retained base path.
    monkeypatch.setattr(universe_api, "seed_okf_bundle", real_seed)
    healed_uid = ensure_founder_home(data_dir, "founder-1")
    assert (data_dir / healed_uid / "soul.md").is_file()


def test_forged_home_binding_cannot_escape_data_root(data_dir, tmp_path, monkeypatch):
    import tinyassets.daemon_server as daemon_server
    from tinyassets.api.first_contact import ensure_founder_home

    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    root_sentinel = data_dir / "keep.txt"
    root_sentinel.write_text("do not delete", encoding="utf-8")

    _login("founder-1")
    monkeypatch.setattr(daemon_server, "get_founder_home", lambda *_: "")
    monkeypatch.setattr(
        daemon_server,
        "claim_founder_home",
        lambda *_: "../outside",
    )

    assert ensure_founder_home(data_dir, "founder-1") == ""
    assert sentinel.read_text(encoding="utf-8") == "do not delete"

    monkeypatch.setattr(daemon_server, "claim_founder_home", lambda *_: ".")
    assert ensure_founder_home(data_dir, "founder-1") == ""
    assert root_sentinel.read_text(encoding="utf-8") == "do not delete"


def test_bound_incomplete_dir_repairs_on_conversation_entry(data_dir):
    # A founder bound to an EXISTING but incomplete home dir (no soul.md — a birth
    # interrupted / not rolled back) must be repaired on conversation entry, not
    # wedged in no_universe_yet forever because create refuses an existing dir
    # (Codex 2026-07-15).
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import get_founder_home, set_founder_home
    from tinyassets.ids import new_universe_id

    _login("founder-1")
    stuck = new_universe_id()
    (data_dir / stuck).mkdir()                  # incomplete: dir exists, no soul.md
    # The interrupted birth was of a PLATFORM-GENERATED serial, so its binding
    # carries the provenance marker — that is what lets first-contact repair it
    # rather than fail closed (universe-creation 5.2).
    set_founder_home(
        data_dir,
        founder_sub="founder-1",
        universe_id=stuck,
        platform_generated=True,
    )

    # First retry repairs it — same bound id, now materialized (not stuck).
    assert ensure_founder_home(data_dir, "founder-1") == stuck
    assert get_founder_home(data_dir, "founder-1") == stuck
    assert (data_dir / stuck / "soul.md").is_file()
    assert len(_serial_dirs(data_dir)) == 1


def test_nobody_bound_first_contact_births_no_home(data_dir):
    from tinyassets.api.status import get_status
    from tinyassets.daemon_server import get_founder_home

    auth_middleware(None)   # the suite signs in by default; this test means nobody
    # Nobody bound (the autouse reset): status refuses outright, so it can
    # neither birth a founder home nor a generated universe. There is no
    # anonymous principal to birth one for (founder, 2026-09-02).
    with pytest.raises(PermissionError, match="Authentication required"):
        get_status()
    assert get_founder_home(data_dir, "anonymous") == ""
    assert _serial_dirs(data_dir) == []


def test_readonly_founder_gets_awaiting_not_birth(data_dir):
    # An authenticated founder whose token lacks create/costly scope must NOT
    # auto-birth a universe via get_status (get_status is not a scope bypass) —
    # they fall back to the compact awaiting card, and no home is bound.
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.api.status import get_status
    from tinyassets.daemon_server import get_founder_home

    _login("reader-1", caps=["read", "submit_request", "list"])
    out = json.loads(get_status())
    assert out["first_contact"]["event"] == "no_universe_yet"
    assert "conversation" in out["next_step_for_user"]
    assert ensure_founder_home(data_dir, "reader-1") == ""
    assert get_founder_home(data_dir, "reader-1") == ""
    assert _serial_dirs(data_dir) == []


def test_two_founders_get_distinct_homes(data_dir):
    # Each founder's first contact auto-births their OWN home; the homes and
    # their ACL/ownership are distinct.
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import get_founder_home

    _login("founder-A")
    home_a = ensure_founder_home(data_dir, "founder-A")
    _login("founder-B")
    home_b = ensure_founder_home(data_dir, "founder-B")

    assert home_a != home_b
    assert get_founder_home(data_dir, "founder-A") == home_a
    assert get_founder_home(data_dir, "founder-B") == home_b
    assert len(_serial_dirs(data_dir)) == 2


def test_founder_auto_birth_does_not_write_active_universe_marker(data_dir):
    # universe-creation spec "First MCP contact": a founder's home birth records
    # their `founder_home` binding but must NOT clobber the host-global
    # `.active_universe` marker (that would leak across founders).
    from tinyassets.api.first_contact import ensure_founder_home

    _login("founder-A")
    ensure_founder_home(data_dir, "founder-A")
    assert not (data_dir / ".active_universe").exists()


def test_read_graph_status_stays_pure_no_birth(data_dir):
    # read_graph target=status is the canonical read-only handle: an authenticated
    # founder with no home reading through it gets the awaiting card and NOTHING
    # is created. The dedicated get_status handle is equally pure; conversation
    # entry owns provisioning.
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.universe_server import read_graph

    _login("founder-1")
    out = json.loads(read_graph(target="status"))
    assert out["first_contact"]["event"] == "no_universe_yet"   # pure: no birth
    assert get_founder_home(data_dir, "founder-1") == ""
    assert _serial_dirs(data_dir) == []
    # The dedicated get_status handle is also pure.
    from tinyassets.api.status import get_status

    status = json.loads(get_status())
    assert status["first_contact"]["event"] == "no_universe_yet"
    assert get_founder_home(data_dir, "founder-1") == ""


def test_no_card_for_nobody_or_explicit_id(data_dir):
    from tinyassets.api.status import get_status

    auth_middleware(None)   # the suite signs in by default; this test means nobody
    # nobody bound: refused, so no card and nothing resolved
    with pytest.raises(PermissionError):
        get_status()
    # explicit universe_id: normal read of that universe, no auto-birth, no card
    _login("founder-1")
    out = json.loads(get_status(universe_id="default-universe"))
    assert "first_contact" not in out


def test_explicit_create_is_ledgered(data_dir, monkeypatch):
    # An explicit create (additional universe) still goes through the ledgered
    # MCP dispatch.
    _login("founder-1")
    uid = _create_via_action(data_dir, monkeypatch)
    ledger = data_dir / uid / "ledger.json"
    assert ledger.is_file()
    entries = json.loads(ledger.read_text(encoding="utf-8"))
    assert any(e.get("action") == "create_universe" for e in entries)


def test_additional_explicit_create_does_not_reassign_home(data_dir, monkeypatch):
    # After the auto-birthed home exists, an EXPLICIT create makes an additional
    # universe but must NOT reassign the founder's home binding.
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import get_founder_home

    _login("founder-1")
    home = ensure_founder_home(data_dir, "founder-1")
    extra = _create_via_action(data_dir, monkeypatch)
    assert extra != home
    assert get_founder_home(data_dir, "founder-1") == home    # unchanged
    assert len(_serial_dirs(data_dir)) == 2


def test_stale_founder_home_rematerializes_same_id_on_conversation_entry(data_dir):
    # If the bound home dir is removed, conversation entry re-materializes a
    # living home under the SAME bound id (stable home identity; race-safe — the
    # atomic claim keeps the existing binding rather than minting a competing id).
    import shutil

    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import get_founder_home

    _login("founder-1")
    home1 = ensure_founder_home(data_dir, "founder-1")
    shutil.rmtree(data_dir / home1)  # binding is now stale (dir gone)

    home2 = ensure_founder_home(data_dir, "founder-1")
    assert home2 == home1                       # same id, freshly re-materialized
    assert (data_dir / home2 / "soul.md").is_file()
    assert get_founder_home(data_dir, "founder-1") == home2
    assert len(_serial_dirs(data_dir)) == 1


def test_omitted_universe_never_leaks_another_founders_home(data_dir, monkeypatch):
    # Codex 2026-07-02 adapt: `universe action=inspect` (and friends) must not
    # fall through to another founder's serial home on an omitted-scope read.
    from tinyassets.api import universe as universe_api

    monkeypatch.setattr(universe_api, "_base_path", lambda: data_dir)
    _login("founder-A")
    home_a = _create_via_action(data_dir, monkeypatch)
    assert is_universe_serial(home_a)

    _login("reader-B", caps=["read", "submit_request", "list"])
    out = json.loads(universe_api._universe_impl(action="inspect"))
    assert out.get("universe_id") != home_a  # never another founder's home


def test_omitted_universe_routes_founder_to_their_home(data_dir, monkeypatch):
    from tinyassets.api import universe as universe_api
    from tinyassets.daemon_server import get_founder_home

    monkeypatch.setattr(universe_api, "_base_path", lambda: data_dir)
    _login("founder-A")
    home_a = _create_via_action(data_dir, monkeypatch)
    assert get_founder_home(data_dir, "founder-A") == home_a

    out = json.loads(universe_api._universe_impl(action="inspect"))
    assert out.get("universe_id") == home_a


def test_authenticated_switch_universe_does_not_write_marker(data_dir):
    from tinyassets.api.universe import (
        _action_create_universe,
        _action_switch_universe,
    )

    _login("founder-A")
    uid = json.loads(_action_create_universe())["universe_id"]

    out = json.loads(_action_switch_universe(universe_id=uid))
    assert out["status"] == "selected"
    assert out.get("scope") == "request"
    assert not (data_dir / ".active_universe").exists()


def test_readonly_founder_omitted_scope_does_not_leak_other_home(data_dir, monkeypatch):
    # Cross-founder leak guard at the resolver: founder A has a home; founder B
    # (authenticated, read-only, no home) resolves with no universe_id and must
    # NOT be routed to A's serial home. The resolver stays pure (needs_birth); the
    # read-only founder is denied auto-birth at the get_status scope gate.
    from tinyassets.api.status import _resolve_entry_universe

    _login("founder-A")
    home_a = _create_via_action(data_dir, monkeypatch)
    assert is_universe_serial(home_a)

    _login("reader-B", caps=["read", "submit_request", "list"])
    resolved_b, needs_birth = _resolve_entry_universe("")
    assert needs_birth is True                 # no home -> get_status handles it
    assert resolved_b != home_a                # no cross-founder leak
    assert not is_universe_serial(resolved_b)  # never another founder's home


def test_write_graph_target_universe_creates_and_binds(data_dir, monkeypatch):
    # The canonical connector surface has no `universe` tool — explicit birth
    # routes through write_graph target=universe.
    from tinyassets.api import universe as universe_api
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.universe_server import write_graph

    monkeypatch.setattr(universe_api, "_base_path", lambda: data_dir)
    _login("founder-1")
    out = json.loads(write_graph(target="command_center"))
    assert out.get("error") is None, out
    assert out["status"] == "born"
    assert "persona" in out and "first_run_checklist" not in out
    assert out["persona"]["self_model"]["open_questions"]
    uid = out["universe_id"]
    assert is_universe_serial(uid)
    assert get_founder_home(data_dir, "founder-1") == uid
    assert (data_dir / uid / "soul.md").is_file()


def test_write_graph_unknown_target_lists_universe(data_dir):
    from tinyassets.universe_server import write_graph

    # Signed in: the auth check runs before target dispatch, so an unknown
    # target only reports itself to a caller who has an identity.
    _login("founder-1")
    out = json.loads(write_graph(target="nope"))
    assert out["error"] == "unknown_target"
    assert "command_center" in out["allowed_targets"]


# ---------------------------------------------------------------------------
# universe-lifecycle-and-soul task 5.2: public universe birth self-serializes.
# Every public birth entry point generates its own opaque ``u-``+ULID serial
# and rejects a caller-selected id. Internal migration/dev tooling is exempt.
# ---------------------------------------------------------------------------


def test_public_create_universe_rejects_caller_selected_id(data_dir, monkeypatch):
    """`_universe_impl` (the public dispatch boundary) refuses a chosen id."""
    from tinyassets.api import universe as universe_api

    monkeypatch.setattr(universe_api, "_base_path", lambda: data_dir)
    out = json.loads(
        universe_api._universe_impl(
            action="create_universe", universe_id="my-cool-name"
        )
    )
    assert out["reason"] == "caller_selected_id_rejected"
    assert "opaque serial" in out["error"]
    # No root, serial or descriptive, may be materialized by a rejected birth.
    assert _universe_dirs(data_dir) == []


def test_public_create_universe_without_id_self_serializes(data_dir, monkeypatch):
    """The public path with no id assigns exactly one opaque serial root."""
    _own_universes_as()
    from tinyassets.api import universe as universe_api

    monkeypatch.setattr(universe_api, "_base_path", lambda: data_dir)
    out = json.loads(universe_api._universe_impl(action="create_universe"))
    assert out.get("error") is None, out
    uid = out["universe_id"]
    assert is_universe_serial(uid)
    assert _serial_dirs(data_dir) == [data_dir / uid]


def test_write_graph_universe_rejects_caller_selected_graph_id(data_dir, monkeypatch):
    """The write_graph target=universe birth path also refuses a chosen id."""
    from tinyassets.api import universe as universe_api
    from tinyassets.universe_server import write_graph

    monkeypatch.setattr(universe_api, "_base_path", lambda: data_dir)
    _login("founder-1")
    out = json.loads(write_graph(target="command_center", graph_id="chosen-name"))
    assert out["reason"] == "caller_selected_id_rejected"
    assert _universe_dirs(data_dir) == []


def test_internal_named_id_is_accepted(data_dir, monkeypatch):
    """The internal-trust flag lets migration/first-contact supply a serial."""
    _own_universes_as()
    from tinyassets.api import universe as universe_api
    from tinyassets.ids import new_universe_id

    monkeypatch.setattr(universe_api, "_base_path", lambda: data_dir)
    reserved = new_universe_id()
    out = json.loads(
        universe_api._universe_impl(
            action="create_universe",
            universe_id=reserved,
            allow_named_universe_id=True,
        )
    )
    assert out.get("error") is None, out
    assert out["universe_id"] == reserved
    assert (data_dir / reserved / "soul.md").is_file()


def test_first_contact_birth_still_self_serializes(data_dir, monkeypatch):
    """`ensure_founder_home` births a serial home through the trusted path."""
    from tinyassets.api import universe as universe_api
    from tinyassets.api.first_contact import ensure_founder_home

    monkeypatch.setattr(universe_api, "_base_path", lambda: data_dir)
    _login("founder-1")
    home = ensure_founder_home(data_dir, "founder-1")
    assert is_universe_serial(home)
    assert (data_dir / home / "soul.md").is_file()


def test_stale_descriptive_binding_is_rejected_not_materialized(data_dir, monkeypatch):
    """A poisoned pre-existing descriptive `founder_home` must fail closed.

    universe-creation 5.2 provenance gate: `claim_founder_home` returns a
    pre-existing binding verbatim (ON CONFLICT DO NOTHING). A stale,
    founder-influenced *descriptive* id must NEVER cross the internal-trust flag
    and become a materialized named universe — first-contact fails closed and
    never rebinds it here. `set_founder_home` without the provenance flag records
    the binding as unproven (marker 0), so the gate rejects it.
    """
    from tinyassets.api import universe as universe_api
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import get_founder_home, set_founder_home

    monkeypatch.setattr(universe_api, "_base_path", lambda: data_dir)
    _login("founder-legacy")
    # Seed a stale descriptive binding with no complete directory.
    set_founder_home(data_dir, founder_sub="founder-legacy", universe_id="chosen-name")

    result = ensure_founder_home(data_dir, "founder-legacy")

    assert result == ""                                  # fail closed
    assert not (data_dir / "chosen-name").exists()       # never materialized
    assert _universe_dirs(data_dir) == []                # no universe born at all
    # The stale binding is left as-is for host-run migration — NOT rebound here.
    assert get_founder_home(data_dir, "founder-legacy") == "chosen-name"


def test_serial_shaped_unproven_value_fails_closed(data_dir, monkeypatch):
    """A serial-SHAPED but non-platform-GENERATED binding must fail closed.

    Round-3 review finding: `is_universe_serial` proves only format, not
    generation provenance. A hostile/legacy value like
    `u-00000000000000000000000000` satisfies the regex yet was never generated
    by the platform. Seeding it via `set_founder_home` (no provenance flag →
    marker 0) must NOT be materialized: the structural provenance marker, not
    the format, is what the gate trusts. This test can only pass with the marker
    gate; a format-only gate materializes the hostile id.
    """
    from tinyassets.api import universe as universe_api
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.ids import is_universe_serial

    monkeypatch.setattr(universe_api, "_base_path", lambda: data_dir)
    _login("founder-hostile")
    hostile = "u-00000000000000000000000000"
    assert is_universe_serial(hostile)  # passes FORMAT — the whole point
    set_founder_home(data_dir, founder_sub="founder-hostile", universe_id=hostile)

    result = ensure_founder_home(data_dir, "founder-hostile")

    assert result == ""                          # fail closed on unproven serial
    assert not (data_dir / hostile).exists()     # never materialized


def test_legitimate_reserved_serial_binding_materializes(data_dir, monkeypatch):
    """A proven platform-generated serial binding (incomplete dir) materializes.

    The gate's accepted case: a pre-existing binding recorded WITH the
    provenance marker (e.g. a prior reservation whose dir was removed/never
    completed) is trusted and repaired to a complete serial home.
    """
    from tinyassets.api import universe as universe_api
    from tinyassets.api.first_contact import ensure_founder_home
    from tinyassets.daemon_server import set_founder_home
    from tinyassets.ids import new_universe_id

    monkeypatch.setattr(universe_api, "_base_path", lambda: data_dir)
    _login("founder-reserved")
    reserved = new_universe_id()
    set_founder_home(
        data_dir,
        founder_sub="founder-reserved",
        universe_id=reserved,
        platform_generated=True,
    )

    result = ensure_founder_home(data_dir, "founder-reserved")

    assert result == reserved
    assert is_universe_serial(result)
    assert (data_dir / reserved / "soul.md").is_file()


def test_claim_founder_home_stamps_generation_provenance(data_dir):
    """`claim_founder_home` records provenance structurally, per writer.

    A freshly reserved serial is marked platform-generated (the reservation
    contract), so first-contact trusts it. A value bound WITHOUT the flag stays
    unproven. This is the structural difference the 5.2 gate reads — not id
    shape.
    """
    from tinyassets.daemon_server import (
        claim_founder_home,
        founder_home_is_platform_generated,
        set_founder_home,
    )
    from tinyassets.ids import new_universe_id

    reserved = new_universe_id()
    assert claim_founder_home(data_dir, "founder-fresh", reserved) == reserved
    assert founder_home_is_platform_generated(
        data_dir, founder_sub="founder-fresh", universe_id=reserved
    )

    # Same-shaped serial, but bound without proving generation → unproven.
    shaped = new_universe_id()
    set_founder_home(data_dir, founder_sub="founder-unproven", universe_id=shaped)
    assert not founder_home_is_platform_generated(
        data_dir, founder_sub="founder-unproven", universe_id=shaped
    )


def test_public_tool_wrappers_omit_the_trust_flag():
    """Reachability lock: the trust flag is absent from public MCP wrappers.

    universe-creation 5.2: `allow_named_universe_id` must never appear on a
    public MCP surface, or a caller could self-select an id. Both public birth
    wrappers omit it (Codex also verified this against the live FastMCP schemas
    with `mcp.call_tool` probes). This locks it at the signature level.
    """
    import inspect

    from tinyassets.universe_server import universe, write_graph

    for tool in (universe, write_graph):
        assert "allow_named_universe_id" not in inspect.signature(tool).parameters


# ---------------------------------------------------------------------------
# Can the newborn SPEAK? (universe-creation 1.14)
#
# Every test above proves a universe is BORN. None proved it can answer. In
# production it could not: 92dd60c5 correctly stopped a credential-less
# universe from spending the host's subscription, and nothing gives a newborn
# a credential of its own — so the founder's very first turn came back as
# "All providers exhausted for role=writer".
# ---------------------------------------------------------------------------


def _engine_raises(monkeypatch, exc: BaseException):
    """Make the assigned engine fail, and record whether it was ever called."""
    import tinyassets.universe_intelligence as intelligence

    called: dict = {"count": 0}

    def fake_call_provider(prompt, system="", *, role="writer", **kwargs):
        called["count"] += 1
        raise exc

    monkeypatch.setattr(intelligence, "call_provider", fake_call_provider)
    return called


def _exhausted() -> BaseException:
    """The exact error a universe with no provider authority produces.

    Built the way `ProviderRouter.call` builds it (router.py) — with the
    FEAT-006 `chain_state` diagnostics that the genuine every-provider-failed
    raise carries. The policy hard-fails raise the same class BARE; see
    `_policy_hard_fail`.
    """
    from tinyassets.exceptions import AllProvidersExhaustedError
    from tinyassets.providers.diagnostics import (
        ProviderAttemptDiagnostic,
        build_chain_state,
    )

    chain = ["claude-code", "codex", "ollama-local"]
    attempts = [
        ProviderAttemptDiagnostic(
            provider=name, status="skipped", skip_class="auth_invalid",
            detail="no credential for this universe",
        )
        for name in chain
    ]
    return AllProvidersExhaustedError(
        "All providers exhausted for role=writer. Daemon should retry with backoff.",
        attempts=attempts,
        chain_state=build_chain_state(role="writer", chain=chain, attempts=attempts),
    )


def _policy_hard_fail() -> BaseException:
    """The bare exhaustion the router raises when policy empties the chain."""
    from tinyassets.exceptions import AllProvidersExhaustedError

    return AllProvidersExhaustedError(
        "All providers for role='writer' are blocked by the universe's "
        "allowed_providers=['ollama-local']. Daemon will not silently fall "
        "back to a disallowed provider."
    )


def _attach_engine_credential(universe_dir: Path) -> None:
    """Attach a BYO engine key the same way `universe action=set_engine` does."""
    import base64

    from tinyassets.credential_vault import write_credential_vault

    write_credential_vault(universe_dir, [{
        "credential_type": "llm_api_key",
        "service": "anthropic",
        "secret_b64": base64.b64encode(b"sk-founder-key").decode("ascii"),
    }])


def test_newborn_without_a_credential_gets_onboarding_not_a_raw_error(
    data_dir, monkeypatch
):
    """P0 #1582: first contact births a universe that cannot answer.

    Birth still succeeds; the founder gets an honest setup hold instead of
    `All providers exhausted for role=writer` or a retired tool route.
    """
    from tinyassets.universe_server import converse

    _login("founder-1")
    _engine_raises(monkeypatch, _exhausted())

    out = json.loads(converse(message="Hello"))
    rendered = json.dumps(out)

    uid = out["universe_id"]
    assert is_universe_serial(uid)
    assert (data_dir / uid / "soul.md").is_file()  # birth still succeeded
    assert out["status"] == "held"
    assert out["reason"] == "setup_required"
    assert "All providers exhausted" not in rendered
    assert "set_engine" not in rendered
    assert "not exposed by the advertised handles" in rendered
    assert "byo_api_key" in rendered


def test_setup_required_reply_is_platform_authored_not_the_universe_voice(
    data_dir, monkeypatch
):
    """The held payload must never masquerade as the universe speaking.

    `reply` is the key the connector renders verbatim as the universe's own
    first-person voice; a deterministic platform message carries `note`
    instead — the same split `write_page` / `universe` brain-write relays use.
    """
    from tinyassets.universe_server import converse

    _login("founder-1")
    _engine_raises(monkeypatch, _exhausted())

    out = json.loads(converse(message="Hello"))

    assert "reply" not in out
    assert out["note"].strip()
    assert out["missing"] == ["compute", "model_access"]


def test_credentialed_universe_still_surfaces_transient_exhaustion_honestly(
    data_dir, monkeypatch
):
    """Distinguishability: BUG-038/039 must not be masked as onboarding.

    An ESTABLISHED universe that HAS an attached credential and hits provider
    exhaustion is a real failure. Telling its founder to go attach a provider
    they already attached would hide the outage.
    """
    from tinyassets.universe_server import converse

    _login("founder-1")
    uid = _create_via_action(data_dir, monkeypatch)
    _attach_engine_credential(data_dir / uid)
    _engine_raises(monkeypatch, _exhausted())

    out = json.loads(converse(message="Hello"))

    _assert_reads_as_outage_not_onboarding(out)


def test_credentialless_universe_still_surfaces_non_provider_failures(
    data_dir, monkeypatch
):
    """Only provider exhaustion becomes onboarding — nothing else is swallowed."""
    from tinyassets.credential_vault import credential_vault_path
    from tinyassets.universe_server import converse

    _login("founder-1")
    uid = _create_via_action(data_dir, monkeypatch)
    _engine_raises(monkeypatch, RuntimeError("soul bundle is corrupt"))

    assert not credential_vault_path(data_dir / uid).exists()

    out = json.loads(converse(message="Hello"))

    assert out.get("status") != "held"
    assert "soul bundle is corrupt" in out["error"]


def test_unreadable_vault_never_claims_the_credential_is_missing(
    data_dir, monkeypatch
):
    """Fail-safe direction: a vault we cannot parse is not proof of absence.

    Claiming "no credential attached" from a read failure would send a founder
    to re-attach a key they already have, and would hide a corrupt vault.
    """
    from tinyassets.credential_vault import credential_vault_path
    from tinyassets.universe_server import converse

    _login("founder-1")
    uid = _create_via_action(data_dir, monkeypatch)
    credential_vault_path(data_dir / uid).write_text("{not json", encoding="utf-8")
    _engine_raises(monkeypatch, _exhausted())

    out = json.loads(converse(message="Hello"))

    _assert_reads_as_outage_not_onboarding(out)


def test_held_payload_does_not_invoke_a_second_provider_call(
    data_dir, monkeypatch
):
    """The held path is deterministic — it never retries onto another engine."""
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.universe_server import converse

    _login("founder-1")
    called = _engine_raises(
        monkeypatch,
        ProviderAuthorityHeldError("Connect your provider before running this universe."),
    )

    json.loads(converse(message="Hello"))

    assert called["count"] == 1


@pytest.mark.parametrize(
    ("engine_source", "extra"),
    [
        ("self_hosted_endpoint", {"engine_endpoint": "https://llm.internal/v1"}),
        ("market_rented", {"market_model": "glm-5.2"}),
        ("host_daemon", {}),
    ],
)
def test_non_vault_engine_choice_is_an_outage_not_missing_setup(
    data_dir, monkeypatch, engine_source, extra
):
    """Codex ADAPT 2026-07-25 finding 1: a vault-only test misreads three engines.

    `set_engine` records `self_hosted_endpoint` / `market_rented` /
    `host_daemon` in config and writes NO vault credential. Reading an empty
    vault as "never set up" would send a founder who already chose an engine
    back to onboarding and hide that their engine is down.
    """
    from tinyassets.config import write_universe_config_fields
    from tinyassets.universe_server import converse

    _login("founder-1")
    uid = _create_via_action(data_dir, monkeypatch)
    write_universe_config_fields(
        data_dir / uid, engine_source=engine_source, **extra
    )
    _engine_raises(monkeypatch, _exhausted())

    out = json.loads(converse(message="Hello"))

    _assert_reads_as_outage_not_onboarding(out)


def test_unreadable_config_never_claims_the_engine_is_missing(
    data_dir, monkeypatch
):
    """Codex ADAPT round 2 (2026-07-25), reviewer's exact repro.

    `load_universe_config` degrades a corrupt config to a default
    `UniverseConfig`, whose `engine_source` is the same value that means "the
    founder never chose an engine". Without a separate parseability probe, a
    corrupt config reads as "no engine yet" — losing the founder's recorded
    choice AND hiding that their config is broken.
    """
    from tinyassets.universe_server import converse

    _login("founder-1")
    uid = _create_via_action(data_dir, monkeypatch)
    (data_dir / uid / "config.yaml").write_text(
        "engine_source: [unterminated\n", encoding="utf-8"
    )
    _engine_raises(monkeypatch, _exhausted())

    out = json.loads(converse(message="Hello"))

    _assert_reads_as_outage_not_onboarding(out)


def test_non_string_engine_source_does_not_crash_the_failing_turn(
    data_dir, monkeypatch
):
    """Codex ADAPT round 2 (2026-07-25): `engine_source: 7` parses fine.

    `_build_config` assigns YAML values without type coercion, so a non-string
    reaches the comparison. An AttributeError there would escape while we are
    already handling the founder's failed turn.
    """
    from tinyassets.universe_server import converse

    _login("founder-1")
    uid = _create_via_action(data_dir, monkeypatch)
    (data_dir / uid / "config.yaml").write_text("engine_source: 7\n", encoding="utf-8")
    _engine_raises(monkeypatch, _exhausted())

    out = json.loads(converse(message="Hello"))

    _assert_reads_as_outage_not_onboarding(out)


def test_policy_hard_fail_keeps_its_own_message(data_dir, monkeypatch):
    """Codex ADAPT 2026-07-25 finding 2: not every exhaustion is missing setup.

    The router raises `AllProvidersExhaustedError` bare when a universe's
    `allowed_providers` allowlist empties the chain. Retelling that policy
    block as "you have no engine yet" would send the founder to attach a
    credential the allowlist would still refuse.
    """
    from tinyassets.universe_server import converse

    _login("founder-1")
    _engine_raises(monkeypatch, _policy_hard_fail())

    out = json.loads(converse(message="Hello"))

    assert out.get("status") != "held"
    assert "allowed_providers" in out["error"]


# --------------------------------------------------------------------------- #
# Private by default reaches the NEW-USER path, not just explicit creation
# --------------------------------------------------------------------------- #
# Founder, 2026-09-26: "universes and the nodes in them need to be default
# private and we need to make sure that is set correctly for new users also."
#
# First-contact home materialization is THE new-user path: `converse` with no
# graph_id lands here and it is the only universe most people will ever have.
# It routes through `_universe_impl(action="create_universe")` without passing a
# visibility, so it inherits `DEFAULT_CREATE_VISIBILITY` — which is exactly why it
# needs its own test rather than trusting the shared default: a future caller
# passing an explicit level here would be invisible to the creation-path tests.


def test_a_new_users_first_universe_is_born_private(data_dir):
    from tinyassets.api import visibility as vis
    from tinyassets.api.first_contact import ensure_founder_home

    _login("newcomer-1")
    uid = ensure_founder_home(data_dir, "newcomer-1")
    assert is_universe_serial(uid), uid

    assert vis.is_declared(uid), "a newborn home must not be left undeclared"
    assert vis.universe_visibility(uid) is vis.PRIVATE
    # Born private is the PLATFORM's default, not a decision this person made.
    assert vis.declared_level_source(uid) == "default"
    assert not vis.level_was_chosen_by_owner(uid)


def test_another_user_cannot_discover_or_read_a_new_users_home(data_dir):
    """The property the founder actually asked for, through the real callers."""
    import tinyassets.api.status as status_mod
    import tinyassets.api.universe as us
    from tinyassets.api.first_contact import ensure_founder_home

    _login("newcomer-1")
    uid = ensure_founder_home(data_dir, "newcomer-1")
    assert uid

    # A different real person, fully signed in, holding no grant on that home.
    _login("stranger-9")
    listed = json.loads(us._action_list_universes())
    assert uid not in {u["id"] for u in listed["universes"]}
    assert uid not in json.dumps(listed), "a refusal must not leak the id"

    assert json.loads(
        us._action_inspect_universe(universe_id=uid)
    )["error"] == "universe_access_denied"
    assert json.loads(status_mod.get_status(uid))["error"] == "universe_access_denied"


def test_the_owner_still_reads_their_own_new_home(data_dir):
    """Guard against the test above passing because the home is simply broken."""
    import tinyassets.api.universe as us
    from tinyassets.api.first_contact import ensure_founder_home

    _login("newcomer-1")
    uid = ensure_founder_home(data_dir, "newcomer-1")

    out = json.loads(us._action_inspect_universe(universe_id=uid))
    assert out.get("error") != "universe_access_denied", out
    assert out["visibility"] == "private"
    assert uid in {
        u["id"] for u in json.loads(us._action_list_universes())["universes"]
    }
