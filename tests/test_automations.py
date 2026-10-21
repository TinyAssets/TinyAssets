"""User-owned automations: the fence, the fail-loud registration, and the pump.

Every assertion here is mutation-checked -- the guarding line is flipped, the
test goes red, the line is restored. The mutation table lives in the commit
message for this slice.
"""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
from tinyassets.automations import (
    Automation,
    register_automation,
)
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.control_plane.tick import AssignedQueueConsumer
from tinyassets.storage import db_path

OWNER = "acct_alice"
UNIVERSE = "universe_alice"
BRANCH = "branch_automation_demo"

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)


pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path: Path, monkeypatch):
    """Every test's data root is its own tmp_path.

    `_engine_run_admit` resolves its rolling-cap ledger from
    `TINYASSETS_DATA_DIR` and falls back to `"."` -- the REPO ROOT -- when it is
    unset (engine_mcp_server.py:104). Without this, running these tests wrote
    `.engine_run_admissions.db` into the working tree AND shared one 20/hour cap
    across every test in the file, so later tests were rate-limited by earlier
    ones. Both were observed; this pins the root the way the daemon does.
    """
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


# -- Seeds --------------------------------------------------------------------


def _seed_branch(
    tmp_path: Path,
    *,
    branch_def_id: str = BRANCH,
    author: str = OWNER,
    visibility: str = "public",
) -> str:
    from tinyassets.daemon_server import initialize_author_server, save_branch_definition

    node = NodeDefinition(
        node_id="n1",
        display_name="Automation writer",
        prompt_template="Do the owner's recurring work.",
    )
    branch = BranchDefinition(
        branch_def_id=branch_def_id,
        name="Automation demo",
        author=author,
        visibility=visibility,
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
        entry_point="n1",
        node_defs=[node],
        state_schema=[],
    )
    initialize_author_server(tmp_path)
    save_branch_definition(tmp_path, branch_def=branch.to_dict())
    return branch_def_id


def _seed_nested_branch(
    tmp_path: Path,
    *,
    parent_id: str = "branch_parent_invoke",
    child_id: str = "branch_child_prompt",
    author: str = OWNER,
    wait_mode: str = "blocking",
) -> str:
    """A root whose only node invokes a CHILD branch that holds the prompt.

    The root itself never calls a provider, so a guard wired only to the root's
    nodes never runs where the spend happens.
    """
    from tinyassets.daemon_server import initialize_author_server, save_branch_definition

    initialize_author_server(tmp_path)
    child = BranchDefinition(
        branch_def_id=child_id,
        name="Nested child",
        author=author,
        visibility="public",
        graph_nodes=[GraphNodeRef(id="c1", node_def_id="c1")],
        edges=[EdgeDefinition(from_node="c1", to_node="END")],
        entry_point="c1",
        node_defs=[
            NodeDefinition(
                node_id="c1",
                display_name="Child writer",
                prompt_template="Do the nested work.",
                output_keys=["child_out"],
            )
        ],
        state_schema=[{"name": "child_out", "type": "str"}],
    )
    save_branch_definition(tmp_path, branch_def=child.to_dict())

    parent = BranchDefinition(
        branch_def_id=parent_id,
        name="Parent invoker",
        author=author,
        visibility="public",
        graph_nodes=[GraphNodeRef(id="p1", node_def_id="p1")],
        edges=[EdgeDefinition(from_node="p1", to_node="END")],
        entry_point="p1",
        node_defs=[
            NodeDefinition(
                node_id="p1",
                display_name="Invoke the child",
                invoke_branch_spec={
                    "branch_def_id": child_id,
                    "wait_mode": wait_mode,
                    "inputs_mapping": {},
                    "output_mapping": {"parent_out": "child_out"},
                },
            )
        ],
        state_schema=[{"name": "parent_out", "type": "str"}],
    )
    save_branch_definition(tmp_path, branch_def=parent.to_dict())
    return parent_id


def _seed_version_invoking_branch(
    tmp_path: Path,
    *,
    parent_id: str = "branch_parent_version",
    child_id: str = "branch_child_version",
    author: str = OWNER,
    public: bool = True,
    child_visibility: str = "public",
) -> str:
    """A root that invokes a PUBLISHED VERSION of a child, async.

    The version-pinned async launch is a fourth child path, and it was the one
    that still dropped the guard after round 2 (Codex round 3 §a). ``public``
    is the author's publication mark; ``False`` pins an unpublished snapshot.
    """
    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.daemon_server import initialize_author_server, save_branch_definition

    initialize_author_server(tmp_path)
    child = BranchDefinition(
        branch_def_id=child_id,
        name="Versioned child",
        author=author,
        visibility=child_visibility,
        graph_nodes=[GraphNodeRef(id="v1", node_def_id="v1")],
        edges=[EdgeDefinition(from_node="v1", to_node="END")],
        entry_point="v1",
        node_defs=[
            NodeDefinition(
                node_id="v1",
                display_name="Versioned writer",
                prompt_template="Do the versioned work.",
                output_keys=["child_out"],
            )
        ],
        state_schema=[{"name": "child_out", "type": "str"}],
    )
    save_branch_definition(tmp_path, branch_def=child.to_dict())
    version = publish_branch_version(tmp_path, child.to_dict(), publisher=author,
                                     public=public)

    parent = BranchDefinition(
        branch_def_id=parent_id,
        name="Version invoker",
        author=author,
        visibility="public",
        graph_nodes=[GraphNodeRef(id="p1", node_def_id="p1")],
        edges=[EdgeDefinition(from_node="p1", to_node="END")],
        entry_point="p1",
        node_defs=[
            NodeDefinition(
                node_id="p1",
                display_name="Invoke the published version",
                invoke_branch_version_spec={
                    "branch_version_id": version.branch_version_id,
                    "wait_mode": "async",
                    "inputs_mapping": {},
                    "output_mapping": {"parent_out": "ignored"},
                },
            )
        ],
        state_schema=[{"name": "parent_out", "type": "str"}],
    )
    save_branch_definition(tmp_path, branch_def=parent.to_dict())
    return parent_id


def _seed_owner(
    tmp_path: Path,
    *,
    universe_id: str = UNIVERSE,
    owner: str = OWNER,
    permission: str = "admin",
    home: str | None = None,
) -> None:
    from tinyassets.daemon_server import (
        grant_universe_access,
        initialize_author_server,
        set_founder_home,
    )

    initialize_author_server(tmp_path)
    (tmp_path / universe_id).mkdir(parents=True, exist_ok=True)
    grant_universe_access(
        tmp_path,
        universe_id=universe_id,
        actor_id=owner,
        permission=permission,
        granted_by=owner,
    )
    set_founder_home(
        tmp_path,
        founder_sub=owner,
        universe_id=universe_id if home is None else home,
    )


def _copy_assignment_to(tmp_path: Path, *, universe_id: str, owner: str) -> None:
    """Give another owner an independently granted HTTP model."""
    from tests.support.http_model import seed_http_assignment
    seed_http_assignment(tmp_path, universe_id=universe_id, owner_user_id=owner)


def _switch_assignment_provider(tmp_path: Path, *, universe_id: str, provider: str) -> None:
    """Rebind the universe to another provider, the way an owner would."""
    from tinyassets.provider_assignment import (
        load_provider_assignment,
        provider_assignment_digest,
        store_provider_assignment_in_transaction,
    )

    current = load_provider_assignment(tmp_path, universe_id=universe_id)
    assert current is not None
    switched = replace(current, provider=provider, generation=current.generation + 1)
    switched = replace(
        switched,
        assignment_digest=provider_assignment_digest(
            owner_user_id=switched.owner_user_id,
            universe_id=switched.universe_id,
            provider=switched.provider,
            generation=switched.generation,
            binding_id=switched.binding_id,
            credential_reference_id=switched.credential_reference_id,
            credential_reference_generation=switched.credential_reference_generation,
            credential_reference_digest=switched.credential_reference_digest,
        ),
    )
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        store_provider_assignment_in_transaction(conn, switched)
        conn.commit()


@pytest.fixture
def registered(tmp_path: Path, monkeypatch) -> Automation:
    """A universe whose owner can register, plus one active automation."""
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path)
    return register_automation(
        tmp_path,
        universe_id=UNIVERSE,
        owner_principal_id=OWNER,
        name="Nightly digest",
        branch_def_id=BRANCH,
        interval_seconds=600,
        inputs={"topic": "spec drift"},
        now=NOW,
    )


class _FakeOutcome:
    def __init__(self, run_id: str = "run_fake", status: str = "completed") -> None:
        self.run_id = run_id
        self.status = status
        self.output: dict[str, object] = {}
        self.error = ""

    def with_error(self, error: str) -> "_FakeOutcome":
        self.error = error
        return self


class _SeamRecorder:
    """Stands in for `_execute`, the one substitutable seam."""

    def __init__(self, status: str = "completed") -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = status

    def __call__(
        self, base_path, automation, provider_call, branch, inputs,
        on_run_started=None,
    ):
        self.calls.append((automation.automation_id, dict(inputs)))
        return _FakeOutcome(
            run_id=f"run_{len(self.calls)}",
            status=self.status,
        )


def _child_run_ids(tmp_path: Path, branch_def_id: str) -> list[str]:
    """Run ids launched for a child branch, newest last."""
    from tinyassets.runs import _connect, initialize_runs_db

    initialize_runs_db(tmp_path)
    with _connect(tmp_path) as conn:
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE branch_def_id = ? ORDER BY rowid",
            (branch_def_id,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def _lease_ttl_seconds(store, universe_id: str) -> float:
    """Seconds until the universe lease expires, from its stored row."""
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT expires_at FROM universe_leases WHERE universe_id = ?",
            (universe_id,),
        ).fetchone()
    assert row is not None, "no universe lease row"
    expires = datetime.fromisoformat(str(row[0]))
    return (expires - datetime.now(timezone.utc)).total_seconds()


def _refusal_rows(tmp_path: Path) -> dict[str, str]:
    database = db_path(tmp_path)
    if not database.is_file():
        return {}
    with sqlite3.connect(database) as conn:
        try:
            rows = conn.execute(
                "SELECT branch_task_id, reason FROM assigned_queue_refusals"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
    return {str(key): str(reason) for key, reason in rows}


# -- 1. The (automation_id, due_at) fence -------------------------------------






# -- 2. Restart survival ------------------------------------------------------






# -- 3. Provider switch, nothing pinned ---------------------------------------






# -- 4. Registration fails loud -----------------------------------------------


def _registration_kwargs(**overrides):
    kwargs = {
        "universe_id": UNIVERSE,
        "owner_principal_id": OWNER,
        "name": "Nightly digest",
        "branch_def_id": BRANCH,
        "interval_seconds": 600,
        "cron_expr": "",
        "inputs": {},
        "now": NOW,
    }
    kwargs.update(overrides)
    return kwargs




















# -- 5. Authority is re-derived at run time -----------------------------------












# -- Owner controls -----------------------------------------------------------




# -- 6. The consumer pump -----------------------------------------------------


class _InlineExecutor:
    """Runs submissions on the calling thread so a poll is assertable."""

    def __init__(self) -> None:
        self.submissions: list[tuple] = []

    def submit(self, fn, *args):
        self.submissions.append(args)
        future: Future = Future()
        try:
            future.set_result(fn(*args))
        except BaseException as exc:  # pragma: no cover - surfaced by the assertion
            future.set_exception(exc)
        return future

    def shutdown(self, **_kwargs) -> None:
        pass


def _consumer_with_inline_executor(tmp_path: Path, *, max_concurrency: int = 2):
    consumer = AssignedQueueConsumer(tmp_path, max_concurrency=max_concurrency)
    inline = _InlineExecutor()
    consumer._executor.shutdown(wait=False)
    consumer._executor = inline
    return consumer, inline










# -- Codex ADAPT 2026-08-29 folds ---------------------------------------------


@contextmanager
def _real_providers(**providers):
    """Install a router of fake providers for a REAL run through the session."""
    from unittest.mock import patch

    import tinyassets.providers.call as provider_call_module
    from tinyassets.providers.router import ProviderRouter
    previous_router = provider_call_module.get_provider_router()
    provider_call_module.set_provider_router(ProviderRouter(dict(providers)))
    try:
        with patch("tinyassets.providers.provider_resolver.provider_for_definition",
                   lambda definition: providers["api_key_http:" + definition.id]):
            yield
    finally:
        provider_call_module.set_provider_router(previous_router)


def _receipt_sources(tmp_path: Path, run_id: str) -> tuple[str, ...]:
    """The connected source snapshot the real admission recorded."""
    from tinyassets.storage.provider_work_authority import (
        SQLiteProviderWorkAuthorityStore,
    )

    store = SQLiteProviderWorkAuthorityStore(tmp_path)
    with store.connection() as conn:
        row = conn.execute(
            "SELECT record_json FROM provider_work_receipts WHERE work_item_id = ?",
            (run_id,),
        ).fetchone()
    assert row is not None, f"no provider work receipt for run {run_id}"
    record = json.loads(row[0])
    return tuple(record["admitted_sources"])


# §5 -- the ACL race






















# §6 -- registration refuses what admission refuses












# §7 -- engine admission and the cron cadence floor










# §8 / §1 -- the shared universe lease, pause, timeout and stop














# §6 -- no silent skips






# §9 -- tests that exercise the real thing






# -- Liveness is not activity -------------------------------------------------






# -- 7. The real path ---------------------------------------------------------




