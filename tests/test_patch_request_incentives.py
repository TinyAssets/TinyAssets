"""Patch-request incentive and requester-directed daemon routing tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets.api.market import classify_filing_effort, filing_effort_dispatch_route
from tinyassets.auth.provider import Identity
from tinyassets.branch_tasks import BranchTask, read_queue
from tinyassets.daemon_server import grant_universe_access
from tinyassets.control_plane.queue_policy import DispatcherConfig, score_task
from tinyassets.work_targets import (
    choose_authorial_targets,
    load_work_targets,
    materialize_pending_requests,
    requests_path,
)


def _authenticate_request(
    monkeypatch: pytest.MonkeyPatch,
    *,
    base: Path,
    universe_id: str,
    actor_id: str,
) -> None:
    identity = Identity(
        user_id=actor_id,
        username=actor_id,
        capabilities=["write"],
    )
    monkeypatch.setattr(
        "tinyassets.auth.middleware.current_identity",
        lambda: identity,
    )
    grant_universe_access(
        base,
        universe_id=universe_id,
        actor_id=actor_id,
        permission="write",
        granted_by=actor_id,
    )


@pytest.fixture
def server_base(tmp_path: Path, monkeypatch):
    base = tmp_path / "output"
    base.mkdir()
    uid = "test-uni"
    udir = base / uid
    udir.mkdir()
    (udir / "PROGRAM.md").write_text(
        "Legacy fixture with an explicit compatibility Loop.",
        encoding="utf-8",
    )
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)
    monkeypatch.setenv("UNIVERSE_SERVER_HOST_USER", "host")
    _authenticate_request(
        monkeypatch,
        base=base,
        universe_id=uid,
        actor_id="alice",
    )
    return base, uid


def _submit(**kwargs):
    from tinyassets.api.universe import _action_submit_request

    return json.loads(_action_submit_request(**kwargs))


def test_pickup_incentive_boosts_pickup_not_acceptance(
    server_base,
    monkeypatch,
):
    base, uid = server_base
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")

    response = _submit(
        universe_id=uid,
        text="Please implement the loop status badge.",
        request_type="branch_proposal",
        priority_weight=0.0,
        pickup_incentive="20 credits for an accepted review packet",
    )

    queue = read_queue(base / uid)
    assert response["priority_weight"] == 0.0
    assert response["pickup_incentive"]["enabled"] is True
    assert response["authority_boundary"] == {
        "affects_pickup_priority": True,
        "affects_acceptance": False,
        "affects_release": False,
        "affects_merge": False,
    }
    assert response["request_classification"] == {
        "access": {
            "claimable_by": ["free_daemon", "paid_daemon"],
            "code_writer_gate": "claude_or_codex",
            "checker_gate": "opposite_family_checker",
        },
        "meaning": "branch_refinement",
        "authority": {
            "scope": "requester_pickup_only",
            "boundary": response["authority_boundary"],
        },
    }
    assert queue[0].priority_weight == 0.0
    assert 0.0 < queue[0].pickup_signal_weight <= 5.0
    assert queue[0].trigger_source == "user_request"
    assert (
        queue[0].inputs["request_classification"]
        == response["request_classification"]
    )

    plain = BranchTask(
        branch_task_id="plain",
        branch_def_id=queue[0].branch_def_id,
        universe_id=uid,
        trigger_source="user_request",
        queued_at=queue[0].queued_at,
    )
    host = BranchTask(
        branch_task_id="host",
        branch_def_id=queue[0].branch_def_id,
        universe_id=uid,
        trigger_source="host_request",
        queued_at=queue[0].queued_at,
    )
    cfg = DispatcherConfig()
    assert score_task(queue[0], now_iso=queue[0].queued_at, config=cfg) > score_task(
        plain, now_iso=plain.queued_at, config=cfg,
    )
    assert score_task(host, now_iso=host.queued_at, config=cfg) > score_task(
        queue[0], now_iso=queue[0].queued_at, config=cfg,
    )


def test_incentivized_request_materializes_ahead_with_metadata(server_base):
    base, uid = server_base
    udir = base / uid
    requests_path(udir).write_text(
        json.dumps([
            {
                "id": "req_plain",
                "type": "general",
                "text": "plain request",
                "status": "pending",
                "source": "bob",
            },
            {
                "id": "req_incentive",
                "type": "general",
                "text": "incentivized request",
                "status": "pending",
                "source": "alice",
                "pickup_incentive": {
                    "enabled": True,
                    "terms": "tip after review packet",
                    "pickup_signal_weight": 5.0,
                    "visibility": "public",
                },
                "authority_boundary": {
                    "affects_pickup_priority": True,
                    "affects_acceptance": False,
                    "affects_release": False,
                    "affects_merge": False,
                },
                "request_classification": {
                    "access": {
                        "claimable_by": ["free_daemon", "paid_daemon"],
                        "code_writer_gate": "claude_or_codex",
                        "checker_gate": "opposite_family_checker",
                    },
                    "meaning": "patch",
                    "authority": {
                        "scope": "requester_pickup_only",
                        "boundary": {
                            "affects_pickup_priority": True,
                            "affects_acceptance": False,
                            "affects_release": False,
                            "affects_merge": False,
                        },
                    },
                },
            },
        ]),
        encoding="utf-8",
    )

    created = materialize_pending_requests(udir)
    ranked = choose_authorial_targets(udir, candidate_override=created)

    assert ranked[0].metadata["request_id"] == "req_incentive"
    assert ranked[0].metadata["pickup_incentive"]["enabled"] is True
    assert ranked[0].metadata["request_classification"]["meaning"] == "patch"
    assert "pickup-incentive" in ranked[0].tags
    assert "meaning:patch" in ranked[0].tags
    stored = load_work_targets(udir)
    assert any(t.metadata.get("request_id") == "req_plain" for t in stored)


def test_submit_request_classifies_access_meaning_and_authority(
    server_base,
    monkeypatch,
):
    base, uid = server_base
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")

    response = _submit(
        universe_id=uid,
        text="Patch-loop should classify requests before implementation.",
        request_type="general",
    )

    assert response["request_classification"]["access"] == {
        "claimable_by": ["free_daemon", "paid_daemon"],
        "code_writer_gate": "claude_or_codex",
        "checker_gate": "opposite_family_checker",
    }
    assert response["request_classification"]["meaning"] == "patch"
    assert response["request_classification"]["authority"] == {
        "scope": "requester_pickup_only",
        "boundary": response["authority_boundary"],
    }
    queue = read_queue(base / uid)
    assert (
        queue[0].inputs["request_classification"]
        == response["request_classification"]
    )


def test_filing_effort_classifier_flags_research_ghost_risk():
    result = classify_filing_effort(
        title=(
            "Filing-time effort-class prediction circuit-breaker classifier "
            "for MSR prior art"
        ),
        kind="patch_request",
        observed=(
            "Mechanical filing cites AUC 0.96 on 33K agent PRs and requires "
            "opposite-family checker attention before stall."
        ),
        tags="mechanical",
    )

    assert result["effort_class"] == "ghost-risk"
    assert result["attention"] == "carrier-review-before-daemon-pickup"
    assert "research_prior_art" in result["signals"]
    assert "review_gate" in result["signals"]
    assert result["authority_boundary"]["affects_merge"] is False


def test_filing_effort_classifier_flags_merge_instant_without_ghost_signals():
    result = classify_filing_effort(
        title="Fix typo in connector docs",
        kind="patch_request",
        observed="Mechanical docs-only copy edit with no runtime behavior change.",
    )

    assert result["effort_class"] == "merge-instant"
    assert result["attention"] == "normal-review-gates"
    assert "mechanical_shape" in result["signals"]
    assert "low_runtime_risk" in result["signals"]


def test_filing_effort_classifier_uses_structural_cross_reference_signal():
    result = classify_filing_effort(
        title="Fix typo in connector docs",
        kind="patch_request",
        observed="Mechanical docs-only copy edit with no runtime behavior change.",
        cross_reference_count=4,
    )

    assert result["effort_class"] == "standard"
    assert result["attention"] == "normal-review-gates"
    assert result["combiner"] == "rule_based"
    assert result["confidence"] == "heuristic"
    assert "cross_reference_count" in result["signals"]
    assert result["structural_features"]["cross_reference_count"] == 4


def test_filing_effort_classifier_flags_length_ratio_ghost_risk():
    result = classify_filing_effort(
        title="Mechanical patch request with unusually long observations",
        kind="patch_request",
        observed=(
            "The requested change looks like a small config update, but the filing "
            "spends several paragraphs describing prior failures, recovery paths, "
            "review conditions, rollback hazards, hidden state, and operational "
            "constraints that make the implementation much broader than the title "
            "suggests. It also cites multiple situations where similar changes "
            "were reverted after passing local tests."
        ),
        expected="Update the config default.",
    )

    assert result["effort_class"] == "ghost-risk"
    assert result["attention"] == "carrier-review-before-daemon-pickup"
    assert "observed_expected_length_ratio" in result["signals"]
    assert result["structural_features"]["observed_expected_length_ratio"] > 3.0


def test_filing_effort_classifier_flags_ghost_tag_cluster_overlap():
    result = classify_filing_effort(
        title="Mechanical follow-up from reverted checker lane",
        kind="patch_request",
        observed="Looks mechanical.",
        tags="mechanical, reverted, checker, prior-art",
    )

    assert result["effort_class"] == "ghost-risk"
    assert "ghost_tag_cluster_overlap" in result["signals"]
    assert result["structural_features"]["ghost_tag_cluster_overlap"] >= 2


def test_merge_instant_effort_route_boosts_dispatcher_pickup_not_host_tier():
    effort = classify_filing_effort(
        title="Fix typo in connector docs",
        kind="patch_request",
        observed="Mechanical docs-only copy edit with no runtime behavior change.",
    )
    route = filing_effort_dispatch_route(effort)

    assert route["lane"] == "merge-instant-fast-lane"
    assert route["triage_policy"] == "skip-extended-triage-when-no-ghost-signals"
    assert 0.0 < route["pickup_signal_weight"] <= 5.0

    now = "2026-05-22T00:00:00+00:00"
    merge_instant = BranchTask(
        branch_task_id="merge-instant",
        branch_def_id="branch",
        universe_id="u",
        trigger_source="owner_queued",
        queued_at=now,
        pickup_signal_weight=route["pickup_signal_weight"],
    )
    standard = BranchTask(
        branch_task_id="standard",
        branch_def_id="branch",
        universe_id="u",
        trigger_source="owner_queued",
        queued_at=now,
    )
    host = BranchTask(
        branch_task_id="host",
        branch_def_id="branch",
        universe_id="u",
        trigger_source="host_request",
        queued_at=now,
    )
    cfg = DispatcherConfig()

    assert (
        score_task(merge_instant, now_iso=now, config=cfg)
        > score_task(standard, now_iso=now, config=cfg)
    )
    assert (
        score_task(host, now_iso=now, config=cfg)
        > score_task(merge_instant, now_iso=now, config=cfg)
    )


def test_ghost_risk_effort_route_requests_carrier_attention_without_pickup_boost():
    effort = classify_filing_effort(
        title="Mechanical classifier follow-up with MSR benchmark prior art",
        kind="patch_request",
        observed="Needs opposite-family checker attention before ten-day stall.",
    )
    route = filing_effort_dispatch_route(effort)

    assert route["lane"] == "carrier-attention"
    assert route["triage_policy"] == "notify-carrier-before-daemon-pickup"
    assert route["attention_family"] == "opposite-family-checker"
    assert route["pickup_signal_weight"] == 0.0


def test_requester_directed_daemon_requires_ownership(
    server_base,
    monkeypatch,
):
    base, uid = server_base
    from tinyassets import daemon_registry

    daemon = daemon_registry.create_daemon(
        base,
        display_name="Alice Patch Runner",
        created_by="alice",
        soul_text="Work on Alice's requested patches.",
    )

    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    accepted = _submit(
        universe_id=uid,
        text="Work on the badge request.",
        request_type="branch_proposal",
        directed_daemon_id=daemon["daemon_id"],
        directed_daemon_instruction="Produce a proposal only.",
    )
    queue = read_queue(base / uid)
    assert accepted["requester_directed_daemon"]["effect"] == "applied"
    assert queue[0].trigger_source == "owner_queued"
    assert queue[0].priority_weight == 0.0
    assert queue[0].directed_daemon_id == daemon["daemon_id"]
    assert queue[0].inputs["requester_directed_daemon"]["scope"] == "proposal_only"

    monkeypatch.setenv("UNIVERSE_SERVER_USER", "bob")
    _authenticate_request(
        monkeypatch,
        base=base,
        universe_id=uid,
        actor_id="bob",
    )
    refused = _submit(
        universe_id=uid,
        text="Bob tries to use Alice's daemon.",
        request_type="branch_proposal",
        directed_daemon_id=daemon["daemon_id"],
    )
    assert refused["error"] == "directed_daemon_not_authorized"
