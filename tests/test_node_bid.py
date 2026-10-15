"""NodeBid executor and paid-market priority weights.

Tests:
- NodeBid dataclass + YAML I/O (6)
- validate_node_bid_inputs (4)
- read_node_bids / claim_node_bid (5)
- NodeBidProducer (7)
- execute_node_bid (6)
- Dispatcher scorer + LLM filter (7)
- submit_node_bid MCP action (5)
- Flag matrix (4)
- Settlement + ledger (3)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
import yaml

from tinyassets.bid.execution_log import (
    append_execution_log_entry as append_ledger_entry,
)
from tinyassets.bid.execution_log import (
    read_execution_log as read_ledger,
)
from tinyassets.bid.node_bid import (
    NodeBid,
    bid_path,
    claim_node_bid,
    new_node_bid_id,
    read_node_bid,
    read_node_bids,
    update_node_bid_status,
    validate_node_bid_inputs,
    write_node_bid_post,
)
from tinyassets.control_plane.queue_policy import (
    DispatcherConfig,
    load_dispatcher_config,
)
from tinyassets.executors.node_bid import execute_node_bid

# ───────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────




@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "branches").mkdir()
    (root / "bids").mkdir()
    monkeypatch.setenv("TINYASSETS_REPO_ROOT", str(root))
    return root


@pytest.fixture
def universe_dir(tmp_path: Path) -> Path:
    udir = tmp_path / "uni-a"
    udir.mkdir()
    return udir


@pytest.fixture
def paid_flag_on(monkeypatch):
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "on")
    return True


@pytest.fixture
def paid_flag_off(monkeypatch):
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "off")
    return True


def _write_bid_yaml(
    repo_root: Path,
    node_bid_id: str,
    *,
    node_def_id: str = "extract_entities",
    required_llm_type: str = "",
    inputs: dict | None = None,
    bid: float = 0.0,
    status: str = "open",
) -> Path:
    path = repo_root / "bids" / f"{node_bid_id}.yaml"
    payload = {
        "node_bid_id": node_bid_id,
        "node_def_id": node_def_id,
        "required_llm_type": required_llm_type,
        "inputs": inputs or {},
        "bid": bid,
        "submitted_by": "alice",
        "status": status,
        "evidence_url": "",
        "submitted_at": "2026-04-14T12:00:00+00:00",
    }
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def _make_approved_node(node_id: str, source_code: str):
    from tinyassets.branches import NodeDefinition
    return NodeDefinition(
        node_id=node_id,
        display_name=node_id,
        source_code=source_code,
    ).mark_approved()


# ───────────────────────────────────────────────────────────────────────
# NodeBid dataclass + YAML I/O
# ───────────────────────────────────────────────────────────────────────


def test_nodebid_dataclass_defaults():
    bid = NodeBid(node_bid_id="nb_1", node_def_id="foo")
    assert bid.status == "open"
    assert bid.bid == 0.0
    assert bid.inputs == {}


def test_nodebid_to_and_from_dict_roundtrip():
    bid = NodeBid(
        node_bid_id="nb_1", node_def_id="foo",
        required_llm_type="claude-opus", inputs={"x": 1}, bid=2.5,
        submitted_by="alice", status="open", submitted_at="2026-04-14",
    )
    restored = NodeBid.from_dict(bid.to_dict())
    assert restored == bid


def test_nodebid_from_dict_ignores_unknown_keys():
    data = {"node_bid_id": "nb_1", "node_def_id": "foo", "extra": "ignored"}
    bid = NodeBid.from_dict(data)
    assert bid.node_bid_id == "nb_1"


def test_write_and_read_node_bid(repo_root):
    path = write_node_bid_post(repo_root, {
        "node_bid_id": "nb_test", "node_def_id": "foo",
        "inputs": {"x": 1}, "bid": 3.0, "status": "open",
    })
    assert path.exists()
    bid = read_node_bid(repo_root, "nb_test")
    assert bid is not None
    assert bid.node_def_id == "foo"
    assert bid.bid == 3.0


def test_read_node_bids_empty_dir(tmp_path):
    empty = tmp_path / "empty_repo"
    empty.mkdir()
    assert read_node_bids(empty) == []


def test_read_node_bids_skips_malformed(repo_root, caplog):
    bad = repo_root / "bids" / "bad.yaml"
    bad.write_text("::: invalid yaml :::", encoding="utf-8")
    _write_bid_yaml(repo_root, "good_nb", node_def_id="foo")
    bids = read_node_bids(repo_root)
    assert [b.node_bid_id for b in bids] == ["good_nb"]


# ───────────────────────────────────────────────────────────────────────
# validate_node_bid_inputs
# ───────────────────────────────────────────────────────────────────────


def test_validate_accepts_flat_primitives():
    ok, reason = validate_node_bid_inputs({"a": "s", "b": 1, "c": 1.5, "d": True, "e": None})
    assert ok, reason


def test_validate_rejects_nested_dict():
    ok, _ = validate_node_bid_inputs({"a": {"nested": 1}})
    assert not ok


def test_validate_rejects_underscore_prefix():
    ok, _ = validate_node_bid_inputs({"_universe_path": "/etc"})
    assert not ok


def test_validate_rejects_list_values():
    ok, _ = validate_node_bid_inputs({"items": [1, 2, 3]})
    assert not ok


# ───────────────────────────────────────────────────────────────────────
# claim_node_bid + status update
# ───────────────────────────────────────────────────────────────────────


def test_claim_node_bid_first_wins(repo_root):
    """Phase G.1: ``claim_node_bid`` returns ``NodeBid | None`` per
    preflight §4.1 #1, not ``bool``. First claim renames the YAML
    to the ``.claimed_by_<daemon>`` suffix; subsequent claims find
    no open bid → return None.
    """
    _write_bid_yaml(repo_root, "nb_claim")
    first = claim_node_bid(repo_root, "nb_claim", "daemon-A")
    assert first is not None
    assert first.status == "claimed:daemon-A"
    second = claim_node_bid(repo_root, "nb_claim", "daemon-B")
    assert second is None


def test_claim_node_bid_records_identity_tuple(repo_root, monkeypatch):
    import tinyassets.bid.node_bid as nb_mod
    from tinyassets.bid.node_bid import bids_dir

    monkeypatch.setattr(nb_mod, "_git_has_remote", lambda _root: False)

    _write_bid_yaml(repo_root, "nb_identity")
    claimed = claim_node_bid(
        repo_root,
        "nb_identity",
        "daemon-A",
        owner_user_id="owner-A",
        runtime_instance_id="runtime-A",
        worker_id="worker-A",
    )
    assert claimed is not None
    assert claimed.status == "claimed:daemon-A"
    assert claimed.owner_user_id == "owner-A"
    assert claimed.daemon_id == "daemon-A"
    assert claimed.runtime_instance_id == "runtime-A"
    assert claimed.worker_id == "worker-A"

    [claimed_path] = [
        p for p in bids_dir(repo_root).iterdir()
        if p.name.startswith("nb_identity.yaml.claimed_by_")
    ]
    data = yaml.safe_load(claimed_path.read_text(encoding="utf-8"))
    assert data["owner_user_id"] == "owner-A"
    assert data["daemon_id"] == "daemon-A"
    assert data["runtime_instance_id"] == "runtime-A"
    assert data["worker_id"] == "worker-A"


def test_claim_missing_bid_returns_none(repo_root):
    assert claim_node_bid(repo_root, "nb_missing", "daemon-A") is None


def test_update_node_bid_status(repo_root):
    _write_bid_yaml(repo_root, "nb_up")
    assert update_node_bid_status(
        repo_root, "nb_up",
        status="succeeded", evidence_url="file:///tmp/out.json",
    ) is True
    bid = read_node_bid(repo_root, "nb_up")
    assert bid.status == "succeeded"
    assert bid.evidence_url == "file:///tmp/out.json"


# ───────────────────────────────────────────────────────────────────────
# NodeBidProducer
# ───────────────────────────────────────────────────────────────────────
















# ───────────────────────────────────────────────────────────────────────
# execute_node_bid
# ───────────────────────────────────────────────────────────────────────


def test_execute_succeeds_writes_artifact(tmp_path):
    node = _make_approved_node(
        "simple",
        "def run(state):\n    return {'echo': state.get('x', 0) * 2}\n",
    )
    bid = NodeBid(
        node_bid_id="nb_exec", node_def_id="simple",
        inputs={"x": 21}, status="open",
    )
    result = execute_node_bid(
        bid, node_lookup_fn=lambda nid: node if nid == "simple" else None,
        output_dir=tmp_path,
    )
    assert result.status == "succeeded"
    assert result.output == {"echo": 42}
    assert result.evidence_url.startswith("file://")
    artifact = tmp_path / "bid_outputs" / "nb_exec" / "output.json"
    assert artifact.exists()
    assert json.loads(artifact.read_text()) == {"echo": 42}


def test_execute_run_sees_top_level_helpers(tmp_path):
    """BUG-112 regression (node_bid exec path): run() must see sibling
    top-level helpers. Split globals/locals raised NameError; a single
    exec namespace restores module-level visibility.
    """
    node = _make_approved_node(
        "with_helper",
        "def _double(n):\n"
        "    return n * 2\n"
        "\n"
        "def run(state):\n"
        "    return {'echo': _double(state.get('x', 0))}\n",
    )
    bid = NodeBid(
        node_bid_id="nb_helper", node_def_id="with_helper",
        inputs={"x": 21}, status="open",
    )
    result = execute_node_bid(
        bid, node_lookup_fn=lambda nid: node if nid == "with_helper" else None,
        output_dir=tmp_path,
    )
    assert result.status == "succeeded"
    assert result.output == {"echo": 42}


def test_execute_unknown_node_fails(tmp_path):
    bid = NodeBid(node_bid_id="nb_nf", node_def_id="missing", status="open")
    result = execute_node_bid(
        bid, node_lookup_fn=lambda _nid: None, output_dir=tmp_path,
    )
    assert result.status == "failed"
    assert "node_not_found" in result.error


def test_execute_unapproved_node_fails(tmp_path):
    from tinyassets.branches import NodeDefinition
    node = NodeDefinition(
        node_id="bad", display_name="bad",
        approved=False, source_code="def run(s): return {}",
    )
    bid = NodeBid(node_bid_id="nb_ua", node_def_id="bad", status="open")
    result = execute_node_bid(
        bid, node_lookup_fn=lambda _nid: node, output_dir=tmp_path,
    )
    assert result.status == "failed"
    assert "unapproved_node" in result.error


def test_execute_dangerous_pattern_fails(tmp_path):
    node = _make_approved_node(
        "mal",
        "import pickle\ndef run(s): return {}\n",
    )
    bid = NodeBid(node_bid_id="nb_mal", node_def_id="mal", status="open")
    result = execute_node_bid(
        bid, node_lookup_fn=lambda _nid: node, output_dir=tmp_path,
    )
    assert result.status == "failed"
    assert "dangerous_pattern" in result.error


def test_execute_strips_internal_input_keys(tmp_path):
    node = _make_approved_node(
        "echo",
        "def run(state):\n    return {'keys': sorted(state.keys())}\n",
    )
    bid = NodeBid(
        node_bid_id="nb_strip", node_def_id="echo",
        inputs={
            "__node_bid_id": "nb_strip",
            "__node_def_id": "echo",
            "user_key": "ok",
        },
        status="open",
    )
    result = execute_node_bid(
        bid, node_lookup_fn=lambda _nid: node, output_dir=tmp_path,
    )
    assert result.status == "succeeded"
    assert result.output == {"keys": ["user_key"]}


def test_execute_node_that_raises_fails(tmp_path):
    node = _make_approved_node(
        "boom",
        "def run(state):\n    raise ValueError('x')\n",
    )
    bid = NodeBid(node_bid_id="nb_boom", node_def_id="boom", status="open")
    result = execute_node_bid(
        bid, node_lookup_fn=lambda _nid: node, output_dir=tmp_path,
    )
    assert result.status == "failed"
    assert "run_error" in result.error


# ───────────────────────────────────────────────────────────────────────
# Dispatcher scoring + LLM-type filter
# ───────────────────────────────────────────────────────────────────────


















# ───────────────────────────────────────────────────────────────────────
# submit_node_bid MCP action
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def mcp_harness(tmp_path, monkeypatch, paid_flag_on):
    base = tmp_path / "output"
    base.mkdir()
    uid = "test-uni"
    (base / uid).mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bids").mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)
    monkeypatch.setenv("TINYASSETS_REPO_ROOT", str(repo))
    return {"base": base, "uid": uid, "repo": repo}












# ───────────────────────────────────────────────────────────────────────
# Flag matrix (registration + config)
# ───────────────────────────────────────────────────────────────────────








def test_load_dispatcher_config_flag_on_defaults_bid_coefficient(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "on")
    u = tmp_path / "u"
    u.mkdir()
    cfg = load_dispatcher_config(u)
    assert cfg.bid_coefficient == 1.0
    assert cfg.accept_paid_bids is True


def test_load_dispatcher_config_flag_off_bid_coefficient_stays_zero(
    tmp_path, monkeypatch,
):
    """Invariant: flag-off means bid_coefficient=0.0 and base scores stay unchanged."""
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "off")
    u = tmp_path / "u"
    u.mkdir()
    cfg = load_dispatcher_config(u)
    assert cfg.bid_coefficient == 0.0
    assert cfg.accept_paid_bids is False


def test_load_dispatcher_config_yaml_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "on")
    u = tmp_path / "u"
    u.mkdir()
    (u / "dispatcher_config.yaml").write_text(
        "bid_coefficient: 0.0\n", encoding="utf-8",
    )
    cfg = load_dispatcher_config(u)
    assert cfg.bid_coefficient == 0.0


def test_tier_status_map_live_when_accept(monkeypatch):
    cfg = DispatcherConfig(accept_paid_bids=True)
    assert cfg.tier_status_map()["paid_bid"] == "live"
    cfg2 = DispatcherConfig(accept_paid_bids=False)
    assert cfg2.tier_status_map()["paid_bid"] == "disabled"


# ───────────────────────────────────────────────────────────────────────
# Settlement + ledger
# ───────────────────────────────────────────────────────────────────────


def test_ledger_append_creates_file(universe_dir):
    entry = {"bid_id": "nb_1", "daemon_id": "d1", "success": True}
    append_ledger_entry(universe_dir, entry)
    entries = read_ledger(universe_dir)
    assert len(entries) == 1
    assert entries[0]["bid_id"] == "nb_1"


def test_ledger_append_accumulates(universe_dir):
    append_ledger_entry(universe_dir, {"bid_id": "nb_1"})
    append_ledger_entry(universe_dir, {"bid_id": "nb_2"})
    append_ledger_entry(universe_dir, {"bid_id": "nb_3"})
    entries = read_ledger(universe_dir)
    assert [e["bid_id"] for e in entries] == ["nb_1", "nb_2", "nb_3"]


def test_ledger_concurrent_appends_dont_lose_entries(universe_dir):
    """File lock should serialize. 10 threads × 5 entries = 50 total."""
    def _worker(n):
        for i in range(5):
            append_ledger_entry(universe_dir, {
                "bid_id": f"nb_{n}_{i}", "daemon_id": f"d{n}",
            })
    threads = [threading.Thread(target=_worker, args=(n,)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    entries = read_ledger(universe_dir)
    assert len(entries) == 50


# ───────────────────────────────────────────────────────────────────────
# new_node_bid_id uniqueness
# ───────────────────────────────────────────────────────────────────────


def test_new_node_bid_id_unique():
    ids = {new_node_bid_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(i.startswith("nb_") for i in ids)


def test_bid_path_shape(tmp_path):
    assert bid_path(tmp_path, "nb_x") == tmp_path / "bids" / "nb_x.yaml"


# Phase G.1 additions — shared fixtures.
_VALID_SOURCE = "def run(state):\n    return {'ok': True}\n"


# ───────────────────────────────────────────────────────────────────────
# Phase G.1 — settlement records (tinyassets/settlements.py)
# ───────────────────────────────────────────────────────────────────────


def test_settlement_emitted_on_succeeded_bid(repo_root):
    from tinyassets.bid.settlements import (
        SCHEMA_VERSION,
        record_settlement_event,
        settlements_dir,
    )
    from tinyassets.executors.node_bid import NodeBidResult

    bid = NodeBid(
        node_bid_id="nb_ok", node_def_id="n/x",
        submitted_by="alice", bid=5.0,
    )
    result = NodeBidResult(
        node_bid_id="nb_ok", status="succeeded",
        evidence_url="file:///tmp/e",
    )
    path = record_settlement_event(repo_root, bid, result, "daemon-1")
    assert path.parent == settlements_dir(repo_root)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["outcome_status"] == "succeeded"
    assert data["settled"] is False
    assert data["bid_amount"] == 5.0
    assert data["requester_id"] == "alice"
    # Preflight §4.1 #5b: v1 schema uses outcome_status, NOT success.
    assert "success" not in data


def test_settlement_records_identity_tuple_without_changing_amount(repo_root):
    from tinyassets.bid.settlements import record_settlement_event
    from tinyassets.executors.node_bid import NodeBidResult

    bid = NodeBid(
        node_bid_id="nb_identity", node_def_id="n/x",
        submitted_by="alice", bid=7.25,
    )
    result = NodeBidResult(
        node_bid_id="nb_identity", status="succeeded",
        evidence_url="file:///tmp/e",
    )
    path = record_settlement_event(
        repo_root,
        bid,
        result,
        "daemon-1",
        owner_user_id="owner-1",
        runtime_instance_id="runtime-1",
        worker_id="worker-1",
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["owner_user_id"] == "owner-1"
    assert data["daemon_id"] == "daemon-1"
    assert data["runtime_instance_id"] == "runtime-1"
    assert data["worker_id"] == "worker-1"
    assert data["bid_amount"] == 7.25
    assert data["outcome_status"] == "succeeded"
    assert data["settled"] is False


def test_settlement_emitted_on_failed_bid(repo_root):
    from tinyassets.bid.settlements import record_settlement_event
    from tinyassets.executors.node_bid import NodeBidResult

    bid = NodeBid(node_bid_id="nb_fail", node_def_id="n/x")
    result = NodeBidResult(
        node_bid_id="nb_fail", status="failed",
        evidence_url="", error="boom",
    )
    path = record_settlement_event(repo_root, bid, result, "daemon-2")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["outcome_status"] == "failed"
    assert data["evidence_url"] == ""
    assert data["settled"] is False


def test_settlement_refuses_overwrite(repo_root):
    """Preflight §4.1 #5b: v1 records are IMMUTABLE. A second call
    with the same (bid_id, daemon_id) raises SettlementExistsError.
    """
    from tinyassets.bid.settlements import (
        SettlementExistsError,
        record_settlement_event,
    )
    from tinyassets.executors.node_bid import NodeBidResult

    bid = NodeBid(node_bid_id="nb_dup", node_def_id="n/x")
    result = NodeBidResult(
        node_bid_id="nb_dup", status="succeeded",
        evidence_url="file:///a",
    )
    record_settlement_event(repo_root, bid, result, "daemon-d")
    with pytest.raises(SettlementExistsError):
        record_settlement_event(repo_root, bid, result, "daemon-d")


def test_settlement_schema_version_locked():
    from tinyassets.bid.settlements import SCHEMA_VERSION
    assert SCHEMA_VERSION == "1"


def test_settlement_rejects_invalid_outcome_status(repo_root):
    from tinyassets.bid.settlements import record_settlement_event
    from tinyassets.executors.node_bid import NodeBidResult

    bid = NodeBid(node_bid_id="nb_bad_outcome", node_def_id="n/x")
    # A result with status="running" shouldn't be able to settle.
    result = NodeBidResult(
        node_bid_id="nb_bad_outcome", status="running",
    )
    with pytest.raises(ValueError, match="outcome_status"):
        record_settlement_event(repo_root, bid, result, "d")


def test_settlement_path_daemon_id_sanitized(tmp_path):
    from tinyassets.bid.settlements import settlement_path
    # daemon_id with slashes must not break the filename.
    path = settlement_path(tmp_path, "nb_1", "daemon/with/slashes")
    # Only the daemon-id suffix part is sanitized; the path itself
    # includes the directory separator on disk.
    assert "/" not in path.name
    assert path.name == "nb_1__daemon_with_slashes.yaml"


# ───────────────────────────────────────────────────────────────────────
# Phase G.1 — claim_node_bid git-rename + push-fail revert (preflight §4.1 #1)
# ───────────────────────────────────────────────────────────────────────


def _init_git(repo_root: Path) -> None:
    import subprocess
    for args in (
        ["git", "init"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(args, cwd=str(repo_root), check=False,
                       capture_output=True)


def test_claim_local_only_renames_yaml(repo_root):
    """Local-only install (no remote): file renames to .claimed_by
    suffix; original YAML gone; returned NodeBid has claimed status."""
    from tinyassets.bid.node_bid import bids_dir
    _init_git(repo_root)
    _write_bid_yaml(repo_root, "nb_rename")
    claimed = claim_node_bid(repo_root, "nb_rename", "daemon-local")
    assert claimed is not None
    assert claimed.status == "claimed:daemon-local"
    assert not bid_path(repo_root, "nb_rename").exists()
    suffix_found = any(
        p.name.startswith("nb_rename.yaml.claimed_by_")
        for p in bids_dir(repo_root).iterdir()
    )
    assert suffix_found


def test_claim_revert_on_push_failure(tmp_path, monkeypatch):
    """Preflight §4.1 #1 step 5: push-fail triggers hard-reset AND
    bid_outputs/<id>/ cleanup.
    """
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bids").mkdir()
    monkeypatch.setenv("TINYASSETS_REPO_ROOT", str(repo))
    _init_git(repo)
    _write_bid_yaml(repo, "nb_push_fail")

    # Pre-populate bid_outputs to assert cleanup fires on revert.
    outputs_dir = repo / "bid_outputs" / "nb_push_fail"
    outputs_dir.mkdir(parents=True)
    (outputs_dir / "leftover.json").write_text("{}", encoding="utf-8")

    import tinyassets.bid.node_bid as nb_mod
    monkeypatch.setattr(nb_mod, "_git_has_remote", lambda _root: True)
    monkeypatch.setattr(nb_mod, "_git_current_branch", lambda _root: "main")

    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if len(args) >= 2 and args[0] == "git" and args[1] == "push":
            class R:
                returncode = 1
                stdout = ""
                stderr = "non-fast-forward"
            return R()
        return real_run(args, **kwargs)

    monkeypatch.setattr(nb_mod.subprocess, "run", fake_run)

    claimed = claim_node_bid(repo, "nb_push_fail", "daemon-revert")
    assert claimed is None
    # bid_outputs/<id>/ cleaned on revert.
    assert not outputs_dir.exists()


def test_claim_returns_none_when_already_claimed(repo_root):
    """Bid with non-open status → claim returns None without rename."""
    _init_git(repo_root)
    _write_bid_yaml(
        repo_root, "nb_pre_claimed", status="claimed:other",
    )
    result = claim_node_bid(repo_root, "nb_pre_claimed", "daemon-self")
    assert result is None
    # Original YAML untouched (status still claimed:other).
    from tinyassets.bid.node_bid import read_node_bid
    bid = read_node_bid(repo_root, "nb_pre_claimed")
    assert bid is not None
    assert bid.status == "claimed:other"


# ───────────────────────────────────────────────────────────────────────
# Phase G.1 — producer-side sandbox (invariant 1 both-boundaries)
# ───────────────────────────────────────────────────────────────────────








def test_bid_dangerous_patterns_strict_superset_of_wrapper():
    """Preflight §4.1 #5d + invariant 1: the bid list is a strict
    superset of the wrapper list, and both live at a single source
    of truth in tinyassets.graph_compiler.
    """
    from tinyassets.graph_compiler import (
        _BID_DANGEROUS_PATTERNS,
        _DANGEROUS_PATTERNS,
    )
    assert set(_BID_DANGEROUS_PATTERNS) > set(_DANGEROUS_PATTERNS)
    for added in ("compile(", "open(", "importlib", "pickle", "marshal"):
        assert added in _BID_DANGEROUS_PATTERNS


def test_bid_dangerous_patterns_excludes_network_patterns():
    """Network-call patterns intentionally NOT in the bid list —
    approved nodes may legitimately call LLM APIs via urllib/requests.
    """
    from tinyassets.graph_compiler import _BID_DANGEROUS_PATTERNS
    for network in ("urllib", "requests", "socket", "http.client"):
        assert network not in _BID_DANGEROUS_PATTERNS


# ───────────────────────────────────────────────────────────────────────
# Phase G.2 — call-site fallback race-bypass guard
# ───────────────────────────────────────────────────────────────────────


def _init_repo_with_remote(repo_root: Path) -> None:
    """Init a git repo at ``repo_root`` with a fake remote configured
    so ``git_has_remote`` returns True. The remote URL doesn't need
    to be reachable — we only probe whether one exists.
    """
    import subprocess
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo_root, check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", "https://example.invalid/x.git"],
        cwd=repo_root, check=True, capture_output=True,
    )






