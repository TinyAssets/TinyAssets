"""#58 — Raw run_id / branch_def_id / goal_id must never appear in the
text channel of MCP tool returns. Phone users read `text` verbatim
through Claude.ai; IDs belong in structuredContent for scripts.

Covers the audit surface documented in the task:
run_branch, get_run, list_runs, build_branch, patch_branch,
rollback_node, create_branch, get_branch, list_branches, goals.propose,
goals.bind, plus get_run_output, judge_run, and get_node_output.
"""

from __future__ import annotations

import importlib
import json
import time

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401

#: Runs are attributed to this universe; registered + ACL-granted in `env`.
REDACTION_UNIVERSE = "redaction-universe"

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def env(tmp_path, monkeypatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    monkeypatch.setenv("_FORCE_MOCK", "true")
    # Branch mutation requires a credential-derived subject, and the scope
    # check is per-family: this file drives `goals` as well as `extensions`.
    # Nothing here asserts a *scope* refusal, so granting the writes these
    # tests perform costs no assertion strength. `extensions.costly` and
    # `goals.costly` stay withheld.
    authenticate_request("tester", capabilities=[
        "tinyassets.extensions.read",
        "tinyassets.extensions.write",
        "tinyassets.extensions.admin",
        # `extensions.costly` IS needed here: this file exists to prove run
        # IDs are redacted from the text channel, so it has to actually
        # execute `run_branch`. Nothing in it asserts a scope refusal.
        "tinyassets.extensions.costly",
        "tinyassets.goals.read",
        "tinyassets.goals.write",
    ])

    # Runs are owned by a universe now: without `universe_id` run_branch
    # returns `branch_run_requires_universe`, and with an unregistered id it
    # returns `universe_access_denied` — the ACL grant is the half that is
    # easy to miss.
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        grant_universe_access,
    )

    udir = base / REDACTION_UNIVERSE
    udir.mkdir(parents=True, exist_ok=True)
    ensure_universe_registered(
        base, universe_id=REDACTION_UNIVERSE, universe_path=udir,
    )
    grant_universe_access(
        base,
        universe_id=REDACTION_UNIVERSE,
        actor_id="tester",
        permission="write",
        granted_by="env",
    )
    from tinyassets import universe_server as us
    provider_calls = importlib.import_module("tinyassets.providers.call")

    importlib.reload(us)
    monkeypatch.setattr(
        provider_calls,
        "call_provider",
        lambda prompt, _system="", **_kwargs: f"fixture:{prompt}",
    )
    yield us, base
    importlib.reload(us)


def _call(us, tool, action, **kwargs):
    fn = getattr(us, f"_{tool}_impl")
    return json.loads(fn(action=action, **kwargs))


def _build_min_branch(us, name="id redaction fixture"):
    """One-node branch built through ``build_branch`` (the live create path)."""
    spec = {
        "name": name,
        "entry_point": "capture",
        "node_defs": [{
            "node_id": "capture", "display_name": "Capture",
            "prompt_template": "Echo: {raw}", "output_keys": ["capture_output"],
        }],
        "edges": [
            {"from": "START", "to": "capture"},
            {"from": "capture", "to": "END"},
        ],
        "state_schema": [
            {"name": "raw", "type": "str"},
            {"name": "capture_output", "type": "str"},
        ],
    }
    built = _call(us, "extensions", "build_branch", spec_json=json.dumps(spec))
    assert built["status"] == "built", built
    return built["branch_def_id"]


def _run_and_wait(us, *, branch_def_id, inputs_json, timeout_s=10.0):
    queued = _call(us, "extensions", "run_branch", universe_id=REDACTION_UNIVERSE,
                   branch_def_id=branch_def_id,
                   inputs_json=inputs_json)
    rid = queued["run_id"]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        snap = _call(us, "extensions", "get_run", run_id=rid)
        if snap.get("status") in {"completed", "failed", "cancelled"}:
            return snap
        time.sleep(0.05)
    raise TimeoutError(f"Run {rid} did not terminate in {timeout_s}s")


# ─── build/patch/run/get text-channel invariants ──────────────────────────


def test_build_branch_text_hides_branch_def_id(env):
    us, _ = env
    spec = {
        "name": "phone-friendly",
        "entry_point": "n1",
        "state_schema": [{"name": "x", "type": "str", "default": ""}],
        "node_defs": [{
            "node_id": "n1",
            "display_name": "First node",
            "phase": "custom",
            "prompt_template": "hi",
            "input_keys": [],
            "output_keys": ["x"],
        }],
        "edges": [
            {"from_node": "START", "to_node": "n1"},
            {"from_node": "n1", "to_node": "END"},
        ],
    }
    result = _call(us, "extensions", "build_branch",
                   spec_json=json.dumps(spec))
    assert result["status"] == "built"
    bid = result["branch_def_id"]
    assert bid
    assert bid not in result["text"]
    assert "phone-friendly" in result["text"]


def test_patch_branch_text_hides_branch_def_id(env):
    us, _ = env
    bid = _build_min_branch(us, name="patch fixture")
    # Update fields go FLAT on the op, not nested under "updates" — the
    # nested form makes `updates` itself read as one unknown field and the
    # patch is rejected. Every other test in the suite uses the flat shape
    # (see test_composite_branch_actions.py); this one had drifted.
    ops = [{
        "op": "update_node",
        "node_id": "capture",
        "display_name": "Capture (renamed)",
    }]
    result = _call(us, "extensions", "patch_branch",
                   branch_def_id=bid,
                   changes_json=json.dumps(ops))
    assert result["status"] == "patched"
    assert bid not in result["text"]
    # content_hash alone is still the version discriminator that mints
    # branch_version_id -- an id, not human content, even though it does
    # not itself embed branch_def_id (Codex ADAPT round on 256efe7b). The
    # leak was only the 8-char PREFIX (`branch_version_id`'s own display
    # convention), so check the prefix specifically -- checking containment
    # of the full 64-char hash would pass even with the prefix still leaking.
    assert result["content_hash"][:8] not in result["text"]
    assert result["branch_version_id"] not in result["text"]
    assert "patch fixture" in result["text"]


def test_patch_branch_rejected_set_fork_from_hides_the_existing_id(env):
    """The rejected path leaks too, not just the successful one.

    `set_fork_from` is immutable once set: a second attempt is rejected with
    an op error naming the value ALREADY on the branch. That error string is
    copied verbatim into the rejection `text`'s "Op errors:" section, so it
    must not carry the existing branch_version_id either.
    """
    us, _ = env
    source_bid = _build_min_branch(us, name="fork source")
    published = _call(
        us, "extensions", "patch_branch", branch_def_id=source_bid,
        changes_json=json.dumps([{
            "op": "update_node", "node_id": "capture",
            "display_name": "Capture v2",
        }]),
    )
    fork_bvid = published["branch_version_id"]

    target_bid = _build_min_branch(us, name="fork target")
    first_set = _call(
        us, "extensions", "patch_branch", branch_def_id=target_bid,
        changes_json=json.dumps([
            {"op": "set_fork_from", "branch_version_id": fork_bvid},
        ]),
    )
    assert first_set["status"] == "patched", first_set

    rejected = _call(
        us, "extensions", "patch_branch", branch_def_id=target_bid,
        changes_json=json.dumps([
            {"op": "set_fork_from", "branch_version_id": fork_bvid},
        ]),
    )
    assert rejected["status"] == "rejected", rejected
    assert fork_bvid not in rejected["text"]
    assert source_bid not in rejected["text"]
    assert target_bid not in rejected["text"]


def test_run_branch_text_hides_run_id(env):
    us, _ = env
    bid = _build_min_branch(us)
    result = _call(us, "extensions", "run_branch", universe_id=REDACTION_UNIVERSE,
                   branch_def_id=bid,
                   inputs_json=json.dumps({"raw": "abc"}))
    assert "run_id" in result  # structuredContent still carries it
    assert result["run_id"]
    assert result["run_id"] not in result["text"]


def test_get_run_text_hides_run_id_and_branch_def_id(env):
    us, _ = env
    bid = _build_min_branch(us, name="snapshot fixture")
    run = _run_and_wait(us, branch_def_id=bid,
                       inputs_json=json.dumps({"raw": "xyz"}))
    rid = run["run_id"]
    snap = _call(us, "extensions", "get_run", run_id=rid)
    assert rid not in snap["text"]
    assert bid not in snap["text"]
    # Branch name (not ID) surfaces.
    assert "snapshot fixture" in snap["text"]


def test_get_run_output_text_hides_run_id(env):
    us, _ = env
    bid = _build_min_branch(us, name="output fixture")
    run = _run_and_wait(us, branch_def_id=bid,
                       inputs_json=json.dumps({"raw": "read me"}))
    rid = run["run_id"]
    full = _call(us, "extensions", "get_run_output", run_id=rid)
    assert rid not in full["text"]
    single = _call(us, "extensions", "get_run_output",
                   run_id=rid, field_name="capture_output")
    assert rid not in single["text"]


# ─── goals ────────────────────────────────────────────────────────────────


def test_goal_propose_text_hides_goal_id(env):
    us, _ = env
    result = _call(us, "goals", "propose",
                   name="Paper: long-horizon eval",
                   description="Phase 6 candidate Goal.")
    assert result["status"] == "proposed"
    gid = result["goal"]["goal_id"]
    assert gid
    assert gid not in result["text"]
    assert "Paper: long-horizon eval" in result["text"]


def test_goal_set_canonical_hides_branch_version_id(env):
    us, _ = env
    goal = _call(us, "goals", "propose", name="Canonical Goal")
    gid = goal["goal"]["goal_id"]
    bid = _build_min_branch(us, name="canonical fixture")
    published = _call(
        us, "extensions", "patch_branch", branch_def_id=bid,
        changes_json=json.dumps([{
            "op": "update_node", "node_id": "capture",
            "display_name": "Capture v2",
        }]),
    )
    bvid = published["branch_version_id"]

    result = _call(us, "goals", "set_canonical",
                    goal_id=gid, branch_version_id=bvid)
    assert result["status"] == "ok", result
    assert bvid not in result["text"]
    assert bid not in result["text"]
    assert "Canonical Goal" in result["text"]

    # Personal-scope branch (`scope=<actor>`) has its own `text` string --
    # cover it too, not just the default/global-scope one above.
    personal = _call(us, "goals", "set_canonical",
                      goal_id=gid, branch_version_id=bvid, scope="tester")
    assert personal["status"] == "ok", personal
    assert bvid not in personal["text"]
    assert bid not in personal["text"]


def test_build_branch_invalid_fork_from_hides_the_id(env):
    """The staging-time fork_from check hides its input, same as the pre-flight one.

    ``build_branch`` validates ``spec["fork_from"]`` twice: an early, lenient
    check (``fork_selector = spec.get("fork_from").strip()``) that resolves
    the version and inherits the parent's content, and a later, strict one
    inside the staged branch (``branch.fork_from``, set from the UNSTRIPPED
    raw value) that confirms it is a real ``branch_version_id`` via an exact
    string match. A value that trims to a real version_id -- e.g. one with
    incidental leading/trailing whitespace -- passes the first (stripped)
    check and fails the second (unstripped), so its error text used to leak
    the raw branch_version_id it had just proven exists.
    """
    us, _ = env
    source_bid = _build_min_branch(us, name="fork source")
    published = _call(
        us, "extensions", "patch_branch", branch_def_id=source_bid,
        changes_json=json.dumps([{
            "op": "update_node", "node_id": "capture",
            "display_name": "Capture v2",
        }]),
    )
    bvid = published["branch_version_id"]
    spec = {
        "name": "fork target",
        "fork_from": f" {bvid} ",  # trims to a real version_id
        "entry_point": "n1",
        "state_schema": [{"name": "x", "type": "str", "default": ""}],
        "node_defs": [{
            "node_id": "n1", "display_name": "n1", "phase": "custom",
            "prompt_template": "hi", "input_keys": [], "output_keys": ["x"],
        }],
        "edges": [
            {"from_node": "START", "to_node": "n1"},
            {"from_node": "n1", "to_node": "END"},
        ],
    }
    result = _call(us, "extensions", "build_branch", spec_json=json.dumps(spec))
    assert result["status"] == "rejected", result
    assert bvid not in result["text"]
    assert source_bid not in result["text"]


def test_build_branch_missing_node_ref_hides_source_branch_id(env):
    """A ``node_ref`` naming a real branch but a missing node used to leak it.

    ``_resolve_node_spec`` normalizes ``node_ref.source`` to the real,
    resolved ``branch_def_id`` before looking the node up
    (``_lookup_node_body``); when the referenced node does not exist there,
    the "not found" error used to embed that resolved branch id. It flows
    into ``build_branch``'s rejection `text` via `staging_errors`.
    """
    us, _ = env
    source_bid = _build_min_branch(us, name="node ref source")
    spec = {
        "name": "node ref target",
        "entry_point": "n1",
        "state_schema": [{"name": "x", "type": "str", "default": ""}],
        "node_defs": [{
            "node_id": "n1",
            "node_ref": {"source": source_bid, "node_id": "does-not-exist"},
        }],
        "edges": [
            {"from_node": "START", "to_node": "n1"},
            {"from_node": "n1", "to_node": "END"},
        ],
    }
    result = _call(us, "extensions", "build_branch", spec_json=json.dumps(spec))
    assert result["status"] == "rejected", result
    assert source_bid not in result["text"]


def test_patch_branch_add_node_missing_node_ref_hides_source_branch_id(env):
    """Same containment guard on the patch_branch(add_node) op path.

    ``_apply_patch_op``'s ``add_node`` case delegates straight to
    ``_apply_node_spec``/``_lookup_node_body``, so a missing referenced node
    on a real source branch used to leak that branch's id into the
    rejection `text`'s "Op errors:" section too.
    """
    us, _ = env
    source_bid = _build_min_branch(us, name="node ref source 2")
    target_bid = _build_min_branch(us, name="node ref target 2")
    ops = [{
        "op": "add_node",
        "node_id": "n2",
        "node_ref": {"source": source_bid, "node_id": "does-not-exist"},
    }]
    result = _call(us, "extensions", "patch_branch",
                   branch_def_id=target_bid,
                   changes_json=json.dumps(ops))
    assert result["status"] == "rejected", result
    assert source_bid not in result["text"]
    assert target_bid not in result["text"]
