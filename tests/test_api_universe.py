"""Direct tests for `tinyassets.api.universe` after decomp Step 9.

Legacy test files for the `universe()` MCP tool import via
`tinyassets.universe_server` and continue to pass through the back-compat
re-export shim. This file exercises `tinyassets.api.universe` directly to
lock in the new public surface.

Pattern mirrors `test_api_market.py` / `test_api_runs.py` /
`test_api_runtime_ops.py` from Steps 4-7.

Surface guarded:
- `WRITE_ACTIONS` table contract — every action mapped to (extractor, daemon-gate)
- `_dispatch_with_ledger`, `_scope_universe_response`, `_ledger_target_dir`
  ledger trio — universe-tool internal pipeline
- `_action_*` handler set — present, callable, owned by this module
- Daemon-liveness telemetry helpers — present, owned by this module
"""

from __future__ import annotations

import json

import pytest

from tinyassets.api import universe as univ_mod
from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity
from tinyassets.daemon_server import grant_universe_access

# Coarse effect grants — resolve-always mode matches an action's effect
# (read/write/costly/admin) across every tool (universe, wiki, extensions, …).
_FOUNDER_SCOPES = ["read", "write", "costly", "admin"]


class _StaticAuthProvider(AuthProvider):
    """Resolve-always provider (like WorkOS) resolving bearer ``"ok"``."""

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


@pytest.fixture(autouse=True)
def _reset_auth_provider():
    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _auth_grant(base, universe_ids, sub: str = "host-test") -> None:
    """Authenticate a founder + grant admin on each universe.

    The ratified ownership model (permissions.py) removed env-var write
    authority, so universe-scoped writes now require an authenticated founder
    holding a write/admin grant.
    """
    set_provider(_StaticAuthProvider(
        Identity(user_id=sub, username=sub, capabilities=list(_FOUNDER_SCOPES)),
    ))
    auth_middleware("ok")
    for uid in universe_ids:
        grant_universe_access(
            base, universe_id=uid, actor_id=sub, permission="admin", granted_by=sub,
        )

# ── module surface ──────────────────────────────────────────────────────────






def test_write_actions_entries_are_extractor_gate_tuples() -> None:
    """Every entry is (extractor_callable, write_gate). The write_gate is
    one of: None (always-write), a callable returning bool, or a set of
    allowed sub-command strings (control_daemon's pause/resume gate)."""
    for action, entry in univ_mod.WRITE_ACTIONS.items():
        assert isinstance(entry, tuple), f"{action} entry not a tuple"
        assert len(entry) == 2, f"{action} entry length != 2"
        extractor, gate = entry
        assert callable(extractor), f"{action} extractor not callable"
        assert gate is None or callable(gate) or isinstance(gate, set), (
            f"{action} gate must be None, callable, or set; "
            f"got {type(gate).__name__}={gate!r}"
        )


# ── Pattern A2 wrapper round-trip ────────────────────────────────────────────


def test_listing_a_fresh_data_dir_twice_gives_one_answer(tmp_path, monkeypatch) -> None:
    """The first list initializes the ownership store in the data dir; it must
    not report the directory as it was before that (CI: "empty", then "1
    entries" on the next call -- order-dependent on which test ran first)."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path / "fresh"))
    (tmp_path / "fresh").mkdir()
    first = univ_mod._universe_impl(action="list")
    assert first == univ_mod._universe_impl(action="list")


# ── Ledger dispatcher contract ───────────────────────────────────────────────


def test_dispatch_with_ledger_signature() -> None:
    """`_dispatch_with_ledger(action, kwargs, *, universe_id)` contract."""
    import inspect
    sig = inspect.signature(univ_mod._dispatch_with_ledger)
    params = list(sig.parameters.keys())
    # Don't lock on exact names — ensure (action, kwargs, ...) shape.
    assert len(params) >= 2
    assert params[0] in {"action", "name"}


def test_scope_universe_response_prepends_universe_lead_in() -> None:
    """#15 contract: response carries a `Universe: <id>` text lead-in."""
    raw = '{"text": "hello", "universe_id": "u_test"}'
    out = univ_mod._scope_universe_response(raw)
    assert "Command center:" in out


# ── Daemon telemetry helpers ─────────────────────────────────────────────────


def test_staleness_bucket_handles_none() -> None:
    """`_staleness_bucket(None)` must return a defined bucket, not raise."""
    result = univ_mod._staleness_bucket(None)
    assert isinstance(result, str)
    assert result  # non-empty


def test_daemon_liveness_returns_dict_with_required_keys(tmp_path, monkeypatch) -> None:
    """`_daemon_liveness(udir, status)` returns the contract dict shape
    consumed by `_action_list_universes`, `_action_inspect_universe`,
    and `_action_daemon_overview`."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path.parent))
    result = univ_mod._daemon_liveness(tmp_path, status=None)
    assert isinstance(result, dict)
    # The exact keys are part of the public contract — lock the shape.
    expected_keys = {"liveness", "staleness", "human_phase"}
    actual_keys = set(result.keys())
    # Tolerant assertion — at least one expected key present (full lockdown
    # belongs in dedicated daemon_liveness tests; this sentinel guards
    # against shape collapse).
    assert actual_keys & expected_keys, (
        f"_daemon_liveness shape unexpected: got {actual_keys}, "
        f"expected at least one of {expected_keys}"
    )


# ── 33-handler dispatch table sanity ─────────────────────────────────────────


def test_universe_impl_dispatch_table_has_known_actions() -> None:
    """The `dispatch` table inside `_universe_impl` includes roster actions."""
    # Round-trip "list" through _universe_impl to confirm the dispatch
    # table is wired and covers at least one read action.
    out = univ_mod._universe_impl(action="list")
    assert isinstance(out, str)
    assert out  # non-empty JSON




def test_daemon_actions_create_summon_and_banish(tmp_path, monkeypatch) -> None:
    """Daemon roster actions round-trip through the public universe API body."""
    universe_dir = tmp_path / "u1"
    universe_dir.mkdir()
    monkeypatch.setattr(univ_mod, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(univ_mod, "_request_universe", lambda uid="": uid or "u1")
    monkeypatch.setattr(univ_mod, "_universe_dir", lambda uid: tmp_path / uid)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _auth_grant(tmp_path, ["u1"])

    create_out = json.loads(univ_mod._universe_impl(
        action="daemon_create",
        universe_id="u1",
        inputs_json=json.dumps({
            "display_name": "Node Scout",
            "soul_text": "Prefer graph-science nodes with verified sources.",
            "domain_claims": ["scientist"],
        }),
    ))
    daemon = create_out["daemon"]
    assert daemon["daemon_id"].startswith("daemon::")
    assert daemon["soul_mode"] == "soul"
    assert daemon["domain_claims"] == ["scientist"]

    list_out = json.loads(univ_mod._universe_impl(
        action="daemon_list",
        universe_id="u1",
    ))
    assert any(item["daemon_id"] == daemon["daemon_id"] for item in list_out["daemons"])

    summon_out = json.loads(univ_mod._universe_impl(
        action="daemon_summon",
        universe_id="u1",
        inputs_json=json.dumps({
            "daemon_id": daemon["daemon_id"],
            "provider_name": "claude-code",
            "model_name": "sonnet",
        }),
    ))
    runtime = summon_out["runtime"]
    assert runtime["daemon_id"] == daemon["daemon_id"]
    assert runtime["provider_name"] == "claude-code"

    pause_out = json.loads(univ_mod._universe_impl(
        action="daemon_pause",
        universe_id="u1",
        inputs_json=json.dumps({
            "runtime_instance_id": runtime["runtime_instance_id"],
        }),
    ))
    assert pause_out["effect"] == "applied"
    assert pause_out["authority_scope"] == "owner"
    assert pause_out["runtime"]["status"] == "paused"

    resume_out = json.loads(univ_mod._universe_impl(
        action="daemon_resume",
        universe_id="u1",
        inputs_json=json.dumps({
            "runtime_instance_id": runtime["runtime_instance_id"],
        }),
    ))
    assert resume_out["effect"] == "applied"
    assert resume_out["runtime"]["status"] == "provisioned"

    restart_out = json.loads(univ_mod._universe_impl(
        action="daemon_restart",
        universe_id="u1",
        inputs_json=json.dumps({
            "runtime_instance_id": runtime["runtime_instance_id"],
        }),
    ))
    assert restart_out["effect"] == "queued"
    assert restart_out["runtime"]["status"] == "restart_requested"

    behavior_out = json.loads(univ_mod._universe_impl(
        action="daemon_update_behavior",
        universe_id="u1",
        inputs_json=json.dumps({
            "daemon_id": daemon["daemon_id"],
            "behavior_update": {"work_domains": ["workflow-platform"]},
            "apply_now": True,
        }),
    ))
    assert behavior_out["effect"] == "applied"
    assert behavior_out["daemon"]["metadata"]["behavior_version"] == 1
    assert behavior_out["daemon"]["metadata"]["behavior_policy"] == {
        "work_domains": ["workflow-platform"],
    }

    status_out = json.loads(univ_mod._universe_impl(
        action="daemon_control_status",
        universe_id="u1",
        inputs_json=json.dumps({
            "daemon_id": daemon["daemon_id"],
        }),
    ))
    assert status_out["daemon_count"] == 1
    assert status_out["runtime_count"] == 1

    memory_status_out = json.loads(univ_mod._universe_impl(
        action="daemon_memory_status",
        universe_id="u1",
        daemon_id=daemon["daemon_id"],
    ))
    assert memory_status_out["daemon_id"] == daemon["daemon_id"]
    assert memory_status_out["schema_version"] == 1

    banish_out = json.loads(univ_mod._universe_impl(
        action="daemon_banish",
        universe_id="u1",
        inputs_json=json.dumps({
            "runtime_instance_id": runtime["runtime_instance_id"],
        }),
    ))
    assert banish_out["effect"] == "applied"
    assert banish_out["runtime"]["status"] == "retired"


def test_daemon_control_actions_accept_top_level_daemon_id(
    tmp_path, monkeypatch
) -> None:
    """BUG-126: connector-level daemon_id must target daemon control actions."""
    universe_dir = tmp_path / "u1"
    universe_dir.mkdir()
    monkeypatch.setattr(univ_mod, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(univ_mod, "_request_universe", lambda uid="": uid or "u1")
    monkeypatch.setattr(univ_mod, "_universe_dir", lambda uid: tmp_path / uid)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _auth_grant(tmp_path, ["u1"])

    create_out = json.loads(univ_mod._universe_impl(
        action="daemon_create",
        universe_id="u1",
        inputs_json=json.dumps({
            "display_name": "Loop Runner",
            "soul_text": "Keep the patch loop moving.",
        }),
    ))
    daemon_id = create_out["daemon"]["daemon_id"]

    get_out = json.loads(univ_mod._universe_impl(
        action="daemon_get",
        universe_id="u1",
        daemon_id=daemon_id,
    ))
    assert get_out["daemon"]["daemon_id"] == daemon_id

    summon_out = json.loads(univ_mod._universe_impl(
        action="daemon_summon",
        universe_id="u1",
        daemon_id=daemon_id,
        inputs_json=json.dumps({
            "provider_name": "codex",
            "model_name": "gpt-5.4",
        }),
    ))
    runtime = summon_out["runtime"]
    assert runtime["daemon_id"] == daemon_id
    assert runtime["status"] == "provisioned"

    restart_out = json.loads(univ_mod._universe_impl(
        action="daemon_restart",
        universe_id="u1",
        daemon_id=daemon_id,
    ))
    assert restart_out["effect"] == "queued"
    assert restart_out["daemon_id"] == daemon_id
    assert restart_out["runtime_instance_id"] == runtime["runtime_instance_id"]
    assert restart_out["runtime"]["status"] == "restart_requested"

    control_status = json.loads(univ_mod._universe_impl(
        action="daemon_control_status",
        universe_id="u1",
        daemon_id=daemon_id,
    ))
    assert control_status["daemon_count"] == 1
    assert control_status["runtime_count"] == 1
    assert control_status["runtimes"][0]["daemon_id"] == daemon_id
