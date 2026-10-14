"""Patch-request incentive and requester-directed daemon routing tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets.api.market import classify_filing_effort, filing_effort_dispatch_route
from tinyassets.auth.provider import Identity
from tinyassets.daemon_server import grant_universe_access
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


