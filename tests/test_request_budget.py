"""Daily request evidence includes failed attempts and excludes unrelated sources."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tinyassets.providers.free_sources import daily_cap_for_host
from tinyassets.request_budget import PooledBudget, RequestBudget, request_budget, requests_today
from tinyassets.storage import DB_FILENAME
from tinyassets.storage.agent_turn_journal import ensure_schema

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
PRESET = daily_cap_for_host("openrouter.ai")


def seed_requests(base, count, *, owner="owner", source="connection", model="model:free",
                  created_at=NOW, failed=0, turn_id="seed"):
    with sqlite3.connect(base / DB_FILENAME) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO agent_turns (owner_user_id, universe_id, turn_id, version, generation, "
            "state, round_ordinal, input_json, created_at) "
            "VALUES (?, 'seed-universe', ?, 1, 1, 'completed', ?, '{}', ?)",
            (owner, turn_id, count, created_at.isoformat()),
        )
        for ordinal in range(1, count + 1):
            failure = ordinal <= failed
            conn.execute(
                "INSERT INTO agent_turn_rounds VALUES (?, 'seed-universe', ?, ?, 1, ?, ?, ?, 0)",
                (owner, turn_id, ordinal, "failed" if failure else "completed",
                 json.dumps({"source_ref": source, "model": model}), None if failure else "{}"),
            )


def budget(base, **kwargs):
    return request_budget(base, "owner", "connection", "model:free", preset=PRESET, now=NOW,
                          **kwargs)


def test_counts_failed_rounds_and_only_this_owner_connection_free_models_today(tmp_path):
    seed_requests(tmp_path, 3, failed=2)
    seed_requests(tmp_path, 4, owner="someone-else", turn_id="foreign-owner")
    seed_requests(tmp_path, 5, source="other-connection", turn_id="foreign-source")
    seed_requests(tmp_path, 6, model="paid", turn_id="paid")
    seed_requests(tmp_path, 2, model="zero", turn_id="price-zero")
    seed_requests(tmp_path, 7, created_at=NOW.replace(hour=0) - timedelta(microseconds=1),
                  turn_id="yesterday")
    seed_requests(tmp_path, 1, created_at=NOW.replace(hour=0), turn_id="at-reset")
    assert budget(tmp_path, zero_priced_models={"zero"}).used == 6


def test_submillisecond_reset_and_current_time_boundaries(tmp_path):
    for ordinal, instant in enumerate((
        NOW.replace(hour=0) - timedelta(microseconds=1),
        NOW.replace(hour=0), NOW, NOW + timedelta(microseconds=1),
    )):
        seed_requests(tmp_path, 1, created_at=instant, turn_id=f"boundary-{ordinal}")
    assert budget(tmp_path).used == 2


@pytest.mark.parametrize("broken", [False, True])
def test_unreadable_journal_returns_unknown_without_creating_it(tmp_path, broken):
    path = tmp_path / DB_FILENAME
    if broken:
        path.write_text("not a database")
    assert requests_today(tmp_path, "owner", "connection", reset_timezone="UTC", now=NOW) is None
    assert budget(tmp_path) is None
    assert path.exists() == broken


def test_source_reset_timezone_is_data(tmp_path):
    seed_requests(tmp_path, 3, created_at=NOW.replace(hour=6))
    assert requests_today(tmp_path, "owner", "connection", reset_timezone="America/Los_Angeles",
                          now=NOW) == (0, 0)
    assert requests_today(tmp_path, "owner", "connection", reset_timezone="UTC", now=NOW) == (3, 3)


@pytest.mark.parametrize("failed,cap", [(0, 1000), (51, 50)])
def test_success_past_declared_cap_self_corrects_but_failures_do_not(tmp_path, failed, cap):
    seed_requests(tmp_path, 51, failed=failed)
    assert budget(tmp_path).cap == cap


def test_success_past_cap_without_larger_declared_allowance_is_unknown(tmp_path):
    seed_requests(tmp_path, 51)
    preset = {k: v for k, v in PRESET.items() if k != "credit_requests_per_day"}
    assert request_budget(tmp_path, "owner", "connection", "model:free", preset=preset,
                          now=NOW) is None


@pytest.mark.parametrize("remaining", [50, 10, 3, 0])
def test_prompt_only_describes_daily_pool(remaining):
    value = PooledBudget((("source", RequestBudget(50 - remaining, 50, "OpenRouter", "UTC")),))
    line = value.prompt_line()
    assert f"about {remaining} requests left" in line
    assert "resets 00:00 UTC" in line
    assert "notes/<project>-progress.md" in line
    assert "working slice" not in line
    assert "not a confirmed account quota" in line
    assert "Even at zero I continue the requested work" in line
    assert "request can only reply in text" not in line


def test_adaptive_cap_is_removed():
    from tinyassets import request_budget as module

    assert not hasattr(module, "MAX_TURN_REQUESTS")
    assert not hasattr(module, "MIN_TURN_REQUESTS")
    assert not hasattr(RequestBudget, "planned_requests")


def test_uncapped_source_or_paid_model_has_no_budget(tmp_path):
    seed_requests(tmp_path, 2)
    assert request_budget(tmp_path, "owner", "connection", "model:free", preset={}, now=NOW) is None
    assert request_budget(tmp_path, "owner", "connection", "paid", preset=PRESET, now=NOW) is None


def test_openrouter_allowance_is_installed_data():
    assert PRESET["requests_per_day"] == 50
    assert PRESET["credit_requests_per_day"] == 1000
    assert PRESET["reset_timezone"] == "UTC"
    assert PRESET["credit_url"] == "https://openrouter.ai/settings/credits"


def test_connect_screen_cap_shape_and_unknown_host(monkeypatch):
    from tinyassets.providers import free_sources

    monkeypatch.setattr(free_sources, "_SOURCES", [{
        "name": "Synthetic", "base_url": "https://capped.example/v1",
        "billing_url": "https://capped.example/billing",
        "daily_cap": {"requests_per_day": 25, "tokens_per_minute": 1000,
                      "reset_timezone": "America/Los_Angeles",
                      "source_url": "https://capped.example/limits"},
    }])
    assert daily_cap_for_host("capped.example") == {
        "requests_per_day": 25, "credit_requests_per_day": None,
        "reset_timezone": "America/Los_Angeles", "name": "Synthetic",
        "credit_url": "https://capped.example/billing",
    }
    assert daily_cap_for_host("uncapped.example") is None


def test_earliest_reset_guidance_uses_utc_and_relative_time(monkeypatch):
    from tinyassets import request_budget as budgets

    monkeypatch.setattr(budgets, "_now", lambda: NOW)
    pool = PooledBudget((
        ("utc", RequestBudget(49, 50, "UTC source", "UTC")),
        ("tokyo", RequestBudget(50, 50, "Tokyo source", "Asia/Tokyo")),
    ))
    assert pool.reset_description() == "2026-10-02 15:00 UTC (in about 3 hours)"


def test_credit_suggestion_uses_installed_amount_and_url(tmp_path):
    seed_requests(tmp_path, 45)
    preset = {**PRESET, "credit_amount": "$7", "credit_url": "https://example.com/credit",
              "credit_requests_per_day": 700}
    source = request_budget(tmp_path, "owner", "connection", "model:free", preset=preset, now=NOW)
    suggestion = PooledBudget((("source", source),)).connect_suggestion()
    assert "$7" in suggestion and "https://example.com/credit" in suggestion
    assert "700" in suggestion and "$10" not in suggestion
    assert "local free-request estimate" in suggestion
    assert "your account may already qualify" in suggestion


def test_durable_dispatch_times_include_internal_failures_and_survive_journal_deletion(tmp_path):
    from tinyassets.request_budget import TurnRequestBudget

    midnight = NOW.replace(hour=0)
    clock = [midnight - timedelta(microseconds=1)]
    parent = TurnRequestBudget("owner", "seed-universe", wall_clock=lambda: clock[0],
                               failure_limit=None)
    parent.persist(tmp_path)
    parent.link("turn", "linked")
    seed_requests(tmp_path, 20, turn_id="linked")
    for source, free, purpose, outcome in [
        ("connection", True, "reply", "failed"),  # Yesterday, not today.
        ("connection", True, "reply", "failed"),
        ("connection", True, "review", "succeeded"),
        ("connection", True, "helper", "unknown"),
        ("connection", False, "learning", "succeeded"),  # Paid: separate policy.
        ("other", True, "reply", "failed"),
    ]:
        ordinal = parent.reserve(owner="owner", universe="seed-universe", source_ref=source,
                                 model="model", free=free, purpose=purpose)
        parent.dispatched(ordinal)
        parent.settle(ordinal, outcome)
        clock[0] = midnight
    # No grant or effect occurs for a reserved but never dispatched request.
    ordinal = parent.reserve(owner="owner", universe="seed-universe", source_ref="connection",
                             model="model", free=True)
    parent.settle(ordinal, "not_sent")
    assert requests_today(tmp_path, "owner", "connection", reset_timezone="UTC", now=NOW) == (3, 2)
    seed_requests(tmp_path, 1, turn_id="legacy")
    assert requests_today(tmp_path, "owner", "connection", reset_timezone="UTC", now=NOW) == (4, 4)
    with sqlite3.connect(tmp_path / DB_FILENAME) as conn:
        conn.execute("DELETE FROM agent_turn_rounds")
        conn.execute("DELETE FROM agent_turns")
    assert requests_today(tmp_path, "owner", "connection", reset_timezone="UTC", now=NOW) == (3, 2)
    assert requests_today(tmp_path, "other-owner", "connection",
                          reset_timezone="UTC", now=NOW) == (0, 0)
    parent.close()
