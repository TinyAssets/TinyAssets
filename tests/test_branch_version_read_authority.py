"""No path hands one user another user's private branch history.

patch_branch mints a version snapshot before and after every edit, private
branches included, so a branch's versions are its prompts over time -- including
text the owner later removed. Node edit history is the same record per node.
The readers of both checked nothing. The connector's deprecated ``extensions``
tool was hidden from tools/list but still dispatchable, so any signed-in user
could call them by name. Astra refute review of #4107, 2026-09-30.

Two layers, each tested on its own:

* the connector no longer dispatches ``extensions`` at all (the route the leak
  took); the first test fails on the tree that had the leak;
* every reader of versions or node history asks the branch's readability gate,
  so no future route to them can repeat it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable

import pytest

from tests.test_branch_read_authority import (  # noqa: F401 - fixture
    _publish,
    _seed_branch,
    branch_authority_env,
)

SECRET = "ALICE PRIVATE PROMPT"


def _alices_private_branch(base: Path) -> str:
    branch = _seed_branch(base, branch_def_id="alice-private", author="alice",
                          visibility="private", node_ids=("step",))
    branch["node_defs"][0]["prompt_template"] = SECRET
    from tinyassets.daemon_server import save_branch_definition

    save_branch_definition(base, branch_def=branch)
    return _publish(base, branch, "alice")


def _via_connector(tool: str, arguments: dict) -> str:
    """Call a tool the way a connected client does, and return what it saw."""
    from tinyassets import universe_server

    try:
        result = asyncio.run(universe_server.mcp.call_tool(tool, arguments))
    except Exception as exc:  # noqa: BLE001 - a refusal is an answer here
        return f"refused: {exc}"
    return json.dumps(getattr(result, "structured_content", None) or str(result), default=str)


def test_another_user_cannot_read_private_history_through_the_connector(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],  # noqa: F811
) -> None:
    """The leak as it happened: Bob, signed in, asks the public connector for
    Alice's private branch versions by id. Red on the tree that had the leak."""
    base, authenticate = branch_authority_env
    version_id = _alices_private_branch(base)
    authenticate("bob")
    for arguments in (
        {"action": "list_branch_versions", "branch_def_id": "alice-private"},
        {"action": "get_branch_version", "branch_version_id": version_id},
        {"action": "list_node_versions", "branch_def_id": "alice-private", "node_id": "step"},
    ):
        seen = _via_connector("extensions", arguments)
        assert SECRET not in seen, (arguments, seen)


@pytest.mark.parametrize("tool,arguments", [
    ("extensions", {"action": "list_branches"}),
    ("universe", {"action": "inspect"}),
    ("goals", {"action": "list"}),
    ("gates", {"action": "list_claims"}),
    ("wiki", {"action": "list"}),
])
def test_the_connector_dispatches_no_legacy_fat_tool(tool: str, arguments: dict) -> None:
    """Hidden-but-dispatchable was the route: astra found private runs, versions
    and bindings reachable through gates and goals as well as extensions. The
    registry itself -- what a client can call, listed or not -- is the check."""
    from tinyassets import universe_server

    registered = {t.name for t in asyncio.run(universe_server.mcp.list_tools(run_middleware=False))}
    assert tool not in registered
    assert _via_connector(tool, arguments).startswith("refused")


def test_a_canonical_cannot_name_or_run_a_private_version(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],  # noqa: F811
) -> None:
    """Astra refute, P1: Bob set a personal canonical to Alice's private
    version and ran the goal; the preflight returned the private snapshot's
    input names. The version is now checked where it is set AND where it runs."""
    from tinyassets.api.market import _action_goal_set_canonical
    from tinyassets.api.runs import _action_run_branch_version
    from tinyassets.daemon_server import get_goal, save_goal

    base, authenticate = branch_authority_env
    version_id = _alices_private_branch(base)
    goal = save_goal(base, goal={"goal_id": "open-goal", "name": "Open goal",
                                 "description": "", "author": "carol", "visibility": "public"})
    authenticate("bob")
    refused = json.loads(_action_goal_set_canonical(
        {"goal_id": goal["goal_id"], "scope": "bob", "branch_version_id": version_id}))
    assert refused == {"status": "rejected", "error": f"Branch version '{version_id}' not found."}
    assert version_id not in json.dumps(get_goal(base, goal_id=goal["goal_id"]))

    run = json.loads(_action_run_branch_version({"branch_version_id": version_id}))
    assert SECRET not in json.dumps(run)
    assert run == {"error": f"branch_version_id {version_id!r} not found in branch_versions"}


def test_a_run_view_does_not_render_an_unreadable_branch(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],  # noqa: F811
) -> None:
    """Astra refute, P1: a run a stranger can read re-loaded the CURRENT
    private branch and drew its name, nodes and edges into the diagram."""
    from tinyassets.api.runs import _compose_run_snapshot

    base, authenticate = branch_authority_env
    _alices_private_branch(base)
    record = {"run_id": "r1", "branch_def_id": "alice-private", "status": "completed",
              "actor": "alice", "last_node_id": "", "started_at": None, "finished_at": None,
              "error": "", "output": {}}
    authenticate("bob")
    seen = json.dumps(_compose_run_snapshot(record, []), default=str)
    assert "Step" not in seen and "alice-private\"]" not in seen, seen
    assert "branch not found" in seen

    authenticate("alice")
    assert "Step" in json.dumps(_compose_run_snapshot(record, []), default=str)


def _ext(action: str, **kwargs) -> dict:
    from tinyassets.api.extensions import _extensions_impl

    return json.loads(_extensions_impl(action=action, **kwargs))


@pytest.mark.parametrize("action,kwargs", [
    ("list_branch_versions", {"branch_def_id": "alice-private"}),
    ("list_node_versions", {"branch_def_id": "alice-private", "node_id": "step"}),
    ("suggest_node_edit", {"branch_def_id": "alice-private", "node_id": "step"}),
])
def test_every_history_reader_answers_a_stranger_as_if_absent(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],  # noqa: F811
    action: str, kwargs: dict,
) -> None:
    base, authenticate = branch_authority_env
    _alices_private_branch(base)
    authenticate("bob")
    seen = _ext(action, **kwargs)
    assert SECRET not in json.dumps(seen)
    assert seen == {"error": "Branch 'alice-private' not found."}
    absent = _ext(action, **{**kwargs, "branch_def_id": "no-such-branch"})
    assert absent == {"error": "Branch 'no-such-branch' not found."}

    authenticate("alice")
    assert "error" not in _ext(action, **kwargs), "the owner still reads it"


def test_a_single_version_answers_a_stranger_as_if_absent(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],  # noqa: F811
) -> None:
    base, authenticate = branch_authority_env
    version_id = _alices_private_branch(base)
    from tinyassets.branch_versions import publish_branch_version

    public = _seed_branch(base, branch_def_id="bob-public", author="bob", node_ids=("s",))
    public_version = publish_branch_version(
        base, public, publisher="bob", public=True).branch_version_id

    authenticate("bob")
    assert _ext("get_branch_version", branch_version_id=version_id) == {
        "error": f"Version '{version_id}' not found."}
    assert _ext("get_branch_version", branch_version_id="no-such@00000000") == {
        "error": "Version 'no-such@00000000' not found."}

    authenticate("alice")
    assert _ext("get_branch_version", branch_version_id=version_id)["branch_version_id"] == \
        version_id
    authenticate("carol")
    assert _ext("get_branch_version", branch_version_id=public_version)[
        "branch_version_id"] == public_version


def test_a_public_branchs_unpublished_history_stays_its_authors(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],  # noqa: F811
) -> None:
    """Founder 2026-09-30 (astra round 3 on #4107): a public branch exposes only
    the versions its owner published. Its edit history -- the snapshots
    patch_branch mints on every edit -- stays readable to the author alone."""
    from tinyassets.branch_versions import publish_branch_version

    base, authenticate = branch_authority_env
    branch = _seed_branch(base, branch_def_id="alice-open", author="alice", node_ids=("s",))
    branch["node_defs"][0]["prompt_template"] = SECRET
    history = publish_branch_version(base, branch, publisher="alice",
                                     notes="patch_branch pre-patch snapshot").branch_version_id
    branch["node_defs"][0]["prompt_template"] = "the clean prompt"
    published = publish_branch_version(base, branch, publisher="alice",
                                       public=True).branch_version_id

    authenticate("bob")
    assert _ext("get_branch_version", branch_version_id=history) == {
        "error": f"Version '{history}' not found."}
    listed = _ext("list_branch_versions", branch_def_id="alice-open")
    assert [v["branch_version_id"] for v in listed["versions"]] == [published]
    assert SECRET not in json.dumps(listed)
    seen = _ext("get_branch_version", branch_version_id=published)
    assert seen["branch_version_id"] == published

    authenticate("alice")
    mine = _ext("list_branch_versions", branch_def_id="alice-open")
    assert {v["branch_version_id"] for v in mine["versions"]} == {history, published}


def test_publishing_an_identical_snapshot_marks_the_existing_row(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],  # noqa: F811
) -> None:
    """A publish whose content matches an earlier edit snapshot gets that same
    row back; it must come back MARKED, or the publish silently exposes nothing."""
    from tinyassets.branch_versions import get_branch_version, publish_branch_version

    base, _authenticate = branch_authority_env
    branch = _seed_branch(base, branch_def_id="same", author="alice", node_ids=("s",))
    first = publish_branch_version(base, branch, publisher="alice",
                                   notes="patch_branch post-patch snapshot")
    again = publish_branch_version(base, branch, publisher="alice", public=True)
    assert again.branch_version_id == first.branch_version_id
    assert get_branch_version(base, first.branch_version_id).public is True


def _legacy_versions(base: Path, rows: tuple[tuple[str, str, str], ...]) -> None:
    """A runs DB as the previous release left it: no publication mark column,
    no migration marker, and these (version_id, branch_def_id, notes) rows."""
    from tinyassets.branch_versions import _connect, initialize_branch_versions_db

    initialize_branch_versions_db(base)
    with _connect(base) as conn:
        conn.execute("DROP TABLE branch_versions_migrations")
        conn.execute("ALTER TABLE branch_versions DROP COLUMN public")
        for vid, bid, notes in rows:
            conn.execute(
                "INSERT INTO branch_versions (branch_version_id, branch_def_id, content_hash, "
                "snapshot_json, notes, publisher, published_at) VALUES (?, ?, ?, '{}', ?, "
                "'alice', '2026-01-01')", (vid, bid, vid, notes))


def _marks(base: Path) -> dict[str, bool]:
    from tinyassets.branch_versions import _connect

    with _connect(base) as conn:
        return {r[0]: bool(r[1]) for r in conn.execute(
            "SELECT branch_version_id, public FROM branch_versions")}


#: The old read rule: a non-author read a version exactly when its branch's
#: CURRENT visibility was "public" -- its history included. Everything else hid.
_LEGACY_ROWS = (
    ("open@1", "open", ""),                                  # explicit publish, public branch
    ("open@2", "open", "patch_branch post-patch snapshot"),  # history the old rule exposed
    ("closed@1", "closed", "v1 of the shape"),               # explicit publish, private branch
    ("closed@2", "closed", "patch_branch pre-patch snapshot"),
    ("legacy@1", "legacy", ""),                              # blank visibility read as private
    ("gone@1", "gone", ""),                                  # branch row no longer exists
)


def _seed_legacy_world(base: Path) -> None:
    from tinyassets.daemon_server import _connect as author_connect

    _seed_branch(base, branch_def_id="open", author="alice", node_ids=("s",))
    _seed_branch(base, branch_def_id="closed", author="alice", visibility="private",
                 node_ids=("s",))
    _seed_branch(base, branch_def_id="legacy", author="alice", node_ids=("s",))
    with author_connect(base) as conn:
        conn.execute("UPDATE branch_definitions SET visibility = '' "
                     "WHERE branch_def_id = 'legacy'")
    _legacy_versions(base, _LEGACY_ROWS)


def test_backfill_marks_exactly_what_the_old_read_rule_exposed(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],  # noqa: F811
) -> None:
    """The one-time backfill: every version of a public branch was readable
    before the mark and stays readable. A private branch's versions -- even an
    explicit publish -- a blank-visibility branch's, and an orphan's stay
    unmarked. After it, the mark alone decides what a stranger reads."""
    from tinyassets.branch_versions import initialize_branch_versions_db

    base, authenticate = branch_authority_env
    _seed_legacy_world(base)
    initialize_branch_versions_db(base)
    assert _marks(base) == {
        "open@1": True, "open@2": True,
        "closed@1": False, "closed@2": False, "legacy@1": False, "gone@1": False,
    }

    authenticate("bob")
    assert _ext("get_branch_version", branch_version_id="open@1")[
        "branch_version_id"] == "open@1"
    assert _ext("get_branch_version", branch_version_id="closed@1") == {
        "error": "Version 'closed@1' not found."}


def test_backfill_runs_once_and_later_history_stays_unmarked(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],  # noqa: F811
) -> None:
    """Idempotent and gated: a second open changes nothing, and history minted
    on a public branch AFTER the migration is not swept in by a re-run -- the
    marker row, not the data, is what stops it."""
    from tinyassets.branch_versions import (
        PUBLICATION_MARK_BACKFILL,
        _connect,
        initialize_branch_versions_db,
        publish_branch_version,
    )

    base, _authenticate = branch_authority_env
    _seed_legacy_world(base)
    initialize_branch_versions_db(base)
    first = _marks(base)
    initialize_branch_versions_db(base)
    assert _marks(base) == first

    branch = _seed_branch(base, branch_def_id="open", author="alice", node_ids=("later",))
    later = publish_branch_version(base, branch, publisher="alice",
                                   notes="patch_branch post-patch snapshot").branch_version_id
    initialize_branch_versions_db(base)
    assert _marks(base)[later] is False
    with _connect(base) as conn:
        rows = conn.execute("SELECT name, detail FROM branch_versions_migrations").fetchall()
    assert [(r[0], json.loads(r[1])) for r in rows] == [
        (PUBLICATION_MARK_BACKFILL, {"public_branches": 1, "versions_marked": 2})]

    # Delete the marker and the same data WOULD be swept: the marker is the gate.
    with _connect(base) as conn:
        conn.execute("DELETE FROM branch_versions_migrations")
    initialize_branch_versions_db(base)
    assert _marks(base)[later] is True


def test_blank_visibility_is_private_to_every_reader(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],  # noqa: F811
) -> None:
    """Private by default reaches the readers: the row mapper used to turn a
    blank visibility into "public" before any gate saw it."""
    from tinyassets.api.branches import resolve_branch_id_for_read
    from tinyassets.daemon_server import _connect as author_connect
    from tinyassets.daemon_server import get_branch_definition

    base, authenticate = branch_authority_env
    _seed_branch(base, branch_def_id="legacy", author="alice", node_ids=("s",))
    with author_connect(base) as conn:
        conn.execute("UPDATE branch_definitions SET visibility = '' "
                     "WHERE branch_def_id = 'legacy'")
    assert get_branch_definition(base, branch_def_id="legacy")["visibility"] == "private"
    authenticate("bob")
    assert resolve_branch_id_for_read("legacy", str(base)) is None
    authenticate("alice")
    assert resolve_branch_id_for_read("legacy", str(base)) == "legacy"


def test_orphan_version_cannot_borrow_a_namesakes_authority(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],  # noqa: F811
) -> None:
    """A version whose parent row is gone resolves by EXACT id only. The branch
    resolver's name fallback must not let Carol, who owns a public branch named
    like the missing id, read it as its 'author'."""
    from tinyassets.branch_versions import mark_versions_public

    base, authenticate = branch_authority_env
    _legacy_versions(base, (("gone@1", "gone", ""),))
    _seed_branch(base, branch_def_id="carols", author="carol", name="gone", node_ids=("s",))
    for reader in ("carol", "bob"):
        authenticate(reader)
        assert _ext("get_branch_version", branch_version_id="gone@1") == {
            "error": "Version 'gone@1' not found."}, reader
    mark_versions_public(base, ["gone@1"])
    authenticate("bob")
    assert _ext("get_branch_version", branch_version_id="gone@1") == {
        "error": "Version 'gone@1' not found."}


def test_backfill_waits_while_the_branch_store_is_unreadable(tmp_path: Path) -> None:
    """Versions whose branches cannot be looked up are not guessed about: no
    mark, no marker, and the next open with the store present decides."""
    from tinyassets.branch_versions import initialize_branch_versions_db
    from tinyassets.daemon_server import initialize_author_server
    from tinyassets.storage import db_path

    _legacy_versions(tmp_path, (("open@1", "open", ""),))
    assert not db_path(tmp_path).exists()
    initialize_branch_versions_db(tmp_path)
    assert _marks(tmp_path) == {"open@1": False}
    assert not db_path(tmp_path).exists(), "the runs migration never creates the branch store"

    initialize_author_server(tmp_path)
    _seed_branch(tmp_path, branch_def_id="open", author="alice", node_ids=("s",))
    initialize_branch_versions_db(tmp_path)
    assert _marks(tmp_path) == {"open@1": True}


@pytest.mark.parametrize("action", ["fork_tree", "describe_branch"])
def test_lineage_hides_unpublished_versions(
    branch_authority_env, action,  # noqa: F811 - imported fixture
):
    from tinyassets.branch_versions import publish_branch_version

    base, authenticate = branch_authority_env
    branch = _seed_branch(base, branch_def_id="root", author="alice", node_ids=("s",))
    private_version = publish_branch_version(base, branch, publisher="alice").branch_version_id
    _seed_branch(base, branch_def_id="child", author="alice", fork_from=private_version)
    authenticate("bob")
    seen = _ext(action, branch_def_id="root")
    assert "child" not in json.dumps(seen), seen
    authenticate("alice")
    assert "child" in json.dumps(_ext(action, branch_def_id="root"))


def test_public_node_history_does_not_expose_private_edit_audits(
    branch_authority_env,  # noqa: F811 - imported fixture
):
    from tinyassets.daemon_server import update_branch_definition
    from tinyassets.runs import record_node_edit_audit

    base, authenticate = branch_authority_env
    branch = _seed_branch(base, branch_def_id="node-history", author="alice", node_ids=("s",))
    current = branch["node_defs"][0]
    record_node_edit_audit(
        base, branch_def_id="node-history", version_before=1, version_after=2,
        nodes_changed=["s"], node_before={**current, "prompt_template": SECRET},
        node_after=current,
    )
    update_branch_definition(base, branch_def_id="node-history", updates={"version": 2})
    authenticate("bob")
    seen = _ext("list_node_versions", branch_def_id="node-history", node_id="s")
    assert SECRET not in json.dumps(seen), seen
    assert len(seen["versions"]) == 1
    authenticate("alice")
    assert SECRET in json.dumps(
        _ext("list_node_versions", branch_def_id="node-history", node_id="s"))


@pytest.mark.parametrize("reader", ["leaderboard", "canonical", "handoff"])
def test_indirect_readers_hide_unpublished_versions(
    branch_authority_env, reader,  # noqa: F811 - imported fixture
):
    from tinyassets.api.canonical_dispatch import _latest_published_version_id
    from tinyassets.api.quality_leaderboard import _latest_active_version_id
    from tinyassets.branch_versions import mark_versions_public, publish_branch_version
    from tinyassets.handoffs.models import HandoffValidationError
    from tinyassets.handoffs.service import list_declarations

    base, authenticate = branch_authority_env
    branch = _seed_branch(base, branch_def_id="indirect", author="alice", node_ids=("s",))
    vid = publish_branch_version(base, branch, publisher="alice").branch_version_id
    authenticate("bob")
    if reader == "handoff":
        with pytest.raises(HandoffValidationError, match="not found"):
            list_declarations(actor_id="bob", base_path=base, branch_version_id=vid)
        mark_versions_public(base, [vid])
        assert list_declarations(actor_id="bob", base_path=base,
                                 branch_version_id=vid)["branch_version_id"] == vid
    else:
        read = (_latest_active_version_id if reader == "leaderboard"
                else _latest_published_version_id)
        assert not read(base, branch_def_id="indirect")
        mark_versions_public(base, [vid])
        assert read(base, branch_def_id="indirect") == vid


@pytest.mark.parametrize("actor,provenance", [("bob", "own"), ("alice", "public-foreign")])
def test_nested_invoke_cannot_run_unpublished_history(
    branch_authority_env, monkeypatch, actor, provenance,  # noqa: F811 - imported fixture
):
    from types import SimpleNamespace

    from tinyassets.branch_versions import mark_versions_public, publish_branch_version
    from tinyassets.branches import NodeDefinition
    from tinyassets.graph_compiler import (
        BranchExecutionContext,
        CompilerError,
        _build_invoke_branch_version_node,
    )

    base, _authenticate = branch_authority_env
    branch = _seed_branch(base, branch_def_id="nested", author="alice", node_ids=("s",))
    vid = publish_branch_version(base, branch, publisher="alice").branch_version_id
    called = []

    def launch(*args, **kwargs):
        called.append(kwargs["branch_version_id"])
        return SimpleNamespace(run_id="child-run")

    monkeypatch.setattr("tinyassets.runs.execute_branch_version_async", launch)
    node = NodeDefinition(node_id="invoke", display_name="Invoke",
                          invoke_branch_version_spec={"branch_version_id": vid,
                                                      "wait_mode": "async"})
    invoke = _build_invoke_branch_version_node(
        node, base_path=base, event_sink=None,
        execution_context=BranchExecutionContext(actor=actor, universe_id="u",
                                                 caller_provenance=provenance),
    )
    with pytest.raises(CompilerError, match="not available"):
        invoke({})
    assert called == []
    mark_versions_public(base, [vid])
    invoke({})
    assert called == [vid]


@pytest.mark.parametrize("owner,definition_author,permission,provenance,runs", [
    ("alice", "alice", "admin", "own", True),     # the owner's automation, own history
    ("alice", "alice", "read", "own", False),     # author no longer owns the universe
    # astra refute 2026-09-30: Bob's automation in a universe Alice co-administers
    # runs Alice's public definition, which pins Alice's unpublished version. The
    # run is Bob's, not Alice's, so her history stays hers.
    ("bob", "alice", "admin", "own", False),
    # And the reverse: a co-admin's definition inside Alice's run does not get to
    # choose to pin Alice's unpublished history.
    ("alice", "bob", "admin", "own", False),
    ("alice", "alice", "admin", "public-foreign", False),  # foreign code
])
def test_an_owners_universe_run_uses_its_own_unpublished_version(
    branch_authority_env, monkeypatch,  # noqa: F811 - imported fixture
    owner, definition_author, permission, provenance, runs,
):
    """The mark gates other people, not the owner. An automation executes as
    ``universe:<id>`` and records its owner on the run row; an unpublished
    version is usable only when the run's persisted owner is its author."""
    from types import SimpleNamespace

    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.branches import NodeDefinition
    from tinyassets.daemon_server import grant_universe_access
    from tinyassets.graph_compiler import (
        BranchExecutionContext,
        CompilerError,
        _build_invoke_branch_version_node,
    )
    from tinyassets.runs import _connect, initialize_runs_db

    base, _authenticate = branch_authority_env
    grant_universe_access(base, universe_id="u", actor_id="alice", permission=permission,
                          granted_by="alice")
    branch = _seed_branch(base, branch_def_id="owned", author="alice", node_ids=("s",))
    vid = publish_branch_version(base, branch, publisher="alice").branch_version_id
    initialize_runs_db(base)
    with _connect(base) as conn:
        conn.execute(
            "INSERT INTO runs (run_id, branch_def_id, thread_id, status, actor, owner_user_id,"
            " started_at, queue_universe_id) VALUES ('parent', 'p', 't', 'running',"
            " 'universe:u', ?, 0, 'u')", (owner,))
    called = []

    def launch(*args, **kwargs):
        called.append(kwargs["branch_version_id"])
        return SimpleNamespace(run_id="child-run")

    monkeypatch.setattr("tinyassets.runs.execute_branch_version_async", launch)
    node = NodeDefinition(node_id="invoke", display_name="Invoke",
                          invoke_branch_version_spec={"branch_version_id": vid,
                                                      "wait_mode": "async"})
    invoke = _build_invoke_branch_version_node(
        node, base_path=base, event_sink=None, parent_run_id="parent",
        execution_context=BranchExecutionContext(
            actor="universe:u", universe_id="u", caller_provenance=provenance,
            owner_user_id=owner, definition_author=definition_author),
    )
    if runs:
        invoke({})
        assert called == [vid]
    else:
        with pytest.raises(CompilerError, match="not available"):
            invoke({})
        assert called == []


def test_suggest_edit_filters_private_run_context(
    branch_authority_env, monkeypatch,  # noqa: F811 - imported fixture
):
    base, authenticate = branch_authority_env
    _seed_branch(base, branch_def_id="suggest", author="alice", node_ids=("s",))
    authenticate("bob")
    monkeypatch.setattr("tinyassets.runs.list_runs", lambda *a, **k: [{"run_id": "private"}])
    monkeypatch.setattr("tinyassets.runs.node_output_from_run",
                        lambda *a, **k: {"detail": {"output": SECRET}})
    monkeypatch.setattr("tinyassets.runs.list_judgments",
                        lambda *a, **k: [{"run_id": "private", "text": SECRET}])
    monkeypatch.setattr("tinyassets.runs.get_run", lambda *a, **k: {"run_id": "private"})
    monkeypatch.setattr("tinyassets.api.runs._run_read_allowed", lambda row: False)
    seen = _ext("suggest_node_edit", branch_def_id="suggest", node_id="s")
    assert SECRET not in json.dumps(seen), seen



def test_version_reads_release_sqlite_connections(tmp_path, monkeypatch):
    """Reading versions must release descriptors without waiting for cyclic GC."""
    import sqlite3

    from tinyassets import branch_versions

    connect = branch_versions._connect
    opened = []

    def retained_connection(base):
        connection = connect(base)
        opened.append(connection)
        return connection

    monkeypatch.setattr(branch_versions, "_connect", retained_connection)
    try:
        assert branch_versions.get_branch_version(tmp_path, "missing") is None
        assert opened
        for connection in opened:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
    finally:
        for connection in opened:
            connection.close()
