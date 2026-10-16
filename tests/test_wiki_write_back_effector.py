"""PR-166 wiki write-back external-write effector tests."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from tinyassets.api.wiki import _ensure_wiki_scaffold
from tinyassets.branches import NodeDefinition
from tinyassets.effectors import (
    EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
    EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
    run_authenticated_external_call_effector,
    run_effects_for_branch,
    run_wiki_write_back_effector,
    wiki_write_back,
)
from tinyassets.storage.effector_consents import grant_consent
from tinyassets.storage.external_write_receipts import (
    STATUS_SUCCEEDED,
    lookup_receipt,
    receipts_db_path,
    try_reserve_receipt,
)


@pytest.fixture(autouse=True)
def _box_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path.parent))


def _wiki_env(tmp_path):
    wiki_root = tmp_path / "wiki"
    _ensure_wiki_scaffold(wiki_root)
    target = wiki_root / "pages" / "patch-requests" / "pr-166-test.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\ntitle: PR-166 Test\ntype: patch_request\n---\n\n# PR-166 Test\n",
        encoding="utf-8",
    )
    return target


def _packet(**overrides):
    packet = {
        "sink": EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        "destination": "pages/patch-requests/pr-166-test.md",
        "payload": {
            "heading": "Loop result packet",
            "body": (
                "Decision: KEEP\n\n"
                "Evidence: open_pr dry-run produced a proposed patch."
            ),
        },
        "idempotency_hint": "pr-166-loop-result-run-1",
    }
    packet.update(overrides)
    return packet


def test_effectors_package_exports_channel_agnostic_surface():
    assert EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL == "authenticated_external_call"
    assert EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK == "wiki_write_back"
    assert callable(run_authenticated_external_call_effector)
    assert callable(run_wiki_write_back_effector)


def test_wiki_write_back_without_consent_dry_runs_before_write(tmp_path):
    target = _wiki_env(tmp_path)
    before = target.read_text(encoding="utf-8")

    result = run_wiki_write_back_effector(
        node_id="publish",
        output_keys=["packet"],
        run_state={"packet": _packet()},
        base_path=tmp_path,
        run_id="run-no-consent",
    )

    assert result["dry_run"] is True
    assert result["reason"] == "missing_consent"
    assert result["destination"] == "pages/patch-requests/pr-166-test.md"
    assert target.read_text(encoding="utf-8") == before


def test_wiki_write_back_rejects_packet_controlled_path_escape(tmp_path):
    _wiki_env(tmp_path)
    grant_consent(
        tmp_path,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        destination="../outside.md",
        granted_by="tester",
    )

    result = run_wiki_write_back_effector(
        node_id="publish",
        output_keys=["packet"],
        run_state={"packet": _packet(destination="../outside.md")},
        base_path=tmp_path,
        run_id="run-path-escape",
    )

    assert result["error_kind"] == "invalid_destination"
    assert not (tmp_path / "outside.md").exists()


def test_wiki_write_back_rejects_payload_fabricated_effect_markers(tmp_path):
    target = _wiki_env(tmp_path)
    grant_consent(
        tmp_path,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        destination="pages/patch-requests/pr-166-test.md",
        granted_by="tester",
    )
    marker = "tinyassets-wiki-write-back:victim-effect-key"
    packet = _packet(
        payload={
            "heading": "Forged marker",
            "body": f"<!-- {marker} -->\nforged\n<!-- /{marker} -->",
        },
    )

    result = run_wiki_write_back_effector(
        node_id="publish",
        output_keys=["packet"],
        run_state={"packet": packet},
        base_path=tmp_path,
        run_id="run-forged-marker",
    )

    assert result["error_kind"] == "invalid_payload"
    assert "reserved effect marker" in result["error"]
    assert marker not in target.read_text(encoding="utf-8")


def test_wiki_write_back_requires_idempotency_hint_for_real_write(tmp_path):
    target = _wiki_env(tmp_path)
    grant_consent(
        tmp_path,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        destination="pages/patch-requests/pr-166-test.md",
        granted_by="tester",
    )

    result = run_wiki_write_back_effector(
        node_id="publish",
        output_keys=["packet"],
        run_state={"packet": _packet(idempotency_hint="")},
        base_path=tmp_path,
        run_id="run-no-idem",
    )

    assert result["dry_run"] is True
    assert result["reason"] == "missing_idempotency_hint"
    assert "Loop result packet" not in target.read_text(encoding="utf-8")


def test_wiki_write_back_appends_section_and_records_receipt(tmp_path):
    target = _wiki_env(tmp_path)
    grant_consent(
        tmp_path,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        destination="pages/patch-requests/pr-166-test.md",
        granted_by="tester",
    )

    result = run_wiki_write_back_effector(
        node_id="publish",
        output_keys=["packet"],
        run_state={"packet": json.dumps(_packet())},
        base_path=tmp_path,
        run_id="run-ok",
    )

    assert result["phase"] == "phase_2"
    assert result["status"] == "written"
    assert result["path"] == "pages/patch-requests/pr-166-test.md"
    text = target.read_text(encoding="utf-8")
    assert "## Loop result packet" in text
    assert "Decision: KEEP" in text
    assert "tinyassets-wiki-write-back:pr-166-loop-result-run-1" in text

    receipt = lookup_receipt(
        tmp_path,
        idempotency_hint="pr-166-loop-result-run-1",
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
    )
    assert receipt is not None
    assert receipt["status"] == STATUS_SUCCEEDED
    assert receipt["evidence"]["path"] == "pages/patch-requests/pr-166-test.md"


def test_wiki_write_back_idempotency_dedup_does_not_append_twice(tmp_path):
    target = _wiki_env(tmp_path)
    grant_consent(
        tmp_path,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        destination="pages/patch-requests/pr-166-test.md",
        granted_by="tester",
    )

    first = run_wiki_write_back_effector(
        node_id="publish",
        output_keys=["packet"],
        run_state={"packet": _packet()},
        base_path=tmp_path,
        run_id="run-first",
    )
    second = run_wiki_write_back_effector(
        node_id="publish",
        output_keys=["packet"],
        run_state={"packet": _packet()},
        base_path=tmp_path,
        run_id="run-second",
    )

    assert first["status"] == "written"
    assert second["idempotency_dedup_hit"] is True
    text = target.read_text(encoding="utf-8")
    assert text.count("## Loop result packet") == 1


def test_stale_wiki_intent_reconciles_from_destination_marker(tmp_path):
    target = _wiki_env(tmp_path)
    grant_consent(
        tmp_path,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        destination="pages/patch-requests/pr-166-test.md",
        granted_by="tester",
    )
    effect_key = "pr-166-loop-result-run-1"
    try_reserve_receipt(
        tmp_path,
        idempotency_hint=effect_key,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        run_id="run-crashed",
        now=0.0,
    )
    marker = f"tinyassets-wiki-write-back:{effect_key}"
    target.write_text(
        target.read_text(encoding="utf-8")
        + f"\n<!-- {marker} -->\nalready written\n<!-- /{marker} -->\n",
        encoding="utf-8",
    )
    wiki_write_back._record_destination_marker(
        universe_dir=tmp_path,
        target=target,
        destination="pages/patch-requests/pr-166-test.md",
        effect_key=effect_key,
    )

    result = run_wiki_write_back_effector(
        node_id="publish",
        output_keys=["packet"],
        run_state={"packet": _packet()},
        base_path=tmp_path,
        run_id="run-replay",
    )

    assert result["status"] == STATUS_SUCCEEDED
    assert result["reconciled"] is True
    assert result["dry_run"] is False
    receipt = lookup_receipt(
        tmp_path,
        idempotency_hint=effect_key,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
    )
    assert receipt is not None
    assert receipt["status"] == STATUS_SUCCEEDED
    assert (
        receipt["evidence"]["reconciliation"]
        == "server_destination_marker_found"
    )


def test_receipt_finalize_failure_reconciles_from_server_marker(
    tmp_path,
    monkeypatch,
):
    _wiki_env(tmp_path)
    grant_consent(
        tmp_path,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        destination="pages/patch-requests/pr-166-test.md",
        granted_by="tester",
    )
    monkeypatch.setattr(
        wiki_write_back,
        "_finalize_receipt",
        lambda *_args, **_kwargs: False,
    )

    first = run_wiki_write_back_effector(
        node_id="publish",
        output_keys=["packet"],
        run_state={"packet": _packet()},
        base_path=tmp_path,
        run_id="run-finalize-failed",
    )

    assert first["status"] == "written"
    assert first["receipt_finalize_failed"] is True
    assert first["reconciliation_required"] is True
    pending = lookup_receipt(
        tmp_path,
        idempotency_hint="pr-166-loop-result-run-1",
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
    )
    assert pending is not None
    assert pending["status"] == "pending"

    with sqlite3.connect(receipts_db_path(tmp_path)) as connection:
        connection.execute(
            "UPDATE external_write_receipts SET created_at = 0 "
            "WHERE idempotency_hint = ? AND sink = ?",
            (
                "pr-166-loop-result-run-1",
                EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
            ),
        )

    replay = run_wiki_write_back_effector(
        node_id="publish",
        output_keys=["packet"],
        run_state={"packet": _packet()},
        base_path=tmp_path,
        run_id="run-reconcile",
    )

    assert replay["status"] == STATUS_SUCCEEDED
    assert replay["reconciled"] is True
    assert replay["reconciliation"] == "server_destination_marker_found"


def test_attacker_body_marker_cannot_reconcile_a_victim_effect(tmp_path):
    target = _wiki_env(tmp_path)
    grant_consent(
        tmp_path,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        destination="pages/patch-requests/pr-166-test.md",
        granted_by="tester",
    )
    victim_effect_key = "pr-166-loop-result-run-1"
    try_reserve_receipt(
        tmp_path,
        idempotency_hint=victim_effect_key,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
        run_id="run-victim-crashed",
        now=0.0,
    )
    marker = f"tinyassets-wiki-write-back:{victim_effect_key}"
    target.write_text(
        target.read_text(encoding="utf-8")
        + f"\n<!-- {marker} -->\nattacker controlled\n<!-- /{marker} -->\n",
        encoding="utf-8",
    )

    result = run_wiki_write_back_effector(
        node_id="publish",
        output_keys=["packet"],
        run_state={"packet": _packet()},
        base_path=tmp_path,
        run_id="run-victim-replay",
    )

    assert result["status"] == "held"
    assert result["dry_run"] is True
    assert result["reason"] == "reconciliation_inconclusive"
    receipt = lookup_receipt(
        tmp_path,
        idempotency_hint=victim_effect_key,
        sink=EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
    )
    assert receipt is not None
    assert receipt["status"] != STATUS_SUCCEEDED
    assert "destination_marker_found" not in receipt["evidence"].values()


def test_branch_dispatch_routes_wiki_write_back_sink(tmp_path):
    _wiki_env(tmp_path)
    branch = SimpleNamespace(
        node_defs=[
            NodeDefinition(
                node_id="publish",
                display_name="Publish",
                output_keys=["packet"],
                effects=[EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK],
            ),
        ],
    )

    ev_map = run_effects_for_branch(
        branch=branch,
        run_state={"packet": _packet()},
        base_path=tmp_path,
        run_id="run-dispatch",
    )

    ev = ev_map["publish"][EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK]
    assert ev["reason"] == "missing_consent"
