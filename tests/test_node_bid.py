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
from tinyassets.branch_tasks import BranchTask
from tinyassets.control_plane.queue_policy import (
    DispatcherConfig,
    load_dispatcher_config,
    score_task,
    select_next_task,
)
from tinyassets.control_plane.queue_policy import (
    paid_market_enabled as dispatcher_paid_market_enabled,
)
from tinyassets.executors.node_bid import execute_node_bid
from tinyassets.producers import branch_task as bt_producer_mod
from tinyassets.producers.branch_task import (
    reset_branch_task_registry,
)
from tinyassets.producers.node_bid import (
    NODE_BID_SENTINEL_PREFIX,
    NodeBidProducer,
    paid_market_enabled,
    register_if_enabled,
)

# ───────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = list(bt_producer_mod._REGISTRY)
    reset_branch_task_registry()
    yield
    reset_branch_task_registry()
    bt_producer_mod._REGISTRY.extend(saved)


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


def test_producer_emits_open_bids(repo_root, universe_dir):
    _write_bid_yaml(repo_root, "nb_1", node_def_id="foo", bid=2.5)
    _write_bid_yaml(repo_root, "nb_2", node_def_id="bar", bid=1.0)
    producer = NodeBidProducer()
    tasks = producer.produce(universe_dir, subscribed_goals=[])
    assert len(tasks) == 2
    for t in tasks:
        assert t.branch_def_id.startswith(NODE_BID_SENTINEL_PREFIX)
        assert t.trigger_source == "paid_bid"
        assert t.bid > 0


def test_producer_skips_non_open_status(repo_root, universe_dir):
    _write_bid_yaml(repo_root, "nb_open", node_def_id="foo")
    _write_bid_yaml(repo_root, "nb_done", node_def_id="bar", status="succeeded")
    _write_bid_yaml(repo_root, "nb_claimed", node_def_id="baz", status="claimed:d1")
    producer = NodeBidProducer()
    tasks = producer.produce(universe_dir, subscribed_goals=[])
    assert [t.inputs["__node_bid_id"] for t in tasks] == ["nb_open"]


def test_producer_filters_llm_type_mismatch(repo_root, universe_dir):
    _write_bid_yaml(repo_root, "nb_opus", node_def_id="f", required_llm_type="claude-opus")
    _write_bid_yaml(repo_root, "nb_sonnet", node_def_id="f", required_llm_type="claude-sonnet")
    _write_bid_yaml(repo_root, "nb_any", node_def_id="f", required_llm_type="")
    producer = NodeBidProducer()
    tasks = producer.produce(
        universe_dir, subscribed_goals=[],
        config={"served_llm_type": "claude-opus"},
    )
    ids = sorted(t.inputs["__node_bid_id"] for t in tasks)
    assert ids == ["nb_any", "nb_opus"]


def test_producer_empty_served_llm_accepts_all(repo_root, universe_dir):
    _write_bid_yaml(repo_root, "nb_opus", node_def_id="f", required_llm_type="claude-opus")
    _write_bid_yaml(repo_root, "nb_sonnet", node_def_id="f", required_llm_type="claude-sonnet")
    producer = NodeBidProducer()
    tasks = producer.produce(universe_dir, subscribed_goals=[])
    assert len(tasks) == 2


def test_producer_skips_invalid_inputs(repo_root, universe_dir):
    _write_bid_yaml(
        repo_root, "nb_bad", node_def_id="f",
        inputs={"_universe_path": "/etc"},
    )
    _write_bid_yaml(repo_root, "nb_ok", node_def_id="f", inputs={"x": 1})
    producer = NodeBidProducer()
    tasks = producer.produce(universe_dir, subscribed_goals=[])
    assert [t.inputs["__node_bid_id"] for t in tasks] == ["nb_ok"]


def test_producer_stamps_internal_input_keys(repo_root, universe_dir):
    _write_bid_yaml(
        repo_root, "nb_stamp", node_def_id="foo", inputs={"x": 1},
    )
    producer = NodeBidProducer()
    tasks = producer.produce(universe_dir, subscribed_goals=[])
    assert tasks[0].inputs["__node_bid_id"] == "nb_stamp"
    assert tasks[0].inputs["__node_def_id"] == "foo"
    assert tasks[0].inputs["x"] == 1


def test_producer_mtime_cache(repo_root, universe_dir):
    _write_bid_yaml(repo_root, "nb_c", node_def_id="foo")
    producer = NodeBidProducer()
    first = producer.produce(universe_dir, subscribed_goals=[])
    # Second call with no mtime change should hit cache.
    second = producer.produce(universe_dir, subscribed_goals=[])
    assert [t.branch_task_id for t in first] == [t.branch_task_id for t in second]


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


def _mk_task(**kw):
    defaults = dict(
        branch_task_id=kw.pop("tid", "t1"),
        branch_def_id="foo",
        universe_id="uni",
        trigger_source="paid_bid",
        status="pending",
        queued_at="2026-04-14T12:00:00+00:00",
    )
    defaults.update(kw)
    return BranchTask(**defaults)


def test_score_bid_zero_when_coefficient_zero():
    task = _mk_task(bid=100.0)
    cfg = DispatcherConfig(bid_coefficient=0.0)
    s1 = score_task(task, now_iso="2026-04-14T12:00:00+00:00", config=cfg)
    task0 = _mk_task(bid=0.0)
    s0 = score_task(task0, now_iso="2026-04-14T12:00:00+00:00", config=cfg)
    assert s1 == s0


def test_score_bid_scales_with_coefficient():
    cfg = DispatcherConfig(bid_coefficient=1.0, bid_term_cap=1000.0)
    t1 = _mk_task(tid="a", bid=1.0)
    t10 = _mk_task(tid="b", bid=10.0)
    now = "2026-04-14T12:00:00+00:00"
    assert (
        score_task(t10, now_iso=now, config=cfg)
        > score_task(t1, now_iso=now, config=cfg)
    )


def test_bid_term_cap_enforced():
    cfg = DispatcherConfig(bid_coefficient=1.0, bid_term_cap=5.0)
    t_big = _mk_task(bid=10000.0)
    t_cap = _mk_task(bid=5.0)
    now = "2026-04-14T12:00:00+00:00"
    s_big = score_task(t_big, now_iso=now, config=cfg)
    s_cap = score_task(t_cap, now_iso=now, config=cfg)
    assert s_big == s_cap


def test_host_request_beats_max_bid_paid(tmp_path):
    from tinyassets.branch_tasks import append_task
    u = tmp_path / "u"
    u.mkdir()
    append_task(u, _mk_task(
        tid="host1", trigger_source="host_request", bid=0.0,
    ))
    append_task(u, _mk_task(
        tid="paid1", trigger_source="paid_bid", bid=99999.0,
    ))
    cfg = DispatcherConfig(
        accept_paid_bids=True, bid_coefficient=1.0, bid_term_cap=30.0,
    )
    picked = select_next_task(u, config=cfg)
    assert picked.branch_task_id == "host1"


def test_llm_type_filter_empty_served_passes_all(tmp_path):
    from tinyassets.branch_tasks import append_task
    u = tmp_path / "u"
    u.mkdir()
    append_task(u, _mk_task(
        tid="t1", trigger_source="paid_bid", required_llm_type="claude-opus",
    ))
    cfg = DispatcherConfig(accept_paid_bids=True, served_llm_type="")
    assert select_next_task(u, config=cfg) is not None


def test_llm_type_filter_matching_passes(tmp_path):
    from tinyassets.branch_tasks import append_task
    u = tmp_path / "u"
    u.mkdir()
    append_task(u, _mk_task(
        tid="t1", trigger_source="paid_bid", required_llm_type="claude-opus",
    ))
    cfg = DispatcherConfig(accept_paid_bids=True, served_llm_type="claude-opus")
    assert select_next_task(u, config=cfg) is not None


def test_llm_type_filter_mismatch_skipped(tmp_path):
    from tinyassets.branch_tasks import append_task
    u = tmp_path / "u"
    u.mkdir()
    append_task(u, _mk_task(
        tid="t1", trigger_source="paid_bid", required_llm_type="claude-opus",
    ))
    cfg = DispatcherConfig(accept_paid_bids=True, served_llm_type="claude-sonnet")
    assert select_next_task(u, config=cfg) is None


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


def test_submit_node_bid_flag_off_returns_not_available(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "off")
    base = tmp_path / "output"
    base.mkdir()
    uid = "test-uni"
    (base / uid).mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", uid)

    from tinyassets.api.universe import _action_submit_node_bid
    resp = json.loads(_action_submit_node_bid(
        universe_id=uid, node_def_id="foo", inputs_json="{}", bid=1.0,
    ))
    assert resp["status"] == "not_available"


def test_submit_node_bid_valid_writes_yaml(mcp_harness):
    from tinyassets.api.universe import _action_submit_node_bid
    resp = json.loads(_action_submit_node_bid(
        universe_id=mcp_harness["uid"],
        node_def_id="extract_entities",
        inputs_json=json.dumps({"text": "hi"}),
        bid=2.5,
    ))
    assert resp["status"] == "posted"
    assert Path(resp["path"]).exists()
    bid_id = resp["node_bid_id"]
    data = yaml.safe_load((mcp_harness["repo"] / "bids" / f"{bid_id}.yaml").read_text())
    assert data["node_def_id"] == "extract_entities"
    assert data["bid"] == 2.5
    assert data["status"] == "open"


def test_submit_node_bid_response_has_git_push_hint(mcp_harness):
    from tinyassets.api.universe import _action_submit_node_bid
    resp = json.loads(_action_submit_node_bid(
        universe_id=mcp_harness["uid"],
        node_def_id="extract_entities",
        inputs_json="{}",
        bid=1.0,
    ))
    assert "git add" in resp["next_step"]
    assert "git push" in resp["next_step"]


def test_submit_node_bid_rejects_nested_inputs(mcp_harness):
    from tinyassets.api.universe import _action_submit_node_bid
    resp = json.loads(_action_submit_node_bid(
        universe_id=mcp_harness["uid"],
        node_def_id="f",
        inputs_json=json.dumps({"nested": {"bad": 1}}),
        bid=1.0,
    ))
    assert resp["status"] == "rejected"
    assert "invalid_inputs" in resp["error"]


def test_submit_node_bid_rejects_negative_bid(mcp_harness):
    from tinyassets.api.universe import _action_submit_node_bid
    resp = json.loads(_action_submit_node_bid(
        universe_id=mcp_harness["uid"],
        node_def_id="f",
        inputs_json="{}",
        bid=-1.0,
    ))
    assert resp["status"] == "rejected"


# ───────────────────────────────────────────────────────────────────────
# Flag matrix (registration + config)
# ───────────────────────────────────────────────────────────────────────


def test_flag_off_producer_not_registered(monkeypatch):
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "off")
    reset_branch_task_registry()
    registered = register_if_enabled()
    assert registered is False
    names = [p.name for p in bt_producer_mod._REGISTRY]
    assert "node_bid" not in names


def test_flag_on_producer_registered(monkeypatch):
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "on")
    reset_branch_task_registry()
    registered = register_if_enabled()
    assert registered is True
    names = [p.name for p in bt_producer_mod._REGISTRY]
    assert "node_bid" in names


def test_dispatcher_paid_market_enabled_reads_env(monkeypatch):
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "on")
    assert dispatcher_paid_market_enabled() is True
    assert paid_market_enabled() is True
    monkeypatch.setenv("TINYASSETS_PAID_MARKET", "off")
    assert dispatcher_paid_market_enabled() is False
    assert paid_market_enabled() is False


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


def test_producer_rejects_unapproved_node(repo_root):
    """Layer 1 at the producer boundary: unapproved node skipped
    before BranchTask emission.
    """
    from tinyassets.producers.node_bid import NodeBidProducer

    _write_bid_yaml(repo_root, "nb_unapproved")

    class _UnapprovedNode:
        approved = False
        source_code = _VALID_SOURCE

    prod = NodeBidProducer(
        node_lookup_fn=lambda _slug: _UnapprovedNode(),
    )
    out = prod.produce(
        repo_root / "uni", subscribed_goals=[],
    )
    assert out == []


def test_producer_rejects_dangerous_source_pattern(repo_root):
    """Layer 2 at the producer boundary: dangerous source pattern
    skipped before BranchTask emission.
    """
    from tinyassets.producers.node_bid import NodeBidProducer

    _write_bid_yaml(repo_root, "nb_dangerous_src")

    class _EvilApprovedNode:
        approved = True
        source_code = "import pickle\ndef run(s): return {}\n"

    prod = NodeBidProducer(
        node_lookup_fn=lambda _slug: _EvilApprovedNode(),
    )
    out = prod.produce(
        repo_root / "uni", subscribed_goals=[],
    )
    assert out == []


def test_producer_accepts_approved_clean_node(repo_root):
    """Happy path: layer 1+2 pass → bid enters the queue."""
    from tinyassets.producers.node_bid import NodeBidProducer

    _write_bid_yaml(repo_root, "nb_clean")

    class _CleanNode:
        approved = True
        source_code = _VALID_SOURCE

    prod = NodeBidProducer(
        node_lookup_fn=lambda _slug: _CleanNode(),
    )
    out = prod.produce(
        repo_root / "uni", subscribed_goals=[],
    )
    assert len(out) == 1


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


def test_race_bypass_rejected_when_remote_configured(
    repo_root, universe_dir, monkeypatch,
):
    """Phase G.2: multi-daemon race — A wins the push, B loses. B's
    local `git reset --hard origin/<branch>` restores a stale view
    where B's local clone of the bid YAML still says `status=open`
    (A's claim hasn't propagated to B's local tracking ref yet).

    Pre-fix, B's fallback read-only path would see `existing.status ==
    "open"` and execute anyway, double-spending the bid. Post-fix:
    the fallback is gated on `git_has_remote == False`; in prod
    repos (remote configured), B returns `(False, "claim_race_lost")`
    instead.

    Simulated by: configure a remote, write a YAML that still reads
    `open` locally, monkeypatch `claim_node_bid` to return None
    (simulates race loss). The function under test MUST return
    `claim_race_lost` — not execute.
    """
    _init_repo_with_remote(repo_root)
    _write_bid_yaml(repo_root, "nb_race_bypass", status="open")

    # Re-import the fantasy_author module with the monkeypatched
    # claim_node_bid. We patch at `tinyassets.bid.node_bid` so the
    # function-local import in _try_execute_claimed_node_bid picks
    # up the patched callable.
    import tinyassets.bid.node_bid as nb_mod

    def _patched_claim(repo, bid_id, daemon_id, **_kwargs):
        return None  # always "race lost"
    monkeypatch.setattr(nb_mod, "claim_node_bid", _patched_claim)

    from fantasy_daemon.__main__ import _try_execute_claimed_node_bid
    from tinyassets.branch_tasks import BranchTask

    task = BranchTask(
        branch_task_id="t_race_1",
        branch_def_id="universe-cycle",
        universe_id="uni-a",
        inputs={
            "__node_bid_id": "nb_race_bypass",
            "__node_def_id": "extract_entities",
        },
    )
    success, error = _try_execute_claimed_node_bid(
        universe_dir, task, daemon_id="daemon-b",
    )
    assert success is False
    assert error == "claim_race_lost"


def test_fallback_still_works_without_remote(
    repo_root, universe_dir, monkeypatch,
):
    """Phase G.2: without a remote, the read-only fallback is still
    legitimate — single-process, no race is physically possible.
    Local test harnesses that pre-populate the bid YAML outside the
    claim flow keep working.

    To assert the fallback fires we'd need a full executor run, which
    is tested elsewhere. Here we just confirm the function does NOT
    short-circuit on `claim_race_lost` when the claim returns None
    AND the YAML is still `open` AND no remote is configured. The
    legitimate fallback path proceeds past the race-gate; any
    subsequent error (node_lookup_error, execution failure) is a
    different failure class that proves the fallback took the
    non-race-lost branch.
    """
    import subprocess
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=repo_root, check=True,
        capture_output=True,
    )
    # NO `git remote add` — single-daemon local repo.
    _write_bid_yaml(repo_root, "nb_local_fb", status="open")

    import tinyassets.bid.node_bid as nb_mod

    def _patched_claim(repo, bid_id, daemon_id):
        return None
    monkeypatch.setattr(nb_mod, "claim_node_bid", _patched_claim)

    from fantasy_daemon.__main__ import _try_execute_claimed_node_bid
    from tinyassets.branch_tasks import BranchTask

    task = BranchTask(
        branch_task_id="t_local_1",
        branch_def_id="universe-cycle",
        universe_id="uni-a",
        inputs={
            "__node_bid_id": "nb_local_fb",
            "__node_def_id": "nonexistent_node",
        },
    )
    success, error = _try_execute_claimed_node_bid(
        universe_dir, task, daemon_id="daemon-local",
    )
    # Execution will fail (unknown node), but NOT with claim_race_lost
    # — that means the fallback path took the non-race-gated branch.
    assert error != "claim_race_lost"


def test_fallback_rejected_when_remote_and_yaml_missing(
    repo_root, universe_dir, monkeypatch,
):
    """Edge case: remote configured, YAML missing — the fallback's
    ``existing is None`` check would also return ``claim_race_lost``,
    but this test pins that outcome independently of the remote-gate
    so a future refactor doesn't collapse them.
    """
    _init_repo_with_remote(repo_root)
    # Note: NO call to _write_bid_yaml — the file is missing.

    import tinyassets.bid.node_bid as nb_mod
    monkeypatch.setattr(
        nb_mod, "claim_node_bid", lambda r, b, d, **_kwargs: None,
    )

    from fantasy_daemon.__main__ import _try_execute_claimed_node_bid
    from tinyassets.branch_tasks import BranchTask

    task = BranchTask(
        branch_task_id="t_missing_1",
        branch_def_id="universe-cycle",
        universe_id="uni-a",
        inputs={
            "__node_bid_id": "nb_does_not_exist",
            "__node_def_id": "extract_entities",
        },
    )
    success, error = _try_execute_claimed_node_bid(
        universe_dir, task, daemon_id="daemon-c",
    )
    assert success is False
    assert error == "claim_race_lost"
