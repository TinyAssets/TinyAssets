"""Prepare an inert first agent for explicit model consent; never reset content."""

from pathlib import Path


def reconnect_owned_binding(base: Path, *, uid: str, owner: str) -> dict:
    """Explicit reconnect after withdrawal; preserve every agent content field."""
    from tinyassets.custom_agents import reconnect_binding_candidates_in_transaction
    from tinyassets.onboarding.serving import _require_current_admin
    from tinyassets.provider_assignment import load_provider_assignment_in_transaction
    from tinyassets.shared_self import require_founder_home
    from tinyassets.storage.current_home import check_current_home
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    require_founder_home(base, uid, owner)
    _require_current_admin(base, universe_id=uid, owner=owner)
    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        conn.execute("BEGIN")
        check_current_home(conn, owner, uid)
        assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
        if (assignment is None or assignment.owner_user_id != owner
                or assignment.state != "unassigned"):
            raise PermissionError("explicit_disconnection_required")
        bindings = reconnect_binding_candidates_in_transaction(conn, universe_id=uid,
            owner=owner, provider_ref=assignment.binding_id)
        if len(bindings) != 1:
            raise PermissionError("existing_agent_requires_review")
        return bindings[0]


def ensure_bootstrap_binding(base: Path, *, uid: str, owner: str) -> dict:
    """Create once or resume the untouched first binding, without provider access.

    A noncanonical, edited, foreign, serving or ambiguous binding is recovery,
    not permission to replace the user's agent. Model approval is a separate
    existing request whose capture/publication rechecks home and exact revisions.
    """
    from tinyassets.custom_agents import create_binding, list_bindings
    from tinyassets.onboarding.serving import (
        _BINDING_PAYLOAD,
        RETIRED_BINDING_PAYLOAD,
        _gesture_lock,
        _platform_definition,
        _require_current_admin,
        _retired_platform_definition_ids,
    )
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.shared_self import require_founder_home

    with _gesture_lock(uid):
        require_founder_home(base, uid, owner)
        _require_current_admin(base, universe_id=uid, owner=owner)
        if load_provider_assignment(base, universe_id=uid) is not None:
            raise PermissionError("existing_model_setup_requires_recovery")
        # Any second row proves ambiguity; no truncated owner-specific search.
        bindings = list_bindings(base, universe_id=uid, limit=2)
        if len(bindings) > 1:
            raise PermissionError("existing_agent_requires_review")
        definition = _platform_definition(base)
        did = definition["agent_definition_id"]
        if bindings:
            binding = bindings[0]
            # A pre-rename platform binding (retired definition, retired name) is
            # the same untouched default; the storage migration (C4) re-points it.
            untouched = (
                binding["agent_definition_id"] == did
                and binding["configuration"] == _BINDING_PAYLOAD
                or (binding["agent_definition_id"] in _retired_platform_definition_ids(base)
                    and binding["configuration"] == RETIRED_BINDING_PAYLOAD)
            )
            if (binding["created_by"] != owner or binding["updated_by"] != owner
                    or not untouched
                    or binding["revision"] != 1 or binding["status"] != "configured"):
                raise PermissionError("existing_agent_requires_review")
            return binding
        require_founder_home(base, uid, owner)
        _require_current_admin(base, universe_id=uid, owner=owner)
        return create_binding(base, universe_id=uid, definition_id=did,
                              created_by=owner, payload=dict(_BINDING_PAYLOAD))
