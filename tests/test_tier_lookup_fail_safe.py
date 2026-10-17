"""Owner plan reads never open a per-center database or invent a free plan."""

from __future__ import annotations

import pytest

from tinyassets.effectors.outbound_boundary import execute_replay_safe_effect
from tinyassets.storage.external_write_receipts import STATUS_SUCCEEDED


def test_an_unusable_universe_dir_does_not_stop_an_outbound_effect(tmp_path, monkeypatch):
    """The property that actually matters, at the boundary that actually ships."""
    universe = tmp_path / "universe"
    universe.mkdir()
    calls: list[str] = []

    # Break ONLY the subscription-state connection. Patching Path.mkdir globally also
    # broke the receipt store, which this test is not about -- and a test that fails
    # for a second reason cannot show the first one was fixed.
    def _unusable(*_a, **_kw):
        raise PermissionError("read-only fs")

    with monkeypatch.context() as patch:
        patch.setattr("tinyassets.storage.subscription_state.get_plan", _unusable)
        result = execute_replay_safe_effect(
            universe_dir=universe,
            effect_key="e1",
            sink="test_sink",
            run_id="run-1",
            invoke=lambda: calls.append("e1") or {"ok": True},
        )

    assert result["status"] == STATUS_SUCCEEDED
    assert calls == ["e1"], "a broken tier lookup must not stop the world"


@pytest.mark.parametrize("reader", ["get_plan", "get_tier", "read_tier"])
@pytest.mark.parametrize("tier", ["free", "paid"])
def test_tier_read_uses_bound_plan_even_with_unusable_local_directory(
    tmp_path, monkeypatch, reader, tier,
):
    from tinyassets.storage import subscription_state

    calls = []

    def remote(operation):
        calls.append(operation)
        return {"tier": tier, "ends_at": 123}

    monkeypatch.setattr("tinyassets.broker.owner_reads.remote", remote)
    path = tmp_path / "not-a-directory"
    path.write_text("no local billing store")
    result = getattr(subscription_state, reader)(path / "center")
    assert result == ({"tier": tier, "ends_at": 123} if reader == "get_plan" else tier)
    assert calls == ["plan"]
    assert path.read_text() == "no local billing store"


@pytest.mark.parametrize("reader", ["get_plan", "get_tier", "read_tier"])
def test_missing_plan_fails_loudly(tmp_path, monkeypatch, reader):
    from tinyassets.broker.owner_reads import PlanMissing
    from tinyassets.storage import subscription_state

    def missing(operation):
        raise PlanMissing("missing bound plan")

    monkeypatch.setattr("tinyassets.broker.owner_reads.remote", missing)
    with pytest.raises(PlanMissing, match="missing bound plan"):
        getattr(subscription_state, reader)(tmp_path)
