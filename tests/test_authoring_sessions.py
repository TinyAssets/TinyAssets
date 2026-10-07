"""Authoring session lifecycle — owner-scoped drafts, inspection, atomic edits,
explicit publication, and the ``extensions`` router half.

Requirement source:
``openspec/changes/archive/2026-08-26-complete-independent-full-platform-targets/
specs/node-authoring-and-autoresearch/spec.md`` (tasks 4.1-4.3, 4.5).

Covered requirements:
  - Authoring sessions are authenticated, owner-scoped drafts
  - Every authored definition remains inspectable at full, diff, summary fidelity
  - Draft edits are atomic structural operations with an escape hatch
  - Testing never publishes and publication is an explicit versioned transition
  - Authoring has equivalent browser, local-host, and contributor paths
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

CANONICAL_HANDLES = {
    "read_graph",
    "write_graph",
    "run_graph",
    "read_page",
    "write_page",
    "converse",
    "get_status",
}


def _become(user_id: str) -> None:
    """Sign in as ``user_id``.

    These tests used to set ``UNIVERSE_SERVER_USER``, which named the actor by
    environment variable -- authority from a string anybody can set. The
    autouse operator fixture rebinds between tests, so this does not leak.
    """
    from tinyassets.auth import middleware as _mw
    from tinyassets.auth.provider import Identity

    _mw._current_identity.set(
        Identity(
            user_id=user_id,
            username=user_id,
            display_name=user_id,
            capabilities=[
                "tinyassets.universe.read",
                "tinyassets.universe.write",
                "tinyassets.universe.admin",
                "tinyassets.extensions.read",
                "tinyassets.extensions.write",
            ],
        )
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated data dir + a named actor, matching the extensions-action tests."""
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    _become("alice")
    return base


@pytest.fixture
def service(env):
    from tinyassets.authoring import service as svc

    return svc


def _start(service, actor="alice", kind="node", **kwargs) -> dict:
    return service.start_session(actor_id=actor, artifact_kind=kind, **kwargs)


def _node_ops(name="Recipe checker"):
    """A minimal batch that makes a node draft structurally complete."""
    return [
        {"op": "set", "path": "name", "value": name},
        {
            "op": "append",
            "path": "node_defs",
            "value": {
                "node_id": "check",
                "display_name": "Check",
                "phase": "draft",
                "prompt_template": "Check {recipe}",
                "input_keys": ["recipe"],
                "output_keys": ["notes"],
            },
        },
        {
            "op": "append",
            "path": "graph_nodes",
            "value": {"id": "check", "node_def_id": "check"},
        },
        {"op": "append", "path": "edges", "value": {"from_node": "check", "to_node": "END"}},
        {
            "op": "append",
            "path": "state_schema",
            "value": {"name": "recipe", "type": "str"},
        },
        {
            "op": "append",
            "path": "state_schema",
            "value": {"name": "notes", "type": "str"},
        },
        {"op": "set", "path": "entry_point", "value": "check"},
    ]


def _complete_node_draft(service, actor="alice"):
    session = _start(service, actor=actor, sketch="check my recipes")
    service.apply_edit_batch(
        actor_id=actor,
        session_id=session["session_id"],
        operations=_node_ops(),
    )
    return session["session_id"]


# ---------------------------------------------------------------------------
# Requirement: sessions are authenticated, owner-scoped drafts
# ---------------------------------------------------------------------------


def test_start_from_sketch_creates_owner_scoped_skeleton(service):
    session = _start(service, sketch="track my sourdough starter")

    assert session["owner_id"] == "alice"
    assert session["status"] == "active"
    assert session["seed_mode"] == "sketch"
    assert session["artifact_kind"] == "node"
    assert session["definition"]["sketch"] == "track my sourdough starter"
    # An empty *structural* skeleton: every execution-affecting section present.
    definition = session["definition"]
    for key in (
        "node_defs",
        "graph_nodes",
        "edges",
        "state_schema",
        "entry_point",
        "io_manifest",
        "effects",
        "sandbox_policy",
        "composes",
    ):
        assert key in definition
    # Only completeness blockers, never structural corruption.
    from tinyassets.authoring import models

    codes = {i.code for i in models.validate_definition(definition, artifact_kind="node")}
    assert codes <= {
        "definition.name_required",
        "definition.no_nodes",
        "definition.entry_point_required",
    }


def test_anonymous_actor_cannot_author(service):
    from tinyassets.authoring.models import AuthoringAccessError

    with pytest.raises(AuthoringAccessError):
        _start(service, actor="anonymous", sketch="x")


def test_multiple_seeds_are_refused(service):
    from tinyassets.authoring.models import AuthoringValidationError

    with pytest.raises(AuthoringValidationError) as exc:
        _start(service, sketch="a", base_version_id="ver_whatever")
    assert any(i.code == "seed.exactly_one_required" for i in exc.value.issues)


def test_no_seed_is_refused(service):
    from tinyassets.authoring.models import AuthoringValidationError

    with pytest.raises(AuthoringValidationError):
        _start(service)


def test_inaccessible_base_version_does_not_leak(service):
    from tinyassets.authoring.models import AuthoringAccessError

    other = _complete_node_draft(service, actor="bob")
    service.run_test(actor_id="bob", session_id=other)
    published = service.publish_session(
        actor_id="bob",
        session_id=other,
        expected_version=service.inspect_session(actor_id="bob", session_id=other)[
            "draft_version"
        ],
        change_message="bob's node",
        visibility="private",
    )
    version_id = published["version"]["version_id"]

    with pytest.raises(AuthoringAccessError) as exc:
        _start(service, actor="alice", base_version_id=version_id)
    # No detail about the inaccessible object.
    assert "bob" not in str(exc.value)
    assert "bob's node" not in str(exc.value)


def test_non_owner_read_fails_closed_indistinguishably(service):
    from tinyassets.authoring.models import AuthoringAccessError

    session_id = _complete_node_draft(service, actor="alice")

    with pytest.raises(AuthoringAccessError) as real:
        service.inspect_session(actor_id="mallory", session_id=session_id)
    with pytest.raises(AuthoringAccessError) as fake:
        service.inspect_session(actor_id="mallory", session_id="ses_does_not_exist")
    assert str(real.value) == str(fake.value)


def test_non_owner_cannot_edit_or_publish(service):
    from tinyassets.authoring.models import AuthoringAccessError

    session_id = _complete_node_draft(service, actor="alice")

    with pytest.raises(AuthoringAccessError):
        service.apply_edit_batch(
            actor_id="mallory",
            session_id=session_id,
            operations=[{"op": "set", "path": "name", "value": "stolen"}],
        )
    with pytest.raises(AuthoringAccessError):
        service.publish_session(
            actor_id="mallory",
            session_id=session_id,
            expected_version=1,
            change_message="stolen",
        )


def test_resume_from_published_version_copies_definition_not_execution_data(service):
    session_id = _complete_node_draft(service)
    service.run_test(actor_id="alice", session_id=session_id)
    draft_version = service.inspect_session(actor_id="alice", session_id=session_id)[
        "draft_version"
    ]
    published = service.publish_session(
        actor_id="alice",
        session_id=session_id,
        expected_version=draft_version,
        change_message="v1",
    )
    version = published["version"]

    resumed = _start(service, base_version_id=version["version_id"])
    assert resumed["seed_mode"] == "artifact"
    assert resumed["seed_ref"] == version["version_id"]
    assert resumed["definition"]["node_defs"] == version["definition"]["node_defs"]
    assert resumed["lineage"]["parent_version_id"] == version["version_id"]
    # No execution/instance data rides along.
    for banned in ("runs", "test_events", "credentials", "instance_state", "provenance"):
        assert banned not in resumed["definition"]


# ---------------------------------------------------------------------------
# Requirement: full / diff / summary fidelity
# ---------------------------------------------------------------------------


def test_full_view_exposes_every_execution_affecting_declaration(service):
    session_id = _complete_node_draft(service)
    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        operations=[
            {
                "op": "append",
                "path": "effects",
                "value": {
                    "name": "push_notes",
                    "sink": "github_pull_request",
                    "destination": "acme/recipes",
                    "reversible": False,
                    "credential_class": "github_token",
                },
            },
        ],
    )

    view = service.inspect_session(actor_id="alice", session_id=session_id, view="full")
    definition = view["definition"]
    assert view["view"] == "full"
    assert definition["effects"][0]["destination"] == "acme/recipes"
    assert definition["node_defs"][0]["prompt_template"] == "Check {recipe}"
    assert "validation" in view and isinstance(view["validation"]["issues"], list)
    assert view["definition_hash"]


def test_summary_view_never_hides_effects_or_blockers(service):
    session = _start(service, sketch="publish my notes")
    session_id = session["session_id"]
    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        operations=[
            {
                "op": "append",
                "path": "effects",
                "value": {
                    "name": "push_notes",
                    "sink": "github_pull_request",
                    "destination": "acme/recipes",
                    "reversible": False,
                    "credential_class": "github_token",
                },
            },
        ],
    )

    summary = service.inspect_session(
        actor_id="alice", session_id=session_id, view="summary"
    )
    assert summary["view"] == "summary"
    assert summary["effects"], "a summary must not present a false no-effect view"
    assert summary["effects"][0]["destination"] == "acme/recipes"
    assert summary["effects"][0]["credential_class"] == "github_token"
    assert summary["blockers"], "unresolved validation errors stay visible"
    assert "inputs" in summary and "outputs" in summary and "stages" in summary


def test_diff_is_anchored_to_a_session_event(service):
    session_id = _complete_node_draft(service)
    events = service.inspect_session(
        actor_id="alice", session_id=session_id, view="history"
    )["events"]
    anchor = events[0]["event_id"]

    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        operations=[{"op": "set", "path": "name", "value": "Renamed"}],
    )
    diff = service.inspect_session(
        actor_id="alice", session_id=session_id, view="diff", anchor=anchor
    )
    assert diff["anchor"]["event_id"] == anchor
    changed = {entry["path"] for entry in diff["changes"]}
    assert "name" in changed


def test_missing_diff_anchor_reports_and_invents_nothing(service):
    session_id = _complete_node_draft(service)

    with pytest.raises(Exception) as exc:
        service.inspect_session(
            actor_id="alice",
            session_id=session_id,
            view="diff",
            anchor="evt_expired_or_never_existed",
        )
    assert "anchor" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Requirement: atomic edit batches with an escape hatch
# ---------------------------------------------------------------------------


def test_valid_batch_commits_once_as_one_event(service):
    session = _start(service, sketch="s")
    session_id = session["session_id"]
    before = service.inspect_session(actor_id="alice", session_id=session_id)

    result = service.apply_edit_batch(
        actor_id="alice", session_id=session_id, operations=_node_ops()
    )

    assert result["draft_version"] == before["draft_version"] + 1
    history = service.inspect_session(
        actor_id="alice", session_id=session_id, view="history"
    )["events"]
    edit_events = [e for e in history if e["event_type"] == "edit"]
    assert len(edit_events) == 1
    assert edit_events[0]["payload"]["operation_count"] == len(_node_ops())


def test_one_invalid_operation_rejects_the_whole_batch(service):
    session_id = _complete_node_draft(service)
    before = service.inspect_session(actor_id="alice", session_id=session_id)

    from tinyassets.authoring.models import AuthoringValidationError

    with pytest.raises(AuthoringValidationError):
        service.apply_edit_batch(
            actor_id="alice",
            session_id=session_id,
            operations=[
                {"op": "set", "path": "name", "value": "Renamed"},
                {
                    "op": "append",
                    "path": "graph_nodes",
                    "value": {"id": "ghost", "node_def_id": "missing_def"},
                },
            ],
        )

    after = service.inspect_session(actor_id="alice", session_id=session_id)
    assert after["draft_version"] == before["draft_version"]
    assert after["definition"] == before["definition"]
    assert after["definition_hash"] == before["definition_hash"]


def test_unknown_operation_verb_and_bad_path_are_machine_readable(service):
    from tinyassets.authoring.models import AuthoringValidationError

    session_id = _complete_node_draft(service)

    with pytest.raises(AuthoringValidationError) as exc:
        service.apply_edit_batch(
            actor_id="alice",
            session_id=session_id,
            operations=[{"op": "obliterate", "path": "name"}],
        )
    assert {i.code for i in exc.value.issues} == {"op.unknown_verb"}

    with pytest.raises(AuthoringValidationError) as exc:
        service.apply_edit_batch(
            actor_id="alice",
            session_id=session_id,
            operations=[{"op": "set", "path": "owner_id", "value": "mallory"}],
        )
    assert {i.code for i in exc.value.issues} == {"op.path_not_editable"}


def test_composition_cycle_is_rejected(service):
    from tinyassets.authoring.models import AuthoringValidationError

    session_id = _complete_node_draft(service)
    session = service.inspect_session(actor_id="alice", session_id=session_id)

    with pytest.raises(AuthoringValidationError) as exc:
        service.apply_edit_batch(
            actor_id="alice",
            session_id=session_id,
            operations=[
                {
                    "op": "append",
                    "path": "composes",
                    "value": {"artifact_id": session["artifact_id"]},
                },
            ],
        )
    assert any("cycle" in i.code for i in exc.value.issues)


def test_store_commit_is_itself_compare_and_swap(service, env):
    """The store's CAS must hold independently of the service pre-check.

    Two callers can both pass the service-level version check and race into the
    store; the store is the layer that must refuse the second one. Asserted here
    directly because a service-level guard would otherwise mask a broken CAS.
    """
    from tinyassets.authoring.models import AuthoringConflictError
    from tinyassets.authoring.store import AuthoringStore

    session_id = _complete_node_draft(service)
    store = AuthoringStore()
    stored = store.get_session(session_id, actor_id="alice")

    store.commit_definition(
        session_id,
        actor_id="alice",
        expected_version=stored.draft_version,
        definition={**stored.definition, "name": "First"},
        event_type="edit",
        payload={"operation_count": 1, "operations": []},
    )
    with pytest.raises(AuthoringConflictError):
        store.commit_definition(
            session_id,
            actor_id="alice",
            expected_version=stored.draft_version,  # stale
            definition={**stored.definition, "name": "Second"},
            event_type="edit",
            payload={"operation_count": 1, "operations": []},
        )
    assert store.get_session(session_id, actor_id="alice").definition["name"] == "First"


def test_apply_operations_never_mutates_the_caller_document(env):
    """A rejected batch must leave the in-memory draft untouched, not just the DB."""
    from tinyassets.authoring import models

    definition = models.skeleton_for("node", sketch="pure")
    snapshot = models.canonical_json(definition)

    with pytest.raises(models.AuthoringValidationError):
        models.apply_operations(
            definition,
            [
                {"op": "set", "path": "name", "value": "Mutated"},
                {"op": "set", "path": "not_a_section", "value": 1},
            ],
            artifact_kind="node",
        )
    assert models.canonical_json(definition) == snapshot

    # A successful batch also returns a new document rather than aliasing.
    updated = models.apply_operations(
        definition, [{"op": "set", "path": "name", "value": "Named"}], artifact_kind="node"
    )
    assert updated["name"] == "Named"
    assert models.canonical_json(definition) == snapshot


def test_concurrent_edit_batches_do_not_lose_an_event(service):
    """CAS on draft_version: a stale expected_version loses, never silently wins."""
    from tinyassets.authoring.models import AuthoringConflictError

    session_id = _complete_node_draft(service)
    stale = service.inspect_session(actor_id="alice", session_id=session_id)[
        "draft_version"
    ]

    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        operations=[{"op": "set", "path": "name", "value": "First"}],
        expected_version=stale,
    )
    with pytest.raises(AuthoringConflictError):
        service.apply_edit_batch(
            actor_id="alice",
            session_id=session_id,
            operations=[{"op": "set", "path": "name", "value": "Second"}],
            expected_version=stale,
        )
    assert (
        service.inspect_session(actor_id="alice", session_id=session_id)["definition"][
            "name"
        ]
        == "First"
    )


# ---------------------------------------------------------------------------
# Requirement: testing never publishes; publication is explicit + versioned
# ---------------------------------------------------------------------------


def test_successful_test_publishes_nothing(service):
    session_id = _complete_node_draft(service)
    result = service.run_test(actor_id="alice", session_id=session_id)

    assert result["published"] is False
    assert service.list_versions(actor_id="alice") == []


def test_publish_requires_a_test_of_the_exact_draft_version(service):
    from tinyassets.authoring.models import AuthoringValidationError

    session_id = _complete_node_draft(service)
    draft_version = service.inspect_session(actor_id="alice", session_id=session_id)[
        "draft_version"
    ]

    with pytest.raises(AuthoringValidationError) as exc:
        service.publish_session(
            actor_id="alice",
            session_id=session_id,
            expected_version=draft_version,
            change_message="untested",
        )
    assert any(i.code == "publish.untested_version" for i in exc.value.issues)


def test_publish_creates_one_immutable_version_with_provenance(service):
    session_id = _complete_node_draft(service)
    service.run_test(actor_id="alice", session_id=session_id)
    inspected = service.inspect_session(actor_id="alice", session_id=session_id)

    published = service.publish_session(
        actor_id="alice",
        session_id=session_id,
        expected_version=inspected["draft_version"],
        change_message="first release",
    )
    version = published["version"]

    assert version["version_no"] == 1
    assert version["definition_hash"] == inspected["definition_hash"]
    assert version["parent_version_id"] == ""
    assert version["change_message"] == "first release"
    assert version["owner_id"] == "alice"
    assert version["provenance"]["source_session_id"] == session_id
    assert version["provenance"]["source_draft_version"] == inspected["draft_version"]
    assert version["evidence"]["tests"], "publication records its test evidence"
    assert version["created_at"]


def test_publication_fails_when_the_draft_advanced_after_review(service):
    from tinyassets.authoring.models import AuthoringConflictError

    session_id = _complete_node_draft(service)
    service.run_test(actor_id="alice", session_id=session_id)
    reviewed = service.inspect_session(actor_id="alice", session_id=session_id)[
        "draft_version"
    ]

    # The session advances after review but before publication commits.
    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        operations=[{"op": "set", "path": "name", "value": "Newer"}],
    )

    with pytest.raises(AuthoringConflictError):
        service.publish_session(
            actor_id="alice",
            session_id=session_id,
            expected_version=reviewed,
            change_message="stale review",
        )
    assert service.list_versions(actor_id="alice") == []


def test_later_edits_publish_a_new_version_and_never_mutate_the_old(service):
    session_id = _complete_node_draft(service)
    service.run_test(actor_id="alice", session_id=session_id)
    v1 = service.publish_session(
        actor_id="alice",
        session_id=session_id,
        expected_version=service.inspect_session(
            actor_id="alice", session_id=session_id
        )["draft_version"],
        change_message="v1",
    )["version"]

    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        operations=[{"op": "set", "path": "name", "value": "Renamed"}],
    )
    service.run_test(actor_id="alice", session_id=session_id)
    v2 = service.publish_session(
        actor_id="alice",
        session_id=session_id,
        expected_version=service.inspect_session(
            actor_id="alice", session_id=session_id
        )["draft_version"],
        change_message="v2",
    )["version"]

    assert v2["version_no"] == 2
    assert v2["parent_version_id"] == v1["version_id"]
    assert v2["artifact_id"] == v1["artifact_id"]
    assert v2["definition_hash"] != v1["definition_hash"]
    stored_v1 = service.get_version(actor_id="alice", version_id=v1["version_id"])
    assert stored_v1["definition"] == v1["definition"]
    assert stored_v1["definition"]["name"] == "Recipe checker"


def test_failed_publication_leaves_draft_inspectable(service):
    from tinyassets.authoring.models import AuthoringValidationError

    session = _start(service, sketch="incomplete")
    session_id = session["session_id"]

    with pytest.raises(AuthoringValidationError):
        service.publish_session(
            actor_id="alice",
            session_id=session_id,
            expected_version=session["draft_version"],
            change_message="nope",
        )
    assert service.inspect_session(actor_id="alice", session_id=session_id)["definition"][
        "sketch"
    ] == "incomplete"


# ---------------------------------------------------------------------------
# Requirement: equivalent browser / local-host / contributor paths
# ---------------------------------------------------------------------------


def test_contributor_import_records_source_provenance(service):
    session_id = _complete_node_draft(service)
    definition = service.inspect_session(actor_id="alice", session_id=session_id)[
        "definition"
    ]

    imported = service.import_definition(
        actor_id="alice",
        artifact_kind="node",
        definition=definition,
        source_provenance={
            "kind": "contributor_source",
            "repo": "acme/recipes",
            "commit": "deadbeef",
            "review": "PR-42",
        },
        change_message="materialized from source",
    )
    version = imported["version"]
    assert version["provenance"]["source"]["kind"] == "contributor_source"
    assert version["provenance"]["source"]["review"] == "PR-42"
    # Same inspection contract as the chat path.
    full = service.get_version(actor_id="alice", version_id=version["version_id"])
    assert full["definition"]["node_defs"] == definition["node_defs"]


# ---------------------------------------------------------------------------
# Task 4.3 — router half, no new advertised handle
# ---------------------------------------------------------------------------


@pytest.fixture
def us(env):
    from tinyassets import universe_server as mod

    importlib.reload(mod)
    yield mod
    importlib.reload(mod)


def test_authoring_actions_add_no_advertised_handle(us):
    advertised = {t.name for t in asyncio.run(us.mcp.list_tools(run_middleware=True))}
    assert advertised == CANONICAL_HANDLES


# ---------------------------------------------------------------------------
# Publication gates found by the 2026-07-25 cross-family review
# ---------------------------------------------------------------------------


def test_a_recurring_definition_hash_cannot_reuse_an_older_version_test(service):
    """Edit away and back: the old test's hash matches, its version does not."""
    from tinyassets.authoring.models import AuthoringValidationError

    session_id = _complete_node_draft(service)
    service.run_test(actor_id="alice", session_id=session_id)  # tested at v2

    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        operations=[{"op": "set", "path": "name", "value": "Renamed"}],
    )
    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        operations=[{"op": "set", "path": "name", "value": "Recipe checker"}],
    )
    inspected = service.inspect_session(actor_id="alice", session_id=session_id)
    assert inspected["draft_version"] == 4  # same hash as v2, different version

    with pytest.raises(AuthoringValidationError) as exc:
        service.publish_session(
            actor_id="alice",
            session_id=session_id,
            expected_version=inspected["draft_version"],
            change_message="stale test evidence",
        )
    assert any(i.code == "publish.untested_version" for i in exc.value.issues)
    assert service.list_versions(actor_id="alice") == []

    # Re-testing this version unblocks it.
    service.run_test(actor_id="alice", session_id=session_id)
    published = service.publish_session(
        actor_id="alice",
        session_id=session_id,
        expected_version=inspected["draft_version"],
        change_message="freshly tested",
    )
    assert published["version"]["version_no"] == 1


def test_the_same_draft_version_cannot_be_published_twice(service):
    from tinyassets.authoring.models import AuthoringConflictError

    session_id = _complete_node_draft(service)
    service.run_test(actor_id="alice", session_id=session_id)
    draft_version = service.inspect_session(actor_id="alice", session_id=session_id)[
        "draft_version"
    ]
    service.publish_session(
        actor_id="alice",
        session_id=session_id,
        expected_version=draft_version,
        change_message="v1",
    )

    with pytest.raises(AuthoringConflictError) as exc:
        service.publish_session(
            actor_id="alice",
            session_id=session_id,
            expected_version=draft_version,
            change_message="v1 again",
        )
    assert "already published" in str(exc.value)
    assert len(service.list_versions(actor_id="alice")) == 1


def test_store_publish_is_atomic_against_a_concurrent_draft_advance(service, env):
    """The reviewed version is re-checked *inside* the insert transaction."""
    from tinyassets.authoring.models import AuthoringConflictError
    from tinyassets.authoring.store import AuthoringStore

    session_id = _complete_node_draft(service)
    service.run_test(actor_id="alice", session_id=session_id)
    store = AuthoringStore()
    reviewed = store.get_session(session_id, actor_id="alice")

    def publish_reviewed():
        store.publish_version(
            artifact_id=reviewed.artifact_id,
            artifact_kind=reviewed.artifact_kind,
            owner_id="alice",
            visibility="public",
            definition=reviewed.definition,
            definition_hash=reviewed.definition_hash,
            change_message="stale",
            provenance={"source_draft_version": reviewed.draft_version},
            evidence={},
            source_session_id=session_id,
            expected_draft_version=reviewed.draft_version,
        )

    # Race 1: the draft advances to a *different* definition after review.
    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        operations=[{"op": "set", "path": "name", "value": "Advanced"}],
    )
    with pytest.raises(AuthoringConflictError):
        publish_reviewed()

    # Race 2: the draft advances back to the *same* definition, so the hashes
    # match again and only the version check can catch it. Without this case the
    # hash check masks a broken version check.
    service.apply_edit_batch(
        actor_id="alice",
        session_id=session_id,
        operations=[{"op": "set", "path": "name", "value": "Recipe checker"}],
    )
    current = store.get_session(session_id, actor_id="alice")
    assert current.definition_hash == reviewed.definition_hash
    assert current.draft_version != reviewed.draft_version
    with pytest.raises(AuthoringConflictError):
        publish_reviewed()

    assert service.list_versions(actor_id="alice") == []


def test_an_expired_draft_is_readable_but_not_writable(service, env):
    """Retention is enforced, not merely stored."""
    from tinyassets.authoring.models import AuthoringValidationError
    from tinyassets.authoring.store import AuthoringStore

    session_id = _complete_node_draft(service)
    store = AuthoringStore()
    with store._open(write=True) as conn:  # noqa: SLF001 — direct fixture setup
        conn.execute(
            "UPDATE authoring_sessions SET retention_until = ? WHERE session_id = ?",
            ("2000-01-01T00:00:00+00:00", session_id),
        )

    # Reads still work so the owner can see what lapsed.
    assert service.inspect_session(actor_id="alice", session_id=session_id)["owner_id"] == (
        "alice"
    )

    for call in (
        lambda: service.apply_edit_batch(
            actor_id="alice",
            session_id=session_id,
            operations=[{"op": "set", "path": "name", "value": "x"}],
        ),
        lambda: service.run_test(actor_id="alice", session_id=session_id),
        lambda: service.start_session(actor_id="alice", resume_session_id=session_id),
    ):
        with pytest.raises(AuthoringValidationError) as exc:
            call()
        assert any(i.code == "session.retention_expired" for i in exc.value.issues)
