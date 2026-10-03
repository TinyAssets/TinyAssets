"""Tests for the #88 interim `get_status` MCP primitive.

Navigator's Devin-session1 intelligence report §T-7 introduced this
tool as the highest-ROI interim primitive unblocking tier-2
confidential-tier trust. Devin bounced at live exchange 4 because his
chatbot couldn't verify the routing promise; `get_status` is the
concrete-evidence surface that closes that System → Chatbot chain break.

Tests pin the response contract so future edits don't drop the fields
the chatbot relies on for privacy-critical narration.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tinyassets.universe_server import (
    get_status,
    mcp,
)


@pytest.fixture(autouse=True)
def _home(founder_home):
    """These tests read "the status of my universe", so the operator has one.

    An authenticated founder with no bound home gets the first-contact card
    instead -- a different, correct surface (no-anonymous-principal)."""
    return founder_home


def _list_tools():
    return asyncio.run(mcp.list_tools(run_middleware=False))


def test_get_status_tool_is_registered() -> None:
    """#88 must expose `get_status` as an MCP tool the chatbot can call."""
    names = {t.name for t in _list_tools()}
    assert "get_status" in names


def test_get_status_tool_is_safe_to_call() -> None:
    """#88: get_status stays safe to call on any turn without consent gates.

    Status is observational evidence. First-contact provisioning belongs to
    the authenticated conversation entry path, so the MCP metadata must be
    genuinely read-only as well as idempotent and non-destructive.
    """
    tool = next(t for t in _list_tools() if t.name == "get_status")
    # FastMCP may surface ToolAnnotations via tool.annotations.
    ann = getattr(tool, "annotations", None)
    if ann is not None:
        assert getattr(ann, "readOnlyHint", None) is True
        assert getattr(ann, "idempotentHint", None) is True
        assert getattr(ann, "destructiveHint", None) is False


def test_first_contact_tool_descriptions_match_the_opening_instruction() -> None:
    tools = {tool.name: tool for tool in _list_tools()}
    status_description = " ".join(
        (tools["get_status"].description or "").lower().split()
    )
    converse_description = " ".join(
        (tools["converse"].description or "").lower().split()
    )

    assert "pure, idempotent read" in status_description
    assert "never creates or repairs" in status_description
    assert "founder's home command center" in converse_description
    assert "creates and binds a blank seed" in converse_description


def test_get_status_returns_required_shape() -> None:
    """Response must carry the 4 load-bearing blocks the chatbot narrates
    when answering privacy-critical questions: active_host,
    tier_routing_policy, evidence, caveats.
    """
    payload = json.loads(get_status())
    # Top-level required keys.
    for key in ("active_host", "tier_routing_policy", "evidence",
                "caveats", "universe_id"):
        assert key in payload, f"missing top-level key: {key}"

    # active_host shape.
    ah = payload["active_host"]
    assert "host_id" in ah
    assert "served_llm_type" in ah
    assert "llm_endpoint_bound" in ah
    assert "api_key_providers_enabled" in ah

    # tier_routing_policy shape.
    trp = payload["tier_routing_policy"]
    assert "served_llm_type" in trp
    assert "accept_external_requests" in trp
    assert "accept_paid_bids" in trp
    assert "tier_status_map" in trp

    # evidence shape — each field load-bearing for chatbot trust claim.
    ev = payload["evidence"]
    assert "last_completed_request_llm_used" in ev
    assert "activity_log_tail" in ev
    assert "policy_hash" in ev

    # caveats must be a non-empty list (legacy-surface honesty).
    assert isinstance(payload["caveats"], list)
    assert len(payload["caveats"]) >= 1


def test_get_status_policy_hash_is_deterministic() -> None:
    """Chatbot uses `policy_hash` to detect config drift across calls.
    Two consecutive calls with no config change must return the same
    hash; otherwise the drift-detection contract breaks.
    """
    h1 = json.loads(get_status())["evidence"]["policy_hash"]
    h2 = json.loads(get_status())["evidence"]["policy_hash"]
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest length


def test_get_status_policy_hash_is_sha256_hex() -> None:
    """Hash must be lowercase-hex sha256 so chatbot can compare as a
    simple string match without parsing variants.
    """
    h = json.loads(get_status())["evidence"]["policy_hash"]
    # sha256 hex is 64 lowercase-alnum chars.
    assert all(c in "0123456789abcdef" for c in h)


def test_get_status_surfaces_honest_caveat_about_legacy_surface() -> None:
    """The legacy universe_server surface does NOT enforce per-universe
    sensitivity_tier. Chatbot must see that caveat so trust claims match
    reality (avoid Devin's 'pitch writing checks product isn't cashing'
    bounce reasoning).
    """
    caveats = json.loads(get_status())["caveats"]
    combined = " ".join(caveats).lower()
    # Explicit "does not enforce" signal.
    assert "does not enforce" in combined or "does not yet" in combined \
        or "not a local-only guarantee" in combined or \
        "not enforce" in combined


def test_get_status_never_errors_on_missing_activity_log() -> None:
    """Fresh universes have no activity.log yet. get_status must still
    return a valid response — chatbot should be able to call this on
    day one of a host install, before any requests have run.
    """
    payload = json.loads(get_status())
    # activity_log_tail can be an empty list but must be present.
    assert "activity_log_tail" in payload["evidence"]
    assert isinstance(payload["evidence"]["activity_log_tail"], list)
    # last_completed_request_llm_used defaults to "unknown" when no log.
    # (Also accept any value — chatbot only depends on field existing.)
    assert payload["evidence"]["last_completed_request_llm_used"] is not None


# ─────────────────────────────────────────────────────────────────────
# Track Q — routing-evidence fold-in (§10.7 self-auditing-tools shape).
# Adds `last_n_calls` + `evidence_caveats` + `actionable_next_steps` to
# the existing get_status response. Mirrors the dispatch_evidence
# caveat-augmentation pattern from commit 7d19f34.
# ─────────────────────────────────────────────────────────────────────


def _write_activity_log(tmp_path, lines):
    """Write `lines` to a universe's activity.log. Returns universe_id."""
    import os
    os.environ["TINYASSETS_DATA_DIR"] = str(tmp_path)
    udir = tmp_path / "track_q_universe"
    udir.mkdir(parents=True, exist_ok=True)
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(tmp_path, "track_q_universe")
    (udir / "activity.log").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    return "track_q_universe"


def test_get_status_evidence_includes_last_n_calls() -> None:
    """Track Q — evidence dict must surface `last_n_calls` as structured
    parsed entries (not just raw strings), so the chatbot can filter by
    tag without reparsing.
    """
    payload = json.loads(get_status())
    ev = payload["evidence"]
    assert "last_n_calls" in ev
    assert isinstance(ev["last_n_calls"], list)


def test_get_status_has_evidence_caveats_dict() -> None:
    """Track Q — per-field caveats keyed by evidence field name so the
    chatbot can cite only the degenerate keys when narrating."""
    payload = json.loads(get_status())
    assert "evidence_caveats" in payload
    assert isinstance(payload["evidence_caveats"], dict)


def test_get_status_has_actionable_next_steps_list() -> None:
    """Track Q — §10.7 canonical shape surfaces optional concrete next
    steps the chatbot can relay to the user."""
    payload = json.loads(get_status())
    assert "actionable_next_steps" in payload
    assert isinstance(payload["actionable_next_steps"], list)


def test_get_status_last_n_calls_parses_tagged_entries(tmp_path) -> None:
    """Track Q — tagged activity.log entries become {ts, tag, message,
    raw} dicts in last_n_calls, newest-first."""
    uid = _write_activity_log(tmp_path, [
        "[2026-04-20 10:00:00] [dispatch_guard] older entry",
        "[2026-04-20 10:05:00] [scene_write] newer entry",
    ])
    payload = json.loads(get_status(command_center_id=uid))
    calls = payload["evidence"]["last_n_calls"]
    assert len(calls) == 2
    # Newest-first ordering.
    assert calls[0]["tag"] == "scene_write"
    assert calls[1]["tag"] == "dispatch_guard"
    # Structured parse, not raw strings.
    assert "ts" in calls[0] and "message" in calls[0]


def test_get_status_evidence_caveats_flag_empty_log(tmp_path) -> None:
    """Track Q — when activity.log is empty, per-field caveats must flag
    BOTH activity_log_tail AND last_n_calls as unreliable."""
    import os
    os.environ["TINYASSETS_DATA_DIR"] = str(tmp_path)
    udir = tmp_path / "empty_universe"
    udir.mkdir(parents=True, exist_ok=True)
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(tmp_path, "empty_universe")
    payload = json.loads(get_status(command_center_id="empty_universe"))
    ec = payload["evidence_caveats"]
    # No log → both evidence keys should carry caveats.
    assert "activity_log_tail" in ec
    assert "last_n_calls" in ec
    assert any("empty" in c.lower() or "missing" in c.lower()
               for c in ec["activity_log_tail"])


def test_get_status_activity_log_line_count_reflects_total(tmp_path) -> None:
    """Track Q — regression: previously reported len(activity_tail) which
    caps at 20. Must now report the true total line count so the
    chatbot's narration of 'N entries logged' is correct."""
    uid = _write_activity_log(tmp_path, [
        f"[2026-04-20 10:{i:02d}:00] [scene_write] entry {i}"
        for i in range(30)
    ])
    payload = json.loads(get_status(command_center_id=uid))
    ev = payload["evidence"]
    # Total should be 30; activity_log_tail is capped at 20.
    assert ev["activity_log_line_count"] == 30
    assert len(ev["activity_log_tail"]) == 20


# ─────────────────────────────────────────────────────────────────────
# Task #56 — llm_endpoint_bound provider-chain expansion.
# Priority: ollama → anthropic → codex → claude → unset.
# ─────────────────────────────────────────────────────────────────────


def _get_endpoint_hint(
    monkeypatch,
    env: dict,
    which_map: dict,
    *,
    api_key_opt_in: bool = False,
    home: Path | None = None,
) -> str:
    """Helper: patch env + shutil.which, call get_status, return endpoint hint."""
    import shutil as _shutil

    for key in (
        "OLLAMA_HOST", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY",
        "XAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
        "TINYASSETS_ALLOW_API_KEY_PROVIDERS", "CODEX_HOME",
        # claude is "bound" only with binary AND subscription auth; clear both
        # auth sources so the default is not-authed and tests opt in explicitly.
        "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, val in env.items():
        monkeypatch.setenv(key, val)
    if api_key_opt_in:
        monkeypatch.setenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", "1")

    def _which(cmd, *args, **kwargs):
        return which_map.get(cmd)

    monkeypatch.setattr("shutil.which", _which)
    monkeypatch.setattr(_shutil, "which", _which)
    monkeypatch.setattr(
        "tinyassets.api.status.Path.home",
        lambda: home or (Path.cwd() / ".workflow-test-empty-home"),
    )

    payload = json.loads(get_status())
    return payload["active_host"]["llm_endpoint_bound"]


def test_llm_endpoint_bound_ollama(monkeypatch) -> None:
    hint = _get_endpoint_hint(
        monkeypatch,
        env={"OLLAMA_HOST": "http://localhost:11434"},
        which_map={},
    )
    assert hint == "ollama"


@pytest.mark.parametrize(
    "env, which_map",
    [
        ({"ANTHROPIC_BASE_URL": "http://relay.internal"}, {}),
        ({"OPENAI_API_KEY": "sk-test"}, {"codex": "/usr/local/bin/codex"}),
        ({"XAI_API_KEY": "xai-test"}, {}),
        ({"GEMINI_API_KEY": "gemini-test"}, {}),
        ({"GROQ_API_KEY": "groq-test"}, {}),
        ({"XAI_API_KEY": "x", "GEMINI_API_KEY": "g", "GROQ_API_KEY": "q"}, {}),
    ],
)
def test_host_api_keys_never_bind_even_with_the_retired_switch(
    monkeypatch, env, which_map,
) -> None:
    """The platform has no LLM (Hard Rule 15). A host API key, with or without
    the retired TINYASSETS_ALLOW_API_KEY_PROVIDERS=1, binds nothing."""
    hint = _get_endpoint_hint(
        monkeypatch, env=env, which_map=which_map, api_key_opt_in=True,
    )
    assert hint == "unset"


def test_llm_endpoint_bound_ollama_takes_priority_over_anthropic(
    monkeypatch,
) -> None:
    hint = _get_endpoint_hint(
        monkeypatch,
        env={
            "OLLAMA_HOST": "http://localhost:11434",
            "ANTHROPIC_BASE_URL": "http://relay.internal",
        },
        which_map={},
    )
    assert hint == "ollama"


def test_llm_endpoint_bound_codex_subscription_auth(monkeypatch, tmp_path) -> None:
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    (auth_dir / "auth.json").write_text("{}", encoding="utf-8")
    hint = _get_endpoint_hint(
        monkeypatch,
        env={},
        which_map={"codex": "/usr/local/bin/codex"},
        home=tmp_path,
    )
    assert hint == "codex"


def test_llm_endpoint_bound_codex_honors_codex_home(monkeypatch, tmp_path) -> None:
    codex_home = tmp_path / "persistent-codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()

    hint = _get_endpoint_hint(
        monkeypatch,
        env={"CODEX_HOME": str(codex_home)},
        which_map={"codex": "/usr/local/bin/codex"},
        home=empty_home,
    )

    assert hint == "codex"


def test_llm_endpoint_bound_openai_key_without_codex_cli_falls_through(
    monkeypatch,
) -> None:
    hint = _get_endpoint_hint(
        monkeypatch,
        env={"OPENAI_API_KEY": "sk-test", "CLAUDE_CODE_OAUTH_TOKEN": "tok"},
        which_map={"claude": "/usr/local/bin/claude"},
    )
    assert hint == "claude"


def test_llm_endpoint_bound_claude_cli(monkeypatch) -> None:
    # claude is bound only with binary AND subscription auth (auth-aware fix).
    hint = _get_endpoint_hint(
        monkeypatch,
        env={"CLAUDE_CODE_OAUTH_TOKEN": "tok"},
        which_map={"claude": "/usr/local/bin/claude"},
    )
    assert hint == "claude"


def test_llm_endpoint_bound_claude_cli_present_but_unauthed_is_unset(
    monkeypatch,
) -> None:
    # Binary present, NO auth → must NOT report claude as bound (the 2026-06-25
    # blind spot the auth-aware fix closes).
    hint = _get_endpoint_hint(
        monkeypatch,
        env={},
        which_map={"claude": "/usr/local/bin/claude"},
    )
    assert hint == "unset"


def test_llm_endpoint_bound_unset_when_nothing_available(monkeypatch) -> None:
    hint = _get_endpoint_hint(
        monkeypatch,
        env={},
        which_map={},
    )
    assert hint == "unset"


def test_llm_endpoint_bound_codex_takes_priority_over_claude(
    monkeypatch, tmp_path,
) -> None:
    auth_dir = tmp_path / ".codex"
    auth_dir.mkdir()
    (auth_dir / "auth.json").write_text("{}", encoding="utf-8")
    hint = _get_endpoint_hint(
        monkeypatch,
        env={},
        which_map={
            "codex": "/usr/local/bin/codex",
            "claude": "/usr/local/bin/claude",
        },
        home=tmp_path,
    )
    assert hint == "codex"


# ─────────────────────────────────────────────────────────────────────
# Task #14 — SDK-key-only providers (xai, gemini, groq).
# Priority: ollama → anthropic → codex → claude → xai → gemini → groq → unset.
# ─────────────────────────────────────────────────────────────────────


def test_api_key_endpoint_hints_ignored_without_opt_in(monkeypatch) -> None:
    hint = _get_endpoint_hint(
        monkeypatch,
        env={
            "OPENAI_API_KEY": "sk-test",
            "XAI_API_KEY": "xai-test",
            "GEMINI_API_KEY": "gemini-test",
            "GROQ_API_KEY": "groq-test",
        },
        which_map={"codex": "/usr/local/bin/codex"},
    )
    assert hint == "unset"


def test_llm_endpoint_bound_claude_beats_xai(monkeypatch) -> None:
    """Claude CLI is a subprocess-bound primary writer; XAI_API_KEY
    only feeds an SDK-keyed tertiary fallback. Claude wins."""
    hint = _get_endpoint_hint(
        monkeypatch,
        env={"XAI_API_KEY": "xai-test", "CLAUDE_CODE_OAUTH_TOKEN": "tok"},
        which_map={"claude": "/usr/local/bin/claude"},
    )
    assert hint == "claude"


def test_llm_endpoint_bound_ollama_beats_all_sdk_keys(monkeypatch) -> None:
    """Ollama is always-local; beats every SDK-key-only binding."""
    hint = _get_endpoint_hint(
        monkeypatch,
        env={
            "OLLAMA_HOST": "http://localhost:11434",
            "XAI_API_KEY": "xai-test",
            "GEMINI_API_KEY": "gemini-test",
            "GROQ_API_KEY": "groq-test",
        },
        which_map={},
    )
    assert hint == "ollama"


def test_llm_endpoint_bound_empty_xai_key_falls_through(monkeypatch) -> None:
    """Empty-string key does not count as bound — matches OPENAI_API_KEY
    pattern where empty string is treated as unset."""
    hint = _get_endpoint_hint(
        monkeypatch,
        env={"XAI_API_KEY": ""},
        which_map={},
    )
    assert hint == "unset"


# ─────────────────────────────────────────────────────────────────────
# Task #11 — schema_version + session_boundary contract fields.
# ─────────────────────────────────────────────────────────────────────


def test_get_status_schema_version_is_present() -> None:
    """The token-safe request-identity contract is status schema version 2."""
    payload = json.loads(get_status())
    assert "schema_version" in payload
    assert payload["schema_version"] == 3


def test_get_status_schema_contract() -> None:
    """Contract test: every documented top-level field must be present.

    If this test fails after a get_status change, the author must bump
    schema_version and update the docstring contract in universe_server.py.
    """
    payload = json.loads(get_status())
    required_top_level = {
        "schema_version",
        "active_host",
        "tier_routing_policy",
        "evidence",
        "evidence_caveats",
        "caveats",
        "actionable_next_steps",
        "request_identity",
        "session_boundary",
        "storage_utilization",
        "per_provider_cooldown_remaining",
        "universe_id",
        "universe_exists",
    }
    for key in required_top_level:
        assert key in payload, f"Contract field missing from get_status: '{key}'"

    required_evidence = {
        "last_completed_request_llm_used",
        "activity_log_tail",
        "activity_log_line_count",
        "last_n_calls",
        "policy_hash",
    }
    for key in required_evidence:
        assert key in payload["evidence"], f"evidence.{key} missing"

    required_session_boundary = {
        "prior_session_context_available",
        "principal_fingerprint",
        "last_session_ts",
        "note",
    }
    for key in required_session_boundary:
        assert key in payload["session_boundary"], f"session_boundary.{key} missing"


def test_get_status_session_boundary_no_prior_when_empty_log(tmp_path) -> None:
    """Universe with no activity returns prior_session_context_available=false."""
    import os
    os.environ["TINYASSETS_DATA_DIR"] = str(tmp_path)
    udir = tmp_path / "empty_sb_universe"
    udir.mkdir(parents=True, exist_ok=True)
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(tmp_path, "empty_sb_universe")
    payload = json.loads(get_status(command_center_id="empty_sb_universe"))
    sb = payload["session_boundary"]
    assert sb["prior_session_context_available"] is False
    assert sb["last_session_ts"] is None
    assert sb["principal_fingerprint"] == payload["request_identity"][
        "principal_fingerprint"
    ]


def test_get_status_session_boundary_does_not_use_environment_actor(
    tmp_path, monkeypatch
) -> None:
    """Ambient host identity cannot become request or prior-session identity."""
    user = "test_session_user"
    monkeypatch.setenv("UNIVERSE_SERVER_USER", user)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    udir = tmp_path / "active_sb_universe"
    udir.mkdir(parents=True, exist_ok=True)
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(tmp_path, "active_sb_universe")
    (udir / "activity.log").write_text(
        f"[2026-04-24 12:00:00] [{user}] some activity\n",
        encoding="utf-8",
    )
    payload = json.loads(get_status(command_center_id="active_sb_universe"))
    sb = payload["session_boundary"]
    assert sb["prior_session_context_available"] is False
    assert sb["last_session_ts"] is None
    assert "account_user" not in sb


def test_get_status_request_identity_ignores_environment_actor(monkeypatch) -> None:
    """The status identity is request-local and never falls back to host env.

    It used to fingerprint an "anonymous" subject when no bearer was present;
    there is no such subject now, so the fingerprint is the BOUND principal's
    and the environment actor still appears nowhere."""
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "my_user")
    payload = json.loads(get_status())
    fingerprint = payload["request_identity"]["principal_fingerprint"]
    assert fingerprint.startswith("v1:")
    assert "anonymous" not in fingerprint
    assert "my_user" not in json.dumps(payload, sort_keys=True)


def test_get_status_recent_conversation_denied_to_a_non_founder(
    tmp_path, monkeypatch,
) -> None:
    """The peek is FOUNDER-only. A reader who is not this universe's founder
    must not receive it even with the opt-in set and recorded turns present --
    the shared conversation thread must never leak through a status read.

    It used to be an ANONYMOUS reader; with no anonymous principal the same
    boundary is asserted against a signed-in stranger, which is the caller
    that can actually reach the surface now."""
    from tinyassets.conversation_store import record_exchange

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    uid = "peek_universe"
    udir = tmp_path / uid
    udir.mkdir(parents=True, exist_ok=True)
    record_exchange(udir, "principal:someone", "secret question", "secret answer")

    # A signed-in STRANGER: not this universe's founder. (The suite's autouse
    # operator already is one; naming it here is what makes the boundary the
    # subject of the test rather than an accident of the fixture.)
    from tinyassets.auth import middleware as _mw
    from tinyassets.auth.provider import Identity as _Identity

    _mw._current_identity.set(_Identity(
        user_id="stranger", username="stranger", capabilities=["read", "list"],
    ))
    payload = json.loads(get_status(command_center_id=uid, include_conversation=True))
    assert "recent_conversation" not in payload, (
        "conversation peek must be withheld from an anonymous reader"
    )
    assert "secret answer" not in json.dumps(payload), (
        "recorded conversation content must not leak into a non-founder status read"
    )


def test_get_status_recent_conversation_optin_gate_and_isolation(
    tmp_path, monkeypatch
) -> None:
    """Opt-out default, founder opt-in, non-founder denial, and per-principal
    isolation of co-located founders sharing one universe DB.

    Covers Codex 2026-08-23 must-fix. The two permission calls the peek makes
    (`universe_access_allows`, `current_actor_id`) are stubbed so the test
    exercises the peek's gate + fence + principal-keyed session directly, without
    the universe-provisioning/create-scope machinery."""
    from tinyassets.api import permissions
    from tinyassets.conversation_store import record_exchange

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    uid = "shared_universe"
    udir = tmp_path / uid
    udir.mkdir(parents=True, exist_ok=True)
    # Two founders' turns co-located in ONE universe DB, keyed by principal.
    receipt_a = {"provider": "provider-a", "model": "model-a", "model_status": "reported"}
    record_exchange(udir, "principal:founder-a", "A question", "A answer", execution=receipt_a)
    record_exchange(udir, "principal:founder-b", "B question", "B answer")

    def _as(actor: str, allowed: bool) -> None:
        monkeypatch.setattr(permissions, "current_actor_id", lambda: actor)
        monkeypatch.setattr(
            permissions, "universe_access_allows", lambda _uid, write=False: allowed
        )

    # Opt-OUT default: founder A, but no include_conversation → no transcript.
    _as("founder-a", True)
    assert "recent_conversation" not in json.loads(get_status(command_center_id=uid))

    # Opt-IN: founder A sees ONLY A's own turns, fenced as untrusted.
    _as("founder-a", True)
    pa = json.loads(get_status(command_center_id=uid, include_conversation=True))
    rc = pa["recent_conversation"]
    assert "execution" not in rc["turns"][0]
    assert rc["turns"][1]["execution"] == receipt_a
    assert rc["content_is_untrusted"] is True
    texts_a = " ".join(t["text"] for t in rc["turns"])
    assert "A answer" in texts_a and "B answer" not in texts_a  # principal isolation

    # Co-located founder B sees ONLY B's own turns, never A's.
    _as("founder-b", True)
    pb = json.loads(get_status(command_center_id=uid, include_conversation=True))
    texts_b = " ".join(t["text"] for t in pb["recent_conversation"]["turns"])
    assert "B answer" in texts_b and "A answer" not in texts_b
    assert "provider-a" not in json.dumps(pb)

    # Non-founder (no write access): peek withheld entirely, no content leak.
    _as("stranger", False)
    pc = json.loads(get_status(command_center_id=uid, include_conversation=True))
    assert "recent_conversation" not in pc
    assert "A answer" not in json.dumps(pc) and "B answer" not in json.dumps(pc)
