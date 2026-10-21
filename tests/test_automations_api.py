"""The owner's automation surface: who may see it, who may change it, what it says.

Every assertion here is mutation-checked -- the guarding line in
``tinyassets/api/automations.py`` (or ``universe_server.py``) is flipped, the
test is confirmed red, the line is restored. The mutation table lives in the
commit message for this slice.

The one substituted seam is IDENTITY: ``permissions.current_request_actor_id``,
which in production reads the authenticated request subject out of the auth
middleware. Everything below it -- ``is_authenticated_request``,
``universe_access_allows``, ``universe_access_permission``, the ACL rows, the
store -- runs for real against a seeded data root. Stubbing
``universe_access_allows`` (as the fleet-era tests did) would have made every
authorization assertion here a test of the stub.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tinyassets.api.automations as api
from tests.test_automations import (
    BRANCH,
    OWNER,
    UNIVERSE,
    _seed_branch,
    _seed_owner,
)
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment

ADMIN = "acct_admin"
COLLABORATOR = "acct_writer"
STRANGER = "acct_mallory"

CREATE_PAYLOAD = json.dumps(
    {
        "name": "Nightly digest",
        "branch_def_id": BRANCH,
        "interval_seconds": 3600,
        "inputs": {"topic": "spec drift"},
    }
)


class _Identity:
    """The single substituted seam: who the authenticated request is."""

    def __init__(self, actor: str) -> None:
        self.actor = actor

    def __call__(self) -> str:
        return self.actor


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> _Identity:
    """A seeded universe whose owner can register, signed in as that owner."""
    from tinyassets.api import permissions

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)
    identity = _Identity(OWNER)
    monkeypatch.setattr(permissions, "current_request_actor_id", identity)
    return identity


def _grant(tmp_path: Path, actor: str, permission: str) -> None:
    from tinyassets.daemon_server import grant_universe_access

    grant_universe_access(
        tmp_path,
        universe_id=UNIVERSE,
        actor_id=actor,
        permission=permission,
        granted_by=OWNER,
    )


def _create(**overrides) -> dict:
    payload = json.loads(CREATE_PAYLOAD)
    payload.update(overrides)
    return api.automations(
        action="create",
        universe_id=UNIVERSE,
        payload=json.dumps(payload),
    )


# -- 1. Create, then see it ---------------------------------------------------






# -- 2. Authentication and universe access ------------------------------------














# -- 3. Payload and action validation -----------------------------------------


















# -- 4. Controls: revision fence and ownership --------------------------------












# -- 5. Delete -----------------------------------------------------------------






# -- 6. Why a run was skipped --------------------------------------------------








# -- 7. Fleet-era rows stay visible -------------------------------------------




# -- 8. The pinned handles reach the new surface ------------------------------




