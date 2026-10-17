"""New-turn hints cannot cross owners/custody or alter explicit orders."""

from dataclasses import replace

import pytest

from tinyassets.providers.agent_model_plan import AgentModelPlan
from tinyassets.providers.model_policy import (
    Catalog,
    ConnectionModels,
    Interaction,
    Model,
    ModelPolicy,
    ModelRef,
    Pricing,
)
from tinyassets.providers.source_health import SourceHealth, SourceKey

KEY = SourceKey("base", "owner", "universe", "future-provider", "ref", 1, "digest")


@pytest.mark.parametrize("field,value", [
    ("base", "other"), ("owner", "other"), ("universe", "other"),
    ("provider", "other"), ("reference", "other"), ("generation", 2), ("digest", "other"),
])
def test_exact_scope_isolation(field, value):
    health = SourceHealth()
    health.authentication_failed(KEY)
    assert health.needs_reconnect(KEY)
    assert not health.needs_reconnect(replace(KEY, **{field: value}))


def test_capacity_and_exact_success():
    health = SourceHealth(capacity=2)
    second, third = replace(KEY, owner="2"), replace(KEY, owner="3")
    for key in (KEY, second, third):
        health.authentication_failed(key)
    assert not health.needs_reconnect(KEY)
    assert health.needs_reconnect(second)
    health.succeeded(second)
    assert not health.needs_reconnect(second)
    assert health.needs_reconnect(third)


@pytest.mark.parametrize("elapsed", [420, 86400])
def test_elapsed_time_is_not_authentication_recovery(elapsed):
    now = [0]
    health = SourceHealth(clock=lambda: now[0])
    health.authentication_failed(KEY)
    now[0] = elapsed
    assert health.needs_reconnect(KEY)
    health.succeeded(KEY)
    assert not health.needs_reconnect(KEY)


def test_new_custody_has_no_failure_and_supersedes_older_generation():
    health = SourceHealth()
    newer = replace(KEY, generation=2, digest="new-digest")
    health.authentication_failed(KEY)
    assert not health.needs_reconnect(newer)
    health.authentication_failed(newer)
    assert not health.needs_reconnect(KEY)
    assert health.needs_reconnect(newer)
    # A late old-generation completion cannot clear the newer failure.
    health.succeeded(KEY)
    assert health.needs_reconnect(newer)


def test_success_under_new_custody_removes_obsolete_generation():
    health = SourceHealth()
    health.authentication_failed(KEY)
    health.succeeded(replace(KEY, generation=2, digest="new-digest"))
    assert not health.needs_reconnect(KEY)


@pytest.mark.parametrize("field", ["base", "owner", "universe", "provider", "reference"])
def test_supersession_never_clears_another_source(field):
    health = SourceHealth()
    other = replace(KEY, **{field: "other"})
    health.authentication_failed(KEY)
    health.authentication_failed(other)
    health.succeeded(replace(KEY, generation=2, digest="new-digest"))
    assert not health.needs_reconnect(KEY)
    assert health.needs_reconnect(other)


def plan(policy=None, failed=("a",)):
    return AgentModelPlan(
        Catalog("owner", "u", tuple(ConnectionModels(
            name, name, "http", "fresh", True, True,
            (Model("", True, frozenset({"text"}), pricing=Pricing("fresh", unmetered=True)),),
            default_model_id="",
        ) for name in ("a", "b"))),
        policy or ModelPolicy(0, "automatic", ()),
        Interaction(True, frozenset({"text"}), frozenset()),
        reconnect_sources=failed,
    )


def test_new_auto_demotes_but_does_not_disable_failed_source():
    result = plan().order("owner", "u")
    assert [item.ref.connection_id for item in result.candidates] == ["b", "a"]
    assert "recent_sign_in_failure" in result.candidates[-1].labels
    assert plan(failed=("a", "b")).next_candidate("owner", "u") == ModelRef("a", "")


@pytest.mark.parametrize("field", ["current_selection", "saved_default"])
def test_explicit_order_never_changes(field):
    policy = ModelPolicy(0, "explicit", (ModelRef("b", ""),), **{field: ModelRef("a", "")})
    result = plan(policy).order("owner", "u")
    assert [item.ref.connection_id for item in result.candidates] == ["a", "b"]
    assert "recent_sign_in_failure" in result.candidates[0].labels


def test_health_does_not_bypass_scope_or_allowlist():
    with pytest.raises(ValueError):
        plan().order("another-owner", "u")
    original = plan()
    only_failed = replace(original, catalog=replace(
        original.catalog, connections=original.catalog.connections[:1],
    ))
    assert only_failed.next_candidate("owner", "u") == ModelRef("a", "")
