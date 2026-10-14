"""Old paid-market configuration cannot revive a queue execution tier."""
from tinyassets.branch_tasks_v2 import BranchTask
from tinyassets.control_plane.queue_policy import load_dispatcher_config, score_task


def test_legacy_paid_config_is_inert_and_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "on")
    path = tmp_path / "dispatcher_config.yaml"
    raw = (
        "accept_paid_bids: true\nbid_coefficient: 100\nbid_term_cap: 9999\n"
        "accept_external_requests: false\ntier_weights:\n  paid_bid: 9999\n"
        "  owner_queued: 81\nserved_llm_type: connected-model\n"
    )
    path.write_text(raw)
    config = load_dispatcher_config(tmp_path)
    assert not config.tier_enabled("paid_bid")
    assert "paid_bid" not in config.tier_status_map()
    assert "paid_bid" not in config.tier_weights
    assert not config.tier_enabled("user_request")
    assert config.tier_enabled("owner_queued")
    assert config.served_llm_type == "connected-model"
    assert path.read_text() == raw
    task = BranchTask("t", "b", "u", trigger_source="owner_queued",
                      queued_at="2026-10-14T00:00:00+00:00", bid=1000000)
    assert score_task(task, now_iso=task.queued_at, config=config) == 91


def test_paid_market_flag_does_not_enable_unconfigured_tier(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "on")
    config = load_dispatcher_config(tmp_path)
    assert not config.tier_enabled("paid_bid")
    assert config.tier_enabled("user_request")
