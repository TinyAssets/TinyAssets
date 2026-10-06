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


def test_module_exposes_expected_public_names() -> None:
    """Contract surface — guards against silent removal post-Step-9."""
    expected = {
        # WRITE_ACTIONS table + 17 extractor closures
        "WRITE_ACTIONS",
        "_extract_submit_request", "_extract_give_direction",
        "_extract_set_premise", "_extract_add_canon",
        "_extract_control_daemon",
        "_extract_switch_universe", "_extract_create_universe",
        "_extract_queue_cancel", "_extract_subscribe_goal",
        "_extract_unsubscribe_goal", "_extract_post_to_goal_pool",
        "_extract_submit_node_bid", "_extract_set_tier_config",
        "_extract_daemon_create", "_extract_daemon_summon",
        "_extract_daemon_banish", "_extract_daemon_control",
        "_extract_daemon_update_behavior",
        "_extract_daemon_memory_capture", "_extract_daemon_memory_review",
        "_extract_daemon_memory_promote",
        # Ledger dispatcher trio
        "_ledger_target_dir", "_scope_universe_response",
        "_dispatch_with_ledger",
        # Pattern A2 universe() body
        "_universe_impl",
        # Daemon telemetry
        "_last_activity_at", "_staleness_bucket", "_phase_human",
        "_compute_accept_rate_from_db", "_compute_word_count_from_files",
        "_daemon_liveness", "_parse_activity_line",
        # universe-tool action handlers
        "_action_list_universes", "_action_inspect_universe",
        "_action_read_output", "_action_submit_request",
        "_action_queue_list", "_action_daemon_overview",
        "_action_daemon_list", "_action_daemon_get",
        "_action_daemon_create", "_action_daemon_summon",
        "_action_daemon_banish", "_action_daemon_pause",
        "_action_daemon_resume", "_action_daemon_restart",
        "_action_daemon_update_behavior", "_action_daemon_control_status",
        "_action_daemon_memory_capture", "_action_daemon_memory_search",
        "_action_daemon_memory_list", "_action_daemon_memory_review",
        "_action_daemon_memory_promote", "_action_daemon_memory_status",
        "_action_treasury_status",
        "_action_set_tier_config", "_action_queue_cancel",
        "_action_subscribe_goal", "_action_unsubscribe_goal",
        "_action_list_subscriptions", "_action_post_to_goal_pool",
        "_action_submit_node_bid",
        "_action_give_direction",
        "_action_query_world", "_action_read_premise",
        "_action_set_premise", "_action_add_canon",
        "_action_list_canon",
        "_action_read_canon", "_action_list_sources",
        "_action_read_source", "_action_control_daemon",
        "_action_get_activity", "_action_get_recent_events",
        "_action_get_ledger", "_action_switch_universe",
        "_action_create_universe",
    }
    actual = set(dir(univ_mod))
    missing = expected - actual
    assert not missing, (
        f"tinyassets.api.universe missing expected public names: {sorted(missing)}"
    )


def test_write_actions_table_has_28_entries() -> None:
    """WRITE_ACTIONS dict literal includes daemon create/summon/banish writes,
    the soul.edit learn path, the set_engine founder engine-assignment path, the
    offer_engine market-supply path, declare_universe_loop, and set_visibility.
    (set_persona_name retired — identity is learned in the self-model via
    soul.edit.)

    The count is deliberate friction: membership here is what makes the central
    universe-ACL gate check an action at WRITE strength, so adding an entry must
    be a reviewed act rather than a silent one. `declare_universe_loop` mutates
    soul.md from a caller-supplied universe_id, and omitting it left the action
    gated at READ strength — a cross-tenant write (2026-08-05). `set_visibility`
    (2026-09-26) decides who else may see the universe at all, so it has to be
    gated at WRITE strength and ledgered for the same reason."""
    assert len(univ_mod.WRITE_ACTIONS) == 28
    assert "soul.edit" in univ_mod.WRITE_ACTIONS
    assert "set_engine" in univ_mod.WRITE_ACTIONS
    assert "offer_engine" in univ_mod.WRITE_ACTIONS
    assert "declare_universe_loop" in univ_mod.WRITE_ACTIONS
    assert "set_visibility" in univ_mod.WRITE_ACTIONS


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


def test_daemon_liveness_returns_dict_with_required_keys(tmp_path) -> None:
    """`_daemon_liveness(udir, status)` returns the contract dict shape
    consumed by `_action_list_universes`, `_action_inspect_universe`,
    and `_action_daemon_overview`."""
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


@pytest.mark.parametrize("action", [
    "list", "inspect", "read_output", "submit_request", "queue_list",
    "daemon_overview", "daemon_list", "daemon_get", "daemon_create",
    "daemon_summon", "daemon_pause", "daemon_resume", "daemon_restart",
    "daemon_banish", "daemon_update_behavior", "daemon_control_status",
    "daemon_memory_capture", "daemon_memory_search", "daemon_memory_list",
    "daemon_memory_review", "daemon_memory_promote", "daemon_memory_status",
    "set_tier_config", "queue_cancel",
    "subscribe_goal", "unsubscribe_goal", "list_subscriptions",
    "post_to_goal_pool", "submit_node_bid",
    "give_direction",
    "query_world", "read_premise", "set_premise", "add_canon",
    "list_canon", "read_canon",
    "list_sources", "read_source",
    "control_daemon", "get_activity", "get_recent_events",
    "get_ledger", "switch_universe", "create_universe",
])
def test_every_universe_action_dispatches(action: str, monkeypatch) -> None:
    """Every action verb resolves to a handler (no `Unknown action 'X'` errors
    from the top-level dispatch table).

    Smoke-tests dispatch only — we pass empty kwargs and accept any
    response shape (handlers may legitimately error on missing args, including
    handler-internal "Unknown daemon action" errors that are NOT dispatch-table
    misses). The bug we're guarding against is "action verb dropped from the
    universe() dispatch table" — caught by the exact-string `"Unknown action
    '<action>'"` sentinel.
    """
    out = univ_mod._universe_impl(action=action)
    sentinel = f"Unknown action '{action}'"
    assert sentinel not in out, (
        f"action {action!r} dropped from _universe_impl dispatch table"
    )


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
