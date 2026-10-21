"""User-owned automations: the fence, the fail-loud registration, and the pump.

Every assertion here is mutation-checked -- the guarding line is flipped, the
test goes red, the line is restored. The mutation table lives in the commit
message for this slice.
"""

from __future__ import annotations

from concurrent.futures import Future
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






# §5 -- the ACL race






















# §6 -- registration refuses what admission refuses












# §7 -- engine admission and the cron cadence floor










# §8 / §1 -- the shared universe lease, pause, timeout and stop














# §6 -- no silent skips






# §9 -- tests that exercise the real thing






# -- Liveness is not activity -------------------------------------------------






# -- 7. The real path ---------------------------------------------------------




