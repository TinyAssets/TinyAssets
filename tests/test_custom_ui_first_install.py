"""A fresh account can hold a UI bundle before it has published anything.

The UIs a person keeps, and which one they use, live in their own row of
``universe_app_ui`` -- keyed by (person, universe), compare-and-set on a
revision. It used to be squeezed into an ``app_experience`` agent binding, which
needed a published definition, then a nullable definition column, which leaked
definition-less rows into every binding reader and needed a table rebuild. These
tests pin the replacement: the first save creates the row with nothing
published, two first saves racing leave ONE row, a stale save is refused rather
than overwriting, and ``agent_bindings`` is exactly the shape it always was.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from tinyassets.custom_agents import (
    AgentConflictError,
    AgentValidationError,
    _agent_connect,
    get_app_ui,
    list_bindings,
    list_definitions,
    save_app_ui,
)

ALICE, BOB = "alice", "bob"
HOME = "u-alice"


def _bundle(ui_id: str = "office") -> dict:
    return {"kind": "tinyassets.app-ui.v1", "version": 1, "ui_id": ui_id,
            "name": "Office", "markup": "<div id=lobby>Lobby</div>",
            "style": "#lobby{color:red}", "script": "tinyassets.whoami()"}


def _rows(base: Path) -> list[sqlite3.Row]:
    with _agent_connect(base) as conn:
        return conn.execute("SELECT * FROM universe_app_ui").fetchall()


def test_a_new_account_saves_a_ui_with_nothing_published(tmp_path) -> None:
    empty = get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME)
    assert empty == {"universe_id": HOME, "ui_library": [], "ui_selection": None,
                     "revision": 0, "updated_at": None}

    saved = save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                        expected_revision=0, changes={"ui_library": [_bundle()]})

    assert saved["revision"] == 1
    assert saved["ui_library"] == [_bundle()], "stored verbatim, not rewritten"
    assert get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME) == saved
    # Nothing about the account became public, and no binding was minted.
    assert list_definitions(tmp_path) == []
    assert list_bindings(tmp_path, universe_id=HOME) == []


def test_a_partial_save_keeps_the_field_it_did_not_name(tmp_path) -> None:
    first = save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                        expected_revision=0, changes={"ui_library": [_bundle()]})
    choice = {"version": 1, "state": "active", "ui_id": "office"}
    second = save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                         expected_revision=first["revision"],
                         changes={"ui_selection": choice})

    assert second["revision"] == 2
    assert second["ui_library"] == [_bundle()], "saving a choice erased the library"
    assert second["ui_selection"] == choice

    third = save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                        expected_revision=2,
                        changes={"ui_library": [_bundle(), _bundle("den")]})
    assert third["ui_selection"] == choice, "saving a library erased the choice"


def test_a_stale_save_is_refused_and_changes_nothing(tmp_path) -> None:
    save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                expected_revision=0, changes={"ui_library": [_bundle()]})
    current = save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                          expected_revision=1, changes={"ui_library": [_bundle("den")]})

    for stale in (0, 1, 7):
        with pytest.raises(AgentConflictError, match="current is 2"):
            save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                        expected_revision=stale, changes={"ui_library": []})
    assert get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME) == current


def test_naming_a_revision_for_a_row_that_does_not_exist_creates_nothing(tmp_path) -> None:
    with pytest.raises(AgentConflictError, match="current is 0"):
        save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                    expected_revision=3, changes={"ui_library": [_bundle()]})
    assert _rows(tmp_path) == []


def test_each_person_has_their_own_row_in_the_same_universe(tmp_path) -> None:
    mine = save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                       expected_revision=0, changes={"ui_library": [_bundle()]})
    # Bob's first save in the same universe is revision 0 for HIS row: Alice's
    # row is neither visible to him nor a conflict for him.
    assert get_app_ui(tmp_path, owner_user_id=BOB, universe_id=HOME)["revision"] == 0
    save_app_ui(tmp_path, owner_user_id=BOB, universe_id=HOME,
                expected_revision=0, changes={"ui_library": []})
    assert get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME) == mine


def test_two_concurrent_first_saves_leave_exactly_one_row(tmp_path) -> None:
    """The race the binding bootstrap had: both callers see no row, both write.

    Here both name revision 0; the primary key admits one row and the loser is
    told it lost. Drop the key and this goes red (see the PR's mutation table).
    """
    get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME)  # schema up front
    gate = threading.Barrier(2)
    outcomes: list[object] = []

    def save(ui_id: str) -> None:
        gate.wait()
        try:
            outcomes.append(save_app_ui(
                tmp_path, owner_user_id=ALICE, universe_id=HOME,
                expected_revision=0,
                changes={"ui_selection": {"version": 1, "state": "active", "ui_id": ui_id}},
            ))
        except Exception as exc:  # noqa: BLE001 -- the type IS the assertion
            outcomes.append(exc)

    threads = [threading.Thread(target=save, args=(ui_id,)) for ui_id in ("office", "den")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    rows = _rows(tmp_path)
    assert len(rows) == 1
    assert int(rows[0]["revision"]) == 1
    won = [o for o in outcomes if isinstance(o, dict)]
    lost = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(won) == 1 and len(lost) == 1, outcomes
    assert isinstance(lost[0], AgentConflictError), repr(lost[0])
    # The row holds the WINNER's choice, not a blend and not the loser's.
    assert json.loads(rows[0]["ui_selection_json"]) == won[0]["ui_selection"]


@pytest.mark.parametrize("changes, needle", [
    ({}, "must set ui_library or ui_selection"),
    ({"ui_library": [], "agent_definition_id": "d1"}, "is not one of"),
    ({"ui_library": {}}, "ui_library must be a list"),
    ({"ui_library": [_bundle(), _bundle()]}, "listed twice"),
    ({"ui_library": ["office"]}, "must be an object with a ui_id"),
    ({"ui_selection": "office"}, "ui_selection must be an object"),
    ({"ui_selection": {"pad": "x" * 2000}}, "ui_selection exceeds"),
])
def test_malformed_saves_are_refused_by_reason(tmp_path, changes, needle) -> None:
    with pytest.raises(AgentValidationError, match=needle):
        save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                    expected_revision=0, changes=changes)
    assert _rows(tmp_path) == []


def test_there_is_no_limit_on_how_many_uis_a_library_holds(tmp_path) -> None:
    """No structural cap: the count is the person's business. Forty is well past
    the four the first cut allowed, and every one is kept and read back."""
    many = [_bundle(f"ui-{i}") for i in range(40)]
    saved = save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                        expected_revision=0, changes={"ui_library": many})
    assert [b["ui_id"] for b in saved["ui_library"]] == [b["ui_id"] for b in many]
    assert get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME)["ui_library"] == many


def test_the_library_has_no_size_bound(tmp_path) -> None:
    """Past the old 4 MiB ``MAX_APP_UI_LIBRARY_BYTES``, and the save succeeds.

    That constant refused an install with "remove one first". Its own comment
    said it should be charged against a per-universe storage quota once one
    existed -- one does, so it is (founder, 2026-09-30: an account's two limits
    are cloud storage and concurrent agent seats). Per-BUNDLE validation is
    untouched; that is payload validation of one document.
    """
    from tinyassets import custom_agents
    from tinyassets.custom_agents import _canonical_json

    assert not hasattr(custom_agents, "MAX_APP_UI_LIBRARY_BYTES")

    # ~6 MiB of library, half again the old ceiling, built from valid bundles.
    big = [dict(_bundle(), ui_id=f"ui-{i:03d}", script="x" * 32000) for i in range(200)]
    assert len(_canonical_json(big).encode("utf-8")) > 4 * 1024 * 1024

    saved = save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                        expected_revision=0, changes={"ui_library": big})
    assert len(saved["ui_library"]) == 200
    read_back = get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME)
    assert read_back["ui_library"] == big
    assert read_back["revision"] == 1

    # The SELECTION pointer is still bounded -- one small document, not an account.
    with pytest.raises(AgentValidationError, match="ui_selection exceeds"):
        save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                    expected_revision=1,
                    changes={"ui_selection": {"ui_id": "x" * 4000}})


def test_agent_bindings_is_the_shape_main_has(tmp_path) -> None:
    """The UI store must not reach into agent_bindings at all.

    Its definition column stays NOT NULL, so no binding without a design behind
    it can exist for consumer selection, serving or model bootstrap to trip on.
    """
    save_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME,
                expected_revision=0, changes={"ui_library": [_bundle()]})
    with _agent_connect(tmp_path) as conn:
        sql = str(conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_bindings'"
        ).fetchone()[0])
        assert "agent_definition_id TEXT NOT NULL" in sql
        assert conn.execute("SELECT COUNT(*) FROM agent_bindings").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            conn.execute(
                "INSERT INTO agent_bindings (agent_binding_id, universe_id, "
                "agent_definition_id, configuration_json, created_by, updated_by, "
                "created_at, updated_at) VALUES ('b', ?, NULL, '{}', 'alice', 'alice', 1, 1)",
                (HOME,),
            )


def test_account_deletion_removes_the_persons_rows_everywhere_and_only_theirs(
    tmp_path,
) -> None:
    from tests.test_account_deletion import (
        HOME_A,
        HOME_B,
        A,
        B,
        _seed_auth,
        _seed_outbound,
        _seed_user,
    )
    from tinyassets.account_deletion import delete_account

    base = tmp_path / "data"
    base.mkdir()
    _seed_user(base, A, HOME_A)
    _seed_user(base, B, HOME_B)
    _seed_outbound(base)
    _seed_auth(base)
    save_app_ui(base, owner_user_id=A, universe_id=HOME_A,
                expected_revision=0, changes={"ui_library": [_bundle()]})
    # A's row in B's universe is A's personal choice, so it goes with A too.
    save_app_ui(base, owner_user_id=A, universe_id=HOME_B,
                expected_revision=0, changes={"ui_library": []})
    kept = save_app_ui(base, owner_user_id=B, universe_id=HOME_B,
                       expected_revision=0, changes={"ui_library": [_bundle("den")]})
    # B's own choice ABOUT A's universe. It is B's, so deleting A -- and with it
    # A's universe -- must neither take it nor be blocked by it. Sweeping this
    # table by universe did take it: a collaborator's save can commit between
    # the foreign-row check and the delete (review, 2026-09-26).
    bobs_about_a = save_app_ui(base, owner_user_id=B, universe_id=HOME_A,
                               expected_revision=0, changes={"ui_library": [_bundle("b")]})

    delete_account(base, founder_sub=A, cancel_billing=lambda home: "cancelled",
                   delete_identity=lambda sub: "deleted")

    assert get_app_ui(base, owner_user_id=A, universe_id=HOME_A)["revision"] == 0
    assert get_app_ui(base, owner_user_id=A, universe_id=HOME_B)["revision"] == 0
    assert get_app_ui(base, owner_user_id=B, universe_id=HOME_B) == kept
    assert get_app_ui(base, owner_user_id=B, universe_id=HOME_A) == bobs_about_a


def test_deletion_never_reaches_this_table_by_universe(tmp_path) -> None:
    """The structural half: whatever interleaving happens, the plan for this
    table matches ONLY the deleted person's key, so no other person's row can be
    in the delete's WHERE clause."""
    from tinyassets.account_deletion import deletion_plan

    get_app_ui(tmp_path, owner_user_id=ALICE, universe_id=HOME)  # schema up front
    with _agent_connect(tmp_path) as conn:
        plan = deletion_plan(conn, principal=ALICE, home=HOME)
    assert plan["universe_app_ui"] == [("owner_user_id", "principal")]


# --------------------------------------------------------------------------- #
# the graph handle: authority is the binding check, the row is the caller's
# --------------------------------------------------------------------------- #


@pytest.fixture
def homes(tmp_path, monkeypatch):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    for owner in (ALICE, BOB):
        uid = "u-" + owner
        (tmp_path / uid).mkdir()
        set_founder_home(tmp_path, founder_sub=owner, universe_id=uid,
                         platform_generated=True)
        grant_universe_access(tmp_path, universe_id=uid, actor_id=owner,
                              permission="admin")
    return tmp_path


def _as(name: str):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    return identity_context(Identity(user_id=name, username=name, capabilities=["write"]))


def _write(universe: str, revision: int, payload: dict) -> dict:
    from tinyassets.universe_server import write_graph

    return json.loads(write_graph(target="app_ui", operation="save", graph_id=universe,
                                  expected_revision=revision,
                                  payload_json=json.dumps(payload)))


def _read(universe: str) -> dict:
    from tinyassets.universe_server import read_graph

    return json.loads(read_graph(target="app_ui", graph_id=universe))


def test_the_handle_saves_and_reads_the_callers_own_row(homes) -> None:
    with _as(ALICE):
        assert _read("u-alice")["app_ui"]["revision"] == 0
        saved = _write("u-alice", 0, {"ui_library": [_bundle()]})
        assert saved["status"] == "saved", saved
        assert saved["app_ui"]["revision"] == 1
        # The whole-row read carries the platform's blank command center
        # alongside the stored row (read_app_ui), so it is a superset of what
        # was written rather than equal to it. The STORED fields must still
        # round-trip exactly, and the addition must be read-only.
        read = _read("u-alice")["app_ui"]
        assert {k: v for k, v in read.items() if k != "platform_default"} == saved["app_ui"]
        assert read["platform_default"]["ui_id"] == "platform:blank"
        assert "platform_default" not in saved["app_ui"], "never stored, only served"
        assert _write("u-alice", 0, {"ui_library": []})["error"] == "app_ui_conflict"
        assert _write("u-alice", 1, {"nope": 1})["error"] == "app_ui_validation_error"
    rows = _rows(homes)
    assert [(r["owner_user_id"], r["universe_id"]) for r in rows] == [(ALICE, "u-alice")]


def test_another_person_can_neither_read_nor_write_into_a_universe_they_lack(homes) -> None:
    with _as(ALICE):
        _write("u-alice", 0, {"ui_library": [_bundle()]})
    with _as(BOB):
        read = _read("u-alice")
        assert "app_ui" not in read and read.get("error"), read
        written = _write("u-alice", 0, {"ui_library": [_bundle("den")]})
        assert "app_ui" not in written and written.get("error"), written
    with _as(ALICE):
        assert _read("u-alice")["app_ui"]["ui_library"] == [_bundle()]
    assert len(_rows(homes)) == 1


def test_an_unknown_operation_is_refused_by_name(homes) -> None:
    from tinyassets.universe_server import write_graph

    with _as(ALICE):
        refused = json.loads(write_graph(target="app_ui", operation="delete",
                                         graph_id="u-alice", payload_json="{}"))
    assert refused["error"] == "unknown_app_ui_operation"
    assert refused["allowed_operations"] == [
        "save", "activate", "use_default", "add_ui", "replace_ui", "edit_ui", "remove_ui",
        "put_asset", "remove_asset"]


def test_the_route_the_handbook_names_works_on_the_engine_surface(tmp_path, monkeypatch) -> None:
    """The interfaces chapter tells the universe's own agent to read and save
    ``target="app_ui"``. Driven through the engine handles it is actually given:
    a chapter naming a route that surface refuses is a broken instruction."""
    from tests.engine_authority_helpers import seed_bound_engine
    from tinyassets import engine_mcp_server as engine

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(engine, "_ACTOR_ID", ALICE)
    monkeypatch.setattr(engine, "_GRAPH_ID", HOME)
    (tmp_path / HOME).mkdir()
    seed_bound_engine(monkeypatch)

    empty = json.loads(engine.read_graph(target="app_ui"))
    assert empty["app_ui"]["revision"] == 0, empty
    saved = json.loads(engine.write_graph(
        target="app_ui", operation="save", expected_revision=0,
        payload_json=json.dumps({"ui_library": [_bundle()]}),
    ))
    assert saved["status"] == "saved", saved
    # The engine reads an index, never the whole library (a model's result is
    # bounded); the full row is the connector's no-query read.
    index = json.loads(engine.read_graph(target="app_ui"))["app_ui"]
    assert index["revision"] == saved["app_ui"]["revision"]
    assert [u["ui_id"] for u in index["uis"]] == ["office"]
    rows = _rows(tmp_path)
    assert [(r["owner_user_id"], r["universe_id"]) for r in rows] == [(ALICE, HOME)]
