"""Credential-bound branch read, provenance, and lineage authority."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest


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
def branch_authority_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authenticate_request: Callable[[str | None], None],
) -> tuple[Path, Callable[[str | None], None]]:
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    _become("environment-owner")

    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(base)
    authenticate_request(None)
    yield base, authenticate_request
    authenticate_request(None)


def _call_raw(action: str, **kwargs: Any) -> str:
    from tinyassets.api import branches

    return branches._dispatch_branch_action(
        action,
        branches._BRANCH_ACTIONS[action],
        kwargs,
    )


def _call(action: str, **kwargs: Any) -> dict[str, Any]:
    return json.loads(_call_raw(action, **kwargs))


def _seed_branch(
    base: Path,
    *,
    branch_def_id: str,
    author: str,
    visibility: str = "public",
    name: str | None = None,
    fork_from: str | None = None,
    parent_def_id: str | None = None,
    node_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    from tinyassets.daemon_server import save_branch_definition

    node_defs = [
        {
            "node_id": node_id,
            "display_name": node_id.replace("_", " ").title(),
            "description": f"{node_id} reusable node",
            "phase": "custom",
            "input_keys": [],
            "output_keys": [],
            "prompt_template": f"Run {node_id}.",
            "author": author,
        }
        for node_id in node_ids
    ]
    return save_branch_definition(
        base,
        branch_def={
            "branch_def_id": branch_def_id,
            "name": name or branch_def_id,
            "description": "",
            "author": author,
            "domain_id": "workflow",
            "visibility": visibility,
            "fork_from": fork_from,
            "parent_def_id": parent_def_id,
            "node_defs": node_defs,
            "graph_nodes": [
                {"id": node_id, "node_def_id": node_id, "position": index}
                for index, node_id in enumerate(node_ids)
            ],
            "edges": (
                [
                    {"from_node": "START", "to_node": node_ids[0]},
                    {"from_node": node_ids[0], "to_node": "END"},
                ]
                if node_ids
                else []
            ),
            "conditional_edges": [],
            "state_schema": [],
            "entry_point": node_ids[0] if node_ids else "",
        },
    )


def _publish(base: Path, branch: dict[str, Any], publisher: str, *, public: bool = False) -> str:
    from tinyassets.branch_versions import publish_branch_version

    return publish_branch_version(
        base,
        branch,
        publisher=publisher,
        public=public,
    ).branch_version_id


def test_caller_identity_string_is_not_an_authenticated_credential(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    """A string a caller hands over is not a credential.

    This used to read `current_identity().user_id == "anonymous"` -- the same
    claim, made by asserting that an unauthenticated caller got a PRINCIPAL
    named "anonymous". There is no such principal now: an unresolvable token
    binds nobody, and asking who is here refuses.
    """
    import pytest

    from tinyassets.auth.middleware import (
        auth_middleware,
        clear_identity,
        current_identity,
        current_identity_or_none,
    )

    clear_identity()
    assert auth_middleware("alice") is None, "a bare string resolved to somebody"
    assert current_identity_or_none() is None

    with pytest.raises(PermissionError, match="Authentication required"):
        current_identity()


def test_anonymous_environment_actor_cannot_create_or_reach_ledger(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    authenticate(None)

    response = _call(
        "create_branch",
        name="environment-forge",
        author="caller-forge",
    )

    assert response == {
        "error": "Authenticated branch subject required.",
    }
    assert not (base / "ledger.json").exists()


def test_create_and_build_bind_author_receipt_nodes_and_ledger_to_subject(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    authenticate("credential-alice")

    created = _call(
        "create_branch",
        name="server-bound-create",
        author="caller-mallory",
    )
    built = _call(
        "build_branch",
        spec_json=json.dumps({
            "name": "server-bound-build",
            "author": "caller-mallory",
            "entry_point": "draft",
            "node_defs": [{
                "node_id": "draft",
                "display_name": "Draft",
                "prompt_template": "Draft.",
                "author": "caller-mallory",
            }],
            "edges": [
                {"from": "START", "to": "draft"},
                {"from": "draft", "to": "END"},
            ],
        }),
    )

    from tinyassets.daemon_server import get_branch_definition

    created_row = get_branch_definition(
        base,
        branch_def_id=created["branch_def_id"],
    )
    built_row = get_branch_definition(
        base,
        branch_def_id=built["branch_def_id"],
    )
    assert created_row["author"] == "credential-alice"
    assert built_row["author"] == "credential-alice"
    assert built_row["node_defs"][0]["author"] == "credential-alice"
    assert built["batch_receipt"]["actor"] == "credential-alice"
    # Private creates are not public attribution, so they stay out of the ledger.
    assert not (base / "ledger.json").exists()
    encoded = json.dumps(
        {"created": created_row, "built": built_row, "receipt": built["batch_receipt"]},
        sort_keys=True,
    )
    assert "environment-owner" not in encoded
    assert "caller-mallory" not in encoded


def test_listing_uses_only_authenticated_viewer_and_mine_is_empty_for_anonymous(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    _seed_branch(
        base,
        branch_def_id="public-row",
        author="bob",
    )
    _seed_branch(
        base,
        branch_def_id="alice-private",
        author="alice",
        visibility="private",
    )
    _seed_branch(
        base,
        branch_def_id="bob-private",
        author="bob",
        visibility="private",
    )
    _seed_branch(
        base,
        branch_def_id="environment-private",
        author="environment-owner",
        visibility="private",
    )

    authenticate(None)
    anonymous_all = _call("list_branches", scope="all")
    anonymous_mine = _call("list_branches", scope="mine")

    assert {row["branch_def_id"] for row in anonymous_all["branches"]} == {
        "public-row",
    }
    assert anonymous_all["count"] == 1
    assert anonymous_mine == {"branches": [], "count": 0}

    authenticate("alice")
    alice_rows = _call("list_branches", scope="all")
    assert {row["branch_def_id"] for row in alice_rows["branches"]} == {
        "public-row",
        "alice-private",
    }
    assert alice_rows["count"] == 2


def test_reusable_node_search_counts_only_public_and_own_private_candidates(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    _seed_branch(
        base,
        branch_def_id="public-source",
        author="bob",
        node_ids=("shared_probe", "public_only"),
    )
    _seed_branch(
        base,
        branch_def_id="own-source",
        author="alice",
        visibility="private",
        node_ids=("shared_probe", "own_only"),
    )
    _seed_branch(
        base,
        branch_def_id="foreign-source",
        author="bob",
        visibility="private",
        node_ids=("shared_probe", "foreign_only"),
    )
    _seed_branch(
        base,
        branch_def_id="environment-source",
        author="environment-owner",
        visibility="private",
        node_ids=("shared_probe", "environment_only"),
    )

    authenticate("alice")
    all_results = _call("search_nodes")
    shared = _call("search_nodes", query="shared_probe")
    ids = {entry["node_id"] for entry in all_results["entries"]}

    assert ids == {"shared_probe", "public_only", "own_only"}
    assert all_results["count"] == 3
    assert shared["count"] == 1
    assert shared["entries"][0]["reuse_count"] == 2
    assert set(shared["entries"][0]["goal_ids"]) == set()


@pytest.mark.parametrize(
    "action",
    ["get_branch", "describe_branch", "validate_branch", "fork_tree"],
)
def test_private_id_and_missing_selector_have_byte_identical_read_envelopes(
    action: str,
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    _seed_branch(
        base,
        branch_def_id="private-selector",
        author="bob",
        visibility="private",
        node_ids=("secret_node",),
    )
    authenticate("alice")

    denied = _call_raw(action, branch_def_id="private-selector")

    from tinyassets.daemon_server import delete_branch_definition

    assert delete_branch_definition(
        base,
        branch_def_id="private-selector",
    )
    missing = _call_raw(action, branch_def_id="private-selector")

    expected = json.dumps({
        "error": "Branch 'private-selector' not found.",
    })
    assert denied == missing == expected


def test_environment_identity_cannot_resolve_private_name_to_canonical_id(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    _seed_branch(
        base,
        branch_def_id="opaque-private-id",
        name="Guessed Private Name",
        author="environment-owner",
        visibility="private",
    )
    authenticate(None)

    response = _call(
        "get_branch",
        branch_def_id="Guessed Private Name",
    )

    assert response == {
        "error": "Branch 'Guessed Private Name' not found.",
    }
    assert "opaque-private-id" not in json.dumps(response)


def test_public_and_authenticated_owner_reads_remain_available(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    _seed_branch(
        base,
        branch_def_id="public-readable",
        author="bob",
        node_ids=("public_node",),
    )
    _seed_branch(
        base,
        branch_def_id="owner-private",
        author="alice",
        visibility="private",
        node_ids=("owner_node",),
    )

    authenticate(None)
    assert _call(
        "get_branch",
        branch_def_id="public-readable",
    )["branch_def_id"] == "public-readable"

    authenticate("alice")
    assert _call(
        "get_branch",
        branch_def_id="owner-private",
    )["branch_def_id"] == "owner-private"


def test_unreadable_fork_pointer_and_ancestor_are_omitted(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    private_parent = _seed_branch(
        base,
        branch_def_id="private-parent",
        author="bob",
        visibility="private",
        node_ids=("parent_node",),
    )
    parent_version = _publish(base, private_parent, publisher="bob")
    _seed_branch(
        base,
        branch_def_id="public-child",
        author="alice",
        fork_from=parent_version,
        parent_def_id="private-parent",
        node_ids=("child_node",),
    )
    authenticate("alice")

    got = _call("get_branch", branch_def_id="public-child")
    described = _call("describe_branch", branch_def_id="public-child")
    lineage = _call("fork_tree", branch_def_id="public-child")

    assert "fork_from" not in got
    assert "parent_def_id" not in got
    assert "fork_from" not in described
    assert "parent_def_id" not in described
    assert lineage["fork_from"] is None
    assert lineage["ancestors"] == []
    assert "private-parent" not in json.dumps(lineage)

    authenticate("bob")
    parent_owner_view = _call("get_branch", branch_def_id="public-child")
    assert parent_owner_view["fork_from"] == parent_version
    assert parent_owner_view["parent_def_id"] == "private-parent"


def test_descendant_projection_includes_public_and_owner_private_only(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    parent = _seed_branch(
        base,
        branch_def_id="lineage-root",
        author="bob",
        node_ids=("root_node",),
    )
    parent_version = _publish(base, parent, publisher="bob", public=True)
    _seed_branch(
        base,
        branch_def_id="public-descendant",
        author="carol",
        fork_from=parent_version,
    )
    _seed_branch(
        base,
        branch_def_id="owner-private-descendant",
        author="alice",
        visibility="private",
        fork_from=parent_version,
    )
    _seed_branch(
        base,
        branch_def_id="foreign-private-descendant",
        author="carol",
        visibility="private",
        fork_from=parent_version,
    )
    authenticate("alice")

    lineage = _call("fork_tree", branch_def_id="lineage-root")
    descendant_ids = {
        row["branch_def_id"] for row in lineage["descendants"]
    }

    assert descendant_ids == {
        "public-descendant",
        "owner-private-descendant",
    }
    assert lineage["descendant_count"] == 2


def _seed_source_code_branch(
    base: Path,
    *,
    branch_def_id: str,
    author: str,
    visibility: str,
    node_author: str,
) -> dict[str, Any]:
    from hashlib import sha256

    from tinyassets.daemon_server import save_branch_definition

    source_code = "def run(state):\n    return {'secret': state}\n"
    return save_branch_definition(
        base,
        branch_def={
            "branch_def_id": branch_def_id,
            "name": branch_def_id,
            "author": author,
            "domain_id": "workflow",
            "visibility": visibility,
            "entry_point": "source_node",
            "node_defs": [{
                "node_id": "source_node",
                "display_name": "Source Node",
                "description": "private source description",
                "phase": "custom",
                "input_keys": ["state"],
                "output_keys": ["secret"],
                "tools_allowed": ["wiki"],
                "source_code": source_code,
                "prompt_template": "",
                "author": node_author,
                "approved": True,
                "approved_by": "credential-approver",
                "approved_at": "2026-07-25T00:00:00+00:00",
                "approved_source_hash": sha256(
                    source_code.encode("utf-8"),
                ).hexdigest(),
                "approval_reason": "reviewed",
            }],
            "graph_nodes": [{
                "id": "source_node",
                "node_def_id": "source_node",
                "position": 0,
            }],
            "edges": [
                {"from_node": "START", "to_node": "source_node"},
                {"from_node": "source_node", "to_node": "END"},
            ],
            "conditional_edges": [],
            "state_schema": [],
        },
    )


def _stored_branch_bytes(base: Path, branch_def_id: str) -> bytes:
    from tinyassets.daemon_server import get_branch_definition

    return json.dumps(
        get_branch_definition(base, branch_def_id=branch_def_id),
        sort_keys=True,
        default=str,
    ).encode()


def test_foreign_private_and_missing_node_ref_are_identical_and_copy_nothing(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    _seed_branch(
        base,
        branch_def_id="copy-destination",
        author="alice",
        node_ids=("destination_node",),
    )
    _seed_source_code_branch(
        base,
        branch_def_id="private-copy-source",
        author="bob",
        visibility="private",
        node_author="original-node-author",
    )
    authenticate("alice")
    before = _stored_branch_bytes(base, "copy-destination")

    denied = _call_raw(
        "add_node",
        branch_def_id="copy-destination",
        node_id="copied_node",
        display_name="Copied Node",
        node_ref={
            "source": "private-copy-source",
            "node_id": "source_node",
        },
    )

    from tinyassets.daemon_server import delete_branch_definition

    assert _stored_branch_bytes(base, "copy-destination") == before
    assert delete_branch_definition(
        base,
        branch_def_id="private-copy-source",
    )
    missing = _call_raw(
        "add_node",
        branch_def_id="copy-destination",
        node_id="copied_node",
        display_name="Copied Node",
        node_ref={
            "source": "private-copy-source",
            "node_id": "source_node",
        },
    )

    expected = json.dumps({
        "error": "Branch 'private-copy-source' not found.",
    })
    assert denied == missing == expected
    assert _stored_branch_bytes(base, "copy-destination") == before
    encoded = denied + missing + _stored_branch_bytes(
        base,
        "copy-destination",
    ).decode()
    assert "private source description" not in encoded
    assert "credential-approver" not in encoded
    assert "tools_allowed" not in denied + missing
    assert "source_code" not in denied + missing


@pytest.mark.parametrize(
    ("source_visibility", "source_author"),
    [("public", "bob"), ("private", "alice")],
)
def test_authorized_node_ref_preserves_copied_source_provenance(
    source_visibility: str,
    source_author: str,
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    _seed_branch(
        base,
        branch_def_id="authorized-copy-destination",
        author="alice",
        node_ids=("destination_node",),
    )
    _seed_source_code_branch(
        base,
        branch_def_id="authorized-copy-source",
        author=source_author,
        visibility=source_visibility,
        node_author="original-node-author",
    )
    authenticate("alice")

    response = _call(
        "add_node",
        branch_def_id="authorized-copy-destination",
        node_id="copied_node",
        display_name="Copied Node",
        author="caller-forge",
        node_ref={
            "source": "authorized-copy-source",
            "node_id": "source_node",
        },
    )

    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(
        base,
        branch_def_id="authorized-copy-destination",
    )
    copied = next(
        node for node in stored["node_defs"]
        if node["node_id"] == response["node_id"]
    )
    assert copied["author"] == "original-node-author"
    assert copied["approved"] is True
    assert copied["approved_by"] == "credential-approver"
    assert copied["approval_reason"] == "reviewed"
    assert copied["source_code"].startswith("def run")
    assert copied["tools_allowed"] == ["wiki"]
    assert "caller-forge" not in json.dumps(copied)


def test_foreign_private_and_missing_clone_version_are_identical_and_atomic(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    private_parent = _seed_source_code_branch(
        base,
        branch_def_id="private-clone-parent",
        author="bob",
        visibility="private",
        node_author="original-node-author",
    )
    private_version = _publish(base, private_parent, publisher="bob")
    authenticate("alice")
    before_rows = _call("list_branches", scope="all")
    spec = json.dumps({
        "name": "denied-clone",
        "fork_from": private_version,
    })

    denied = _call_raw("build_branch", spec_json=spec)

    from tinyassets.daemon_server import delete_branch_definition

    assert delete_branch_definition(
        base,
        branch_def_id="private-clone-parent",
    )
    missing = _call_raw("build_branch", spec_json=spec)

    expected = json.dumps({
        "error": f"Branch version '{private_version}' not found.",
    })
    assert denied == missing == expected
    assert _call("list_branches", scope="all") == before_rows


@pytest.mark.parametrize(
    ("source_visibility", "source_author"),
    [("public", "bob"), ("private", "alice")],
)
def test_authorized_public_and_owner_private_clone_preserves_source_nodes(
    source_visibility: str,
    source_author: str,
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    parent = _seed_source_code_branch(
        base,
        branch_def_id="authorized-clone-parent",
        author=source_author,
        visibility=source_visibility,
        node_author="original-node-author",
    )
    # An explicit publish: a stranger reads only versions their owner published.
    version_id = _publish(base, parent, publisher=source_author, public=True)
    authenticate("alice")

    built = _call(
        "build_branch",
        spec_json=json.dumps({
            "name": "authorized-clone",
            "fork_from": version_id,
        }),
    )

    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(
        base,
        branch_def_id=built["branch_def_id"],
    )
    assert built["status"] == "built"
    assert stored["author"] == "alice"
    assert stored["fork_from"] == version_id
    # Node attribution (authorship) is always preserved.
    assert stored["node_defs"][0]["author"] == "original-node-author"
    if source_author == "alice":
        # Same-author fork: the forker already owns/trusts the approval, so it is
        # retained (its hash is valid).
        assert stored["node_defs"][0]["approved_by"] == "credential-approver"
        assert stored["node_defs"][0]["approved"] is True
    else:
        # Cross-author fork (Codex ADAPT 2026-08-22 #2): a foreign author's
        # executable approval is NOT trusted for the forker's runs — the approval
        # hash is self-computable, so it is stripped and must be re-approved.
        assert stored["node_defs"][0]["approved_by"] == ""
        assert stored["node_defs"][0]["approved"] is False


def test_foreign_private_and_missing_set_fork_from_are_identical_and_atomic(
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    _seed_branch(
        base,
        branch_def_id="lineage-destination",
        author="alice",
        node_ids=("destination_node",),
    )
    private_parent = _seed_branch(
        base,
        branch_def_id="private-lineage-parent",
        author="bob",
        visibility="private",
        node_ids=("parent_node",),
    )
    private_version = _publish(base, private_parent, publisher="bob")
    authenticate("alice")
    before = _stored_branch_bytes(base, "lineage-destination")
    changes = json.dumps([{
        "op": "set_fork_from",
        "branch_version_id": private_version,
    }])

    denied = _call_raw(
        "patch_branch",
        branch_def_id="lineage-destination",
        changes_json=changes,
    )

    from tinyassets.branch_versions import list_branch_versions
    from tinyassets.daemon_server import delete_branch_definition

    assert _stored_branch_bytes(base, "lineage-destination") == before
    assert list_branch_versions(base, "lineage-destination") == []
    assert delete_branch_definition(
        base,
        branch_def_id="private-lineage-parent",
    )
    missing = _call_raw(
        "patch_branch",
        branch_def_id="lineage-destination",
        changes_json=changes,
    )

    expected = json.dumps({
        "error": f"Branch version '{private_version}' not found.",
    })
    assert denied == missing == expected
    assert _stored_branch_bytes(base, "lineage-destination") == before
    assert list_branch_versions(base, "lineage-destination") == []


@pytest.mark.parametrize(
    ("source_visibility", "source_author"),
    [("public", "bob"), ("private", "alice")],
)
def test_authorized_set_fork_from_accepts_public_and_owner_private_parent(
    source_visibility: str,
    source_author: str,
    branch_authority_env: tuple[Path, Callable[[str | None], None]],
) -> None:
    base, authenticate = branch_authority_env
    _seed_branch(
        base,
        branch_def_id="authorized-lineage-destination",
        author="alice",
        node_ids=("destination_node",),
    )
    parent = _seed_branch(
        base,
        branch_def_id="authorized-lineage-parent",
        author=source_author,
        visibility=source_visibility,
        node_ids=("parent_node",),
    )
    # An explicit publish: a stranger reads only versions their owner published.
    version_id = _publish(base, parent, publisher=source_author, public=True)
    authenticate("alice")

    patched = _call(
        "patch_branch",
        branch_def_id="authorized-lineage-destination",
        changes_json=json.dumps([{
            "op": "set_fork_from",
            "branch_version_id": version_id,
        }]),
    )

    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(
        base,
        branch_def_id="authorized-lineage-destination",
    )
    assert patched["status"] == "patched"
    assert stored["fork_from"] == version_id
