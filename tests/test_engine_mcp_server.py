"""Tests for the founder-scoped engine MCP server + its wiring.

Security-critical: this is the P0 engine-sandbox surface. These lock in the
confinement the 2026-08-13 Codex review required for the read-only slice —
verified-principal identity, hard fail-closed, the graph pin, and the
restricted read_graph target set — so a regression turns a gate red instead of
silently re-opening a cross-universe read.
"""
from __future__ import annotations

import json

import pytest

from tests.engine_authority_helpers import mock_engine_admission

# ── engine_mcp_server: fail-closed + confinement ────────────────────────────

def test_binding_error_fails_closed_without_both_ids(monkeypatch):
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(s, "_ACTOR_ID", "")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-x")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    assert s._binding_error() is not None

    monkeypatch.setattr(s, "_ACTOR_ID", "sub-1")
    monkeypatch.setattr(s, "_GRAPH_ID", "")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    assert s._binding_error() is not None

    monkeypatch.setattr(s, "_GRAPH_ID", "u-x")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    assert s._binding_error() is None


def test_read_graph_refuses_unpinned_targets(monkeypatch):
    """Codex #5: only graph_id-keyed targets are exposed; the rest are refused."""
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(s, "_ACTOR_ID", "sub-1")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-x")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    # "branch" left this list deliberately (2026-08-26): the universe had the X
    # credential deposited and still could not post, because it could list its
    # branches but not read one's node wiring. Reading a branch is strictly
    # weaker than running one, which this surface already allows.
    # "runs"/"run" left this list deliberately (2026-08-26): the universe queued
    # the founder's real X post and then could not say whether it posted - the run
    # had already failed on an unapproved source_code node. "I queued it" is not
    # an outcome, and reading your own run is not a write.
    # Binding reads now use the canonical universe+binding-id SQL scope.
    # Global discovery remains confined to its separate public browse surface.
    for bad in ("goals", "goal", "agents", "agent"):
        out = json.loads(s.read_graph(target=bad))
        assert "not available" in out.get("error", ""), bad


def test_read_graph_pins_graph_id_and_target(monkeypatch):
    """The pinned graph_id is the ONLY selector passed to the real handler."""
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s

    captured: dict = {}
    monkeypatch.setattr(us, "read_graph", lambda **kw: (captured.update(kw), "{}")[1])
    monkeypatch.setattr(s, "_ACTOR_ID", "sub-1")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-pinned")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})

    s.read_graph(target="graph")
    assert captured == {"target": "graph", "graph_id": "u-pinned"}


def test_get_status_pins_command_center_id(monkeypatch):
    """Codex #9: get_status keys off command_center_id, not graph_id — pin the right arg."""
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s

    captured: dict = {}
    monkeypatch.setattr(us, "get_status", lambda **kw: (captured.update(kw), "{}")[1])
    monkeypatch.setattr(s, "_ACTOR_ID", "sub-1")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-pinned")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})

    s.get_status()
    assert captured == {"command_center_id": "u-pinned"}


def test_handlers_refused_when_unbound(monkeypatch):
    """No principal/graph → refuse without ever reaching the real handler."""
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s

    calls = {"n": 0}

    def _boom(**kw):
        calls["n"] += 1
        return "{}"

    monkeypatch.setattr(us, "read_graph", _boom)
    monkeypatch.setattr(us, "get_status", _boom)
    monkeypatch.setattr(s, "_ACTOR_ID", "")  # unbound
    monkeypatch.setattr(s, "_GRAPH_ID", "u-x")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})

    assert "refusing" in json.loads(s.read_graph(target="graph")).get("error", "")
    assert "refusing" in json.loads(s.get_status()).get("error", "")
    assert calls["n"] == 0


def test_bind_founder_identity_uses_verified_principal(monkeypatch):
    """Codex #1/#2: identity is the verified principal with least-privilege reads."""
    from tinyassets import engine_mcp_server as s
    from tinyassets.auth import middleware

    monkeypatch.setattr(s, "_ACTOR_ID", "workos-sub-123")
    token = s._bind_founder_identity()
    try:
        ident = middleware.current_identity()
        assert ident.user_id == "workos-sub-123"
        assert set(ident.capabilities) == {"read", "list"}
        assert "write" not in ident.capabilities
        assert "submit_request" not in ident.capabilities
    finally:
        middleware._current_identity.reset(token)


def test_bind_founder_identity_refuses_without_actor(monkeypatch, nobody):
    # No actor bound to the engine surface means nobody to act as, and there
    # is no anonymous principal to stand in (founder, 2026-09-02).
    from tinyassets import engine_mcp_server as s
    from tinyassets.auth import middleware

    monkeypatch.setattr(s, "_ACTOR_ID", "")
    with pytest.raises(PermissionError, match="no bound actor"):
        s._bind_founder_identity()
    assert middleware.current_identity_or_none() is None


# ── engine_mcp_server: shared commons (browse / read / remix) ───────────────

def _bind_ids(monkeypatch, actor="sub-1", graph="u-x"):
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(s, "_ACTOR_ID", actor)
    monkeypatch.setattr(s, "_GRAPH_ID", graph)
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    return s


def test_browse_commons_fails_closed_when_unbound(monkeypatch):
    s = _bind_ids(monkeypatch, actor="")  # no principal
    assert "refusing" in json.loads(s.browse_commons()).get("error", "")


def test_browse_commons_rejects_unknown_kind(monkeypatch):
    s = _bind_ids(monkeypatch)
    out = json.loads(s.browse_commons(kind="nodes"))
    assert "not available" in out.get("error", "")


def test_browse_commons_branches_lists_published_with_read_caps(monkeypatch):
    """branches → list_branches scope=published, viewer = bound founder, read-only caps."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s
    from tinyassets.auth import middleware

    monkeypatch.setattr(s, "_ACTOR_ID", "sub-9")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-9")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    captured: dict = {}

    def _fake(**kw):
        ident = middleware.current_identity()
        captured["kw"] = kw
        captured["user_id"] = ident.user_id
        captured["caps"] = set(ident.capabilities)
        return "{}"

    monkeypatch.setattr(ext, "_extensions_impl", _fake)
    s.browse_commons(kind="branches", author="someone")
    assert captured["kw"]["action"] == "list_branches"
    assert captured["kw"]["scope"] == "published"
    assert captured["kw"]["author"] == "someone"
    assert captured["user_id"] == "sub-9"  # viewer = founder
    assert captured["caps"] == {"read", "list"}  # least privilege


def test_browse_commons_agents_delegates_to_read_graph(monkeypatch):
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch)
    captured: dict = {}
    monkeypatch.setattr(us, "read_graph", lambda **kw: (captured.update(kw), "{}")[1])
    s.browse_commons(kind="agents", query="triage", limit=5)
    assert captured["target"] == "agents"
    assert captured["query"] == "triage"
    assert captured["limit"] == 5


def test_read_commons_shape_reads_any_public_branch_by_id(monkeypatch):
    """NOT graph-pinned: the caller-supplied branch_id passes through; the
    canonical get_branch author-gates a private non-authored shape."""
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s
    from tinyassets.auth import middleware

    monkeypatch.setattr(s, "_ACTOR_ID", "sub-9")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-9")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    captured: dict = {}

    def _fake(**kw):
        captured["kw"] = kw
        captured["caps"] = set(middleware.current_identity().capabilities)
        return "{}"

    monkeypatch.setattr(us, "read_graph", _fake)
    s.read_commons_shape(branch_id="other-universe-branch")
    assert captured["kw"] == {"target": "branch", "branch_id": "other-universe-branch"}
    assert captured["caps"] == {"read", "list"}  # read-only


def test_read_commons_shape_requires_exactly_one_id(monkeypatch):
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch)
    calls = {"n": 0}
    monkeypatch.setattr(us, "read_graph", lambda **kw: (calls.update(n=1), "{}")[1])
    # neither -> error
    assert "exactly one" in json.loads(s.read_commons_shape()).get("error", "")
    # both -> error (Codex #7: must not silently pick branch_id)
    assert "exactly one" in json.loads(
        s.read_commons_shape(branch_id="b", agent_definition_id="a")
    ).get("error", "")
    assert calls["n"] == 0  # never reached the read


def test_remix_shape_requires_fork_from_and_name(monkeypatch):
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-ok")
    mock_engine_admission(monkeypatch, {"u-ok"})
    assert "fork_from" in json.loads(s.remix_shape(name="x")).get("error", "")
    assert "name is required" in json.loads(
        s.remix_shape(fork_from="v-1")
    ).get("error", "")


def test_remix_shape_refused_without_serving_authority(monkeypatch):
    """Remix requires current serving-owner admission before reaching writes."""
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-not-listed")
    mock_engine_admission(monkeypatch, {"u-other"})
    calls = {"n": 0}
    monkeypatch.setattr(us, "write_graph", lambda **kw: (calls.update(n=1), "{}")[1])
    out = json.loads(s.remix_shape(fork_from="v-1", name="mine"))
    assert "current serving owner" in out.get("error", "")
    assert calls["n"] == 0  # never reached the write


def test_remix_shape_forks_private_with_minimal_caps(monkeypatch):
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s
    from tinyassets.auth import middleware

    monkeypatch.setattr(s, "_ACTOR_ID", "sub-9")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    captured: dict = {}

    def _fake(**kw):
        captured["kw"] = kw
        captured["caps"] = set(middleware.current_identity().capabilities)
        return "{}"

    monkeypatch.setattr(us, "write_graph", _fake)
    s.remix_shape(fork_from="v-parent", name="my remix", description="tweaked")
    assert captured["kw"]["target"] == "branch"
    assert captured["kw"]["operation"] == "remix"
    spec = json.loads(captured["kw"]["payload_json"])
    assert spec["fork_from"] == "v-parent"
    assert spec["name"] == "my remix"
    assert spec["visibility"] == "private"  # private by default
    assert spec["description"] == "tweaked"
    # least-privilege branch-write caps (Codex #6): NOT submit_request.
    assert {"read", "list", "write", "costly"} == captured["caps"]
    assert "submit_request" not in captured["caps"]


def test_publish_shape_is_not_exposed_this_slice(monkeypatch):
    """PUBLISH is deferred to the consent-gated slice (Codex ADAPT #5) — the
    engine server must not expose it, and it is absent from the allowlist."""
    from tinyassets import engine_mcp_server as s
    from tinyassets import universe_intelligence as ui

    assert not hasattr(s, "publish_shape")
    assert "publish_shape" not in ui._ENGINE_MCP_TOOLS


def test_run_graph_refuses_foreign_private_branch(monkeypatch):
    """Codex ADAPT #1: a foreign-private branch id must be refused before the run
    path loads it — indistinguishable from missing."""
    import tinyassets.api.branches as branches
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    # not readable by the founder -> None (foreign-private or missing)
    monkeypatch.setattr(branches, "_resolve_readable_branch", lambda *a, **k: None)
    calls = {"n": 0}
    monkeypatch.setattr(us, "run_graph", lambda **kw: (calls.update(n=1), "{}")[1])
    out = json.loads(s.run_graph(branch_def_id="foreign-private"))
    assert "not found" in out.get("error", "")
    assert calls["n"] == 0  # the run path was never reached


def test_run_graph_binds_its_admission_to_the_started_run(monkeypatch, tmp_path):
    """The admission is charged as a write before the run; binding it to the
    run_id is what lets the dispatcher downgrade it to a read afterwards."""
    import sqlite3
    import types

    import tinyassets.api.branches as branches
    import tinyassets.universe_server as us
    from tinyassets import engine_admissions as adm
    from tinyassets import engine_mcp_server as s

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(branches, "_resolve_readable_branch",
                        lambda *a, **k: ("b1", types.SimpleNamespace()))
    started = json.dumps({"run_id": "run-77", "status": "running"})
    monkeypatch.setattr(us, "run_graph", lambda **kw: started)
    out = s.run_graph(branch_def_id="b1")
    assert "run-77" in out
    conn = sqlite3.connect(str(tmp_path / adm.LEDGER_NAME))
    rows = conn.execute("SELECT universe_id, kind, run_id FROM admissions").fetchall()
    conn.close()
    assert rows == [("u-9", "write", "run-77")]


# ── engine_mcp_server: governed brain read-write loop ───────────────────────

def _seed_brain_universe(monkeypatch, tmp_path, uid="u-brain"):
    """Seed a real OKF bundle and point the engine + resolver at it."""
    import tinyassets.api.helpers as helpers
    from tinyassets import engine_mcp_server as s
    from tinyassets.universe_bundle import seed_okf_bundle

    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    # _engine_run_admit keys its settlement ledger off TINYASSETS_DATA_DIR (not
    # _base_path), so isolate it per test.
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    udir = tmp_path / uid
    seed_okf_bundle(udir, purpose="help the founder", loop_branch_def_id="")
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02): the
    # persona assembler refuses content on a universe nobody owns.
    own_universe(tmp_path, uid)
    monkeypatch.setattr(s, "_ACTOR_ID", "sub-brain")
    monkeypatch.setattr(s, "_GRAPH_ID", uid)
    mock_engine_admission(monkeypatch, {uid})
    return udir


def test_read_brain_returns_editable_sections(monkeypatch, tmp_path):
    from tinyassets import engine_mcp_server as s

    _seed_brain_universe(monkeypatch, tmp_path)
    out = json.loads(s.read_brain())
    # orgchart joined the brain sections 2026-08-23 so the agent can record its org
    # chart (previously unwritable → it re-asked every turn).
    assert set(out["brain"]) == {"identity", "founder", "origin", "body", "orgchart"}
    # all five are governed-editable (orgchart via the SOUL_EDIT_GOVERNED baseline).
    assert set(out["editable_sections"]) == {
        "identity", "founder", "origin", "body", "orgchart",
    }
    assert "self_model" in out


def test_write_brain_then_next_system_prompt_reflects_it(monkeypatch, tmp_path):
    """THE loop: a write_brain edit lands in the files the NEXT turn's system
    prompt is rebuilt from."""
    from tinyassets import engine_mcp_server as s
    from tinyassets.universe_intelligence import _build_persona_system_prompt

    udir = _seed_brain_universe(monkeypatch, tmp_path)
    marker = "I am Aria, a research companion who tracks the founder's reading list."
    res = json.loads(s.write_brain(identity=marker, name="Aria"))
    assert res.get("ok") is True

    # Next turn: the system prompt is rebuilt from the universe's brain files.
    # T2 = the founder disclosure tier (full grounding).
    prompt = _build_persona_system_prompt(udir, universe_id="u-brain", tier="T2")
    assert "Aria" in prompt
    assert "research companion" in prompt


def test_brain_read_write_round_trip_does_not_nest_frontmatter(monkeypatch, tmp_path):
    """read_brain returns the clean body, so write -> read -> write-back is stable
    and never nests managed frontmatter (Codex brain-loop review)."""
    from tinyassets import engine_mcp_server as s

    _seed_brain_universe(monkeypatch, tmp_path)
    marker = "I am Aria, the founder's research companion."
    s.write_brain(identity=marker)
    body1 = json.loads(s.read_brain())["brain"]["identity"]
    assert marker in body1
    assert "---" not in body1  # no frontmatter leaked into the body
    # Echo the read body back; it must not accumulate frontmatter.
    s.write_brain(identity=body1)
    body2 = json.loads(s.read_brain())["brain"]["identity"]
    assert body2 == body1


def test_write_brain_cannot_touch_soul_md(monkeypatch, tmp_path):
    """soul.md (its frontmatter carries the executable loop_branch_def_id) is not
    an accepted section and is never written by write_brain."""
    from tinyassets import engine_mcp_server as s

    udir = _seed_brain_universe(monkeypatch, tmp_path)
    before = (udir / "soul.md").read_text(encoding="utf-8")
    s.write_brain(identity="I am Aria, the founder's research companion.")
    after = (udir / "soul.md").read_text(encoding="utf-8")
    assert before == after  # soul.md untouched
    # and there is no soul/harness-code parameter on the tool
    import inspect
    params = set(inspect.signature(getattr(s.write_brain, "fn", s.write_brain)).parameters)
    assert "soul" not in params and "source_code" not in params


def test_write_brain_refuses_hardlinked_governed_file(monkeypatch, tmp_path):
    """Codex brain-loop review: if identity.md is a hardlink aliasing soul.md,
    write_brain must NOT mutate soul.md's (executable) frontmatter through it."""
    import os

    from tinyassets import engine_mcp_server as s

    udir = _seed_brain_universe(monkeypatch, tmp_path)
    identity = udir / "identity.md"
    soul = udir / "soul.md"
    soul_before = soul.read_text(encoding="utf-8")
    # Plant the alias: identity.md becomes a hardlink to soul.md (same inode).
    identity.unlink()
    os.link(soul, identity)

    out = json.loads(s.write_brain(identity="I am Aria, the research companion."))
    assert out.get("error")  # refused, not ok
    assert soul.read_text(encoding="utf-8") == soul_before  # soul.md untouched


def test_write_brain_snapshot_sink_cannot_alias_soul(monkeypatch, tmp_path):
    """Codex re-review BLOCKER: the SECONDARY soul-edit sinks (log/snapshot/index)
    must not write through a hardlink either. Atomic writes repoint the name to a
    fresh inode, so a soul_versions/index.md hardlinked to soul.md cannot overwrite
    it during the snapshot."""
    import os

    from tinyassets import engine_mcp_server as s

    udir = _seed_brain_universe(monkeypatch, tmp_path)
    soul = udir / "soul.md"
    soul_before = soul.read_text(encoding="utf-8")
    index = udir / "soul_versions" / "index.md"
    index.parent.mkdir(parents=True, exist_ok=True)
    if index.exists():
        index.unlink()
    os.link(soul, index)  # predictable-name sink aliased to soul.md

    s.write_brain(identity="I am Aria, the founder's research companion.")
    assert soul.read_text(encoding="utf-8") == soul_before  # soul.md untouched


def _can_symlink(tmp_path):
    probe = tmp_path / "_sl_probe"
    try:
        probe.symlink_to(tmp_path)
        probe.unlink()
        return True
    except (OSError, NotImplementedError):
        return False


def test_write_brain_refuses_parent_dir_symlink_escape(monkeypatch, tmp_path):
    """Codex re-review: soul_versions symlinked to an external dir must not let the
    snapshot write escape the universe."""
    import os

    import pytest

    from tinyassets import engine_mcp_server as s

    if not _can_symlink(tmp_path):
        pytest.skip("symlinks not creatable on this host")
    udir = _seed_brain_universe(monkeypatch, tmp_path)
    external = tmp_path / "external_dir"
    external.mkdir()
    sv = udir / "soul_versions"
    if sv.exists():
        import shutil
        shutil.rmtree(sv)
    os.symlink(external, sv, target_is_directory=True)

    out = json.loads(s.write_brain(identity="I am Aria, the founder's companion."))
    assert out.get("error")  # refused
    # nothing was written into the external dir
    assert not any(external.iterdir())


def test_read_brain_does_not_follow_symlinked_section(monkeypatch, tmp_path):
    """Codex re-review: a brain file symlinked out of the universe must not
    disclose the external file's contents through read_brain."""
    import os

    import pytest

    from tinyassets import engine_mcp_server as s

    if not _can_symlink(tmp_path):
        pytest.skip("symlinks not creatable on this host")
    udir = _seed_brain_universe(monkeypatch, tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET EXTERNAL", encoding="utf-8")
    ident = udir / "identity.md"
    ident.unlink()
    os.symlink(secret, ident)

    out = json.loads(s.read_brain())
    assert "TOP SECRET" not in json.dumps(out)
    assert out["brain"]["identity"] == ""  # not disclosed


def test_engine_run_admit_never_writes_through_a_symlinked_ledger(monkeypatch, tmp_path):
    """A tampered settlement ledger records nothing -- and refuses nothing: the
    ledger admits every run (spec `engine-run-admissions`)."""
    import os

    import pytest

    from tinyassets import engine_mcp_server as s

    if not _can_symlink(tmp_path):
        pytest.skip("symlinks not creatable on this host")
    external_db = tmp_path / "external.db"
    external_db.write_text("", encoding="utf-8")
    ledger = tmp_path / ".engine_run_admissions.db"
    os.symlink(external_db, ledger)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets.engine_admissions import ADMITTED_UNRECORDED

    assert s._engine_run_admit() == ADMITTED_UNRECORDED
    assert external_db.read_text(encoding="utf-8") == ""


def test_write_brain_rejects_oversized_name(monkeypatch, tmp_path):
    from tinyassets import engine_mcp_server as s

    _seed_brain_universe(monkeypatch, tmp_path)
    out = json.loads(s.write_brain(name="x" * (s._BRAIN_MAX_NAME_BYTES + 1)))
    assert "name is too long" in out.get("error", "")


def test_write_brain_refused_without_serving_authority(monkeypatch, tmp_path):
    from tinyassets import engine_mcp_server as s

    _seed_brain_universe(monkeypatch, tmp_path)
    mock_engine_admission(monkeypatch, {"u-other"})
    out = json.loads(s.write_brain(identity="x is a specific grounded fact here"))
    assert "current serving owner" in out.get("error", "")


def test_write_brain_requires_something_to_write(monkeypatch, tmp_path):
    from tinyassets import engine_mcp_server as s

    _seed_brain_universe(monkeypatch, tmp_path)
    assert "nothing to write" in json.loads(s.write_brain()).get("error", "")


def test_write_brain_rejects_oversized_section(monkeypatch, tmp_path):
    """A brain section is system-prompt material, so an unbounded body is refused
    (Codex brain-loop review)."""
    from tinyassets import engine_mcp_server as s

    _seed_brain_universe(monkeypatch, tmp_path)
    huge = "x" * (s._BRAIN_MAX_SECTION_BYTES + 1)
    out = json.loads(s.write_brain(identity=huge))
    assert "too large" in out.get("error", "")


def test_read_brain_fails_closed_unbound(monkeypatch):
    s = _bind_ids(monkeypatch, actor="")
    assert "refusing" in json.loads(s.read_brain()).get("error", "")


def test_write_brain_uses_least_privilege_caps(monkeypatch, tmp_path):
    """The brain write binds read/list/write only — no costly/submit (Codex #5)."""
    import tinyassets.universe_intelligence as ui
    from tinyassets import engine_mcp_server as s
    from tinyassets.auth import middleware

    _seed_brain_universe(monkeypatch, tmp_path)
    caps = {}
    monkeypatch.setattr(
        ui, "commit_learning",
        lambda udir, proposed, **kw: (
            caps.update(c=set(middleware.current_identity().capabilities)),
            {"updated_files": ["identity.md"]},
        )[1],
    )
    s.write_brain(identity="I am Aria, the founder's research companion.")
    assert caps["c"] == {"read", "list", "write"}
    assert "costly" not in caps["c"] and "submit_request" not in caps["c"]


# ── universe_intelligence._sandboxed_config: the enable gate ────────────────

def _fake_ctx():
    class _Cfg:
        timeout = 300

    class _Ctx:
        config = _Cfg()

    return _Ctx()


def test_sandboxed_config_engine_mcp_off_by_default(monkeypatch):
    from tinyassets import universe_intelligence as ui

    monkeypatch.setattr(ui, "_engine_mcp_enabled", lambda: False)
    cfg = ui._sandboxed_config(
        _fake_ctx(), founder_principal="sub", universe_id="u", granted=True
    )
    assert cfg.engine_mcp_enabled is False
    assert "mcp__tinyassets__read_graph" not in (cfg.allowed_tools or ())
    assert "mcp__*" in (cfg.disallowed_tools or ())


def test_sandboxed_config_fails_closed_non_founder_or_no_principal(monkeypatch):
    from tinyassets import universe_intelligence as ui

    monkeypatch.setattr(ui, "_engine_mcp_enabled", lambda: True)
    # granted but no verified principal → off
    cfg = ui._sandboxed_config(
        _fake_ctx(), founder_principal="", universe_id="u", granted=True
    )
    assert cfg.engine_mcp_enabled is False
    # principal present but not a founder turn → off
    cfg = ui._sandboxed_config(
        _fake_ctx(), founder_principal="sub", universe_id="u", granted=False
    )
    assert cfg.engine_mcp_enabled is False
    # the default (learning-extractor) call → off
    assert ui._sandboxed_config(_fake_ctx()).engine_mcp_enabled is False


def test_sandboxed_config_on_when_all_conditions_met(monkeypatch):
    from tinyassets import universe_intelligence as ui

    monkeypatch.setattr(ui, "_engine_mcp_enabled", lambda: True)
    cfg = ui._sandboxed_config(
        _fake_ctx(), founder_principal="sub-9", universe_id="u-9", granted=True
    )
    assert cfg.engine_mcp_enabled is True
    assert "mcp__tinyassets__read" in cfg.allowed_tools
    assert "mcp__tinyassets__bash" in cfg.allowed_tools
    # read-only commons + brain handles are admitted; remix + publish are held
    # off every served allowlist pending the closure-sanitize / consent gate.
    from tinyassets.served_tools import granted_tools
    for _h in ("browse_commons", "read_commons_shape", "read_brain", "write_brain"):
        assert _h in granted_tools(cfg), _h
        assert f"mcp__tinyassets__{_h}" not in cfg.allowed_tools, _h
    assert "mcp__tinyassets__publish_shape" not in cfg.allowed_tools
    assert "mcp__tinyassets__remix_shape" not in cfg.allowed_tools
    # the wildcard deny is dropped so the tinyassets handles are admittable...
    assert "mcp__*" not in cfg.disallowed_tools
    # ...but the resource readers stay denied (surface = exactly the handles)
    assert "ReadMcpResourceTool" in cfg.disallowed_tools
    assert cfg.engine_mcp_actor_id == "sub-9"
    assert cfg.engine_mcp_graph_id == "u-9"


# ── claude_provider._engine_mcp_flags: the CLI wiring ───────────────────────

def test_engine_mcp_flags_fail_closed_without_ids(tmp_path):
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.claude_provider import _engine_mcp_flags

    cfg = ModelConfig(
        engine_mcp_enabled=True, engine_mcp_actor_id="", engine_mcp_graph_id="u"
    )
    assert _engine_mcp_flags(cfg, tmp_path) == []
    cfg = ModelConfig(
        engine_mcp_enabled=True, engine_mcp_actor_id="sub", engine_mcp_graph_id=""
    )
    assert _engine_mcp_flags(cfg, tmp_path) == []


def test_engine_mcp_flags_emits_strict_config_and_pins(tmp_path, monkeypatch):
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.claude_provider import _engine_mcp_flags

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))

    cfg = ModelConfig(
        engine_mcp_enabled=True,
        engine_mcp_actor_id="sub-9",
        engine_mcp_graph_id="u-9",
    )
    flags = _engine_mcp_flags(cfg, tmp_path)
    assert "--strict-mcp-config" in flags
    assert "--mcp-config" in flags

    data = json.loads((tmp_path / ".runtime" / "engine-mcp-config.json").read_text())
    srv = data["mcpServers"]["tinyassets"]
    assert srv["args"] == ["-m", "tinyassets.engine_mcp_server"]
    assert srv["env"]["TINYASSETS_ENGINE_ACTOR_ID"] == "sub-9"
    assert srv["env"]["TINYASSETS_ENGINE_GRAPH_ID"] == "u-9"


def test_sandbox_cli_args_fails_closed_when_strict_not_installed(monkeypatch, tmp_path):
    """Codex ADAPT #1: engine MCP requested but strict-config not installed (e.g.
    the config write failed) must FAIL the turn, not run with a relaxed policy
    that fails open to ambient connectors."""
    import pytest

    from tinyassets.exceptions import ProviderError
    from tinyassets.providers import claude_provider as cp
    from tinyassets.providers.base import ModelConfig

    monkeypatch.setattr(cp, "_engine_mcp_flags", lambda c, d: [])  # write failed
    cfg = ModelConfig(
        sandbox_workspace=True,
        engine_mcp_enabled=True,
        engine_mcp_actor_id="sub",
        engine_mcp_graph_id="u",
        allowed_tools=("WebFetch", "mcp__tinyassets__read_graph"),
        disallowed_tools=("Bash",),  # mcp__* already dropped by the caller
    )
    with pytest.raises(ProviderError):
        cp._sandbox_cli_args(cfg, tmp_path)


def test_sandbox_cli_args_includes_strict_when_installed(monkeypatch, tmp_path):
    from tinyassets.providers import claude_provider as cp
    from tinyassets.providers.base import ModelConfig

    monkeypatch.setattr(
        cp, "_engine_mcp_flags",
        lambda c, d: ["--mcp-config", "x", "--strict-mcp-config"],
    )
    cfg = ModelConfig(
        sandbox_workspace=True,
        engine_mcp_enabled=True,
        engine_mcp_actor_id="sub",
        engine_mcp_graph_id="u",
    )
    flags, _cwd = cp._sandbox_cli_args(cfg, tmp_path)
    assert "--strict-mcp-config" in flags


# Codex no longer wires an MCP server: its served turn declares the granted
# tools as app-server dynamicTools and dials this route from the platform
# process (tests/test_codex_app_server.py; route resolution in
# tests/test_engine_mcp_routes.py).


# ── connect_compute (slice 4): served compute-provider registration ──────────

def test_connect_compute_fails_closed_when_unbound(monkeypatch):
    from tinyassets import engine_mcp_server as s

    monkeypatch.setattr(s, "_ACTOR_ID", "")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-x")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    out = json.loads(s.connect_compute(access_method="subscription_cli"))
    assert "not bound" in out.get("error", "")


def test_connect_compute_refused_without_serving_authority(monkeypatch):
    """Registration refuses failed current-owner admission before the implementation."""
    import tinyassets.api.compute_connection as cc
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-not-listed")
    mock_engine_admission(monkeypatch, {"u-other"})
    calls = {"n": 0}
    monkeypatch.setattr(cc, "connect_compute", lambda **kw: (calls.update(n=1), {})[1])
    out = json.loads(s.connect_compute(access_method="subscription_cli", ref="codex"))
    assert "current serving owner" in out.get("error", "")
    assert calls["n"] == 0  # never reached the write


def test_connect_compute_requires_access_method(monkeypatch):
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-ok")
    mock_engine_admission(monkeypatch, {"u-ok"})
    out = json.loads(s.connect_compute(access_method="  "))
    assert "access_method is required" in out.get("error", "")


def test_connect_compute_pins_universe_and_binds_write_caps(monkeypatch):
    """universe_id is PINNED (never caller-supplied) and least-privilege write caps
    are bound (no submit_request / costly)."""
    import tinyassets.api.compute_connection as cc
    from tinyassets import engine_mcp_server as s
    from tinyassets.auth import middleware

    monkeypatch.setattr(s, "_ACTOR_ID", "sub-cc")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-cc")
    mock_engine_admission(monkeypatch, {"u-cc"})
    captured: dict = {}

    def _fake(**kw):
        captured["kw"] = kw
        captured["caps"] = set(middleware.current_identity().capabilities)
        return {"status": "registered", "definition_id": "provdef_x"}

    monkeypatch.setattr(cc, "connect_compute", _fake)
    s.connect_compute(
        access_method="api_key_http", protocol="openai_chat",
        model="moonshotai/kimi-k2", ref="http_grant_z", visibility="private",
    )
    assert captured["kw"]["universe_id"] == "u-cc"  # PINNED, not caller-supplied
    payload = captured["kw"]["payload"]
    assert payload["access_method"] == "api_key_http"
    assert payload["protocol"] == "openai_chat"
    assert payload["model"] == "moonshotai/kimi-k2"
    assert payload["ref"] == "http_grant_z"
    # strict least privilege: a pure WRITE — write alone, no read/list/submit/costly.
    assert {"write"} == captured["caps"]
    assert "submit_request" not in captured["caps"]
    assert "costly" not in captured["caps"]


def test_connect_compute_registers_subscription_cli_end_to_end(monkeypatch, tmp_path):
    """Full flow against the real impl: the served agent registers a candidate under
    the founder's own universe (admin ACL required); no secret ever appears."""
    from tinyassets import engine_mcp_server as s
    from tinyassets.daemon_server import grant_universe_access

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    uid = "u-cc-e2e"
    (tmp_path / uid).mkdir(parents=True)
    grant_universe_access(
        tmp_path, universe_id=uid, actor_id="founder-cc",
        permission="admin", granted_by="founder-cc",
    )
    monkeypatch.setattr(s, "_ACTOR_ID", "founder-cc")
    monkeypatch.setattr(s, "_GRAPH_ID", uid)
    mock_engine_admission(monkeypatch, {uid})

    out = json.loads(s.connect_compute(
        access_method="subscription_cli", protocol="cli:codex",
        model="gpt-5-codex", ref="codex",
    ))
    assert out["status"] == "registered", out
    assert out["access_method"] == "subscription_cli"
    # No credential-bearing KEY is ever projected (there is no secret to leak). The
    # projection is a fixed, known-safe shape — assert on keys, not a substring scan
    # (the "next" guidance text legitimately names the "api_key_http" access method).
    assert set(out) <= {
        "status", "definition_id", "access_method", "protocol", "model",
        "visibility", "next",
    }, out
    for banned in ("secret", "token", "api_key", "apikey", "authorization",
                   "bearer", "password", "credential", "ref", "auth_material"):
        assert banned not in out, banned
    # A non-admin actor on the same universe is refused (owner-gate, uniform envelope).
    monkeypatch.setattr(s, "_ACTOR_ID", "intruder")
    # Keep exercising the downstream owner envelope, independently of admission.
    mock_engine_admission(monkeypatch, {uid})
    refused = json.loads(s.connect_compute(
        access_method="subscription_cli", protocol="cli:codex",
        model="gpt-5-codex", ref="codex",
    ))
    assert refused.get("error") == "not_found"


def test_connect_compute_api_key_http_grant_isolation_end_to_end(monkeypatch, tmp_path):
    """The served api_key_http path enforces grant isolation via _validate_http_grant:
    a same-owner/same-universe grant registers; a foreign-universe, foreign-owner, or
    nonexistent grant is refused with the uniform envelope (no grant existence leak)."""
    from tinyassets import engine_mcp_server as s
    from tinyassets.daemon_server import grant_universe_access
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    uid = "u-akh"
    (tmp_path / uid).mkdir(parents=True)
    grant_universe_access(
        tmp_path, universe_id=uid, actor_id="owner-akh",
        permission="admin", granted_by="owner-akh",
    )
    ledger = ConnectionLedger(
        tmp_path / "outbound.db", verify_authenticated_principal=lambda: "owner-akh"
    )
    ledger.create_connection(
        connection_id="http_akh", owner_user_id="owner-akh", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("http",), provider="http",
        destination="compute:x", credential_ref="vault://http/compute:x",
        allowed_endpoints=[{"host": "api.example.com",
                            "path_template": "/v1/chat/completions", "methods": ["POST"]}],
    )
    # (a) grant bound to THIS universe + owner -> registers.
    ledger.grant_connection(
        grant_id="grant_ok", connection_id="http_akh", owner_user_id="owner-akh",
        universe_id=uid, unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )
    # (b) SAME connection granted to ANOTHER universe -> foreign-universe grant.
    ledger.grant_connection(
        grant_id="grant_other_uni", connection_id="http_akh", owner_user_id="owner-akh",
        universe_id="u-other", unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )

    monkeypatch.setattr(s, "_ACTOR_ID", "owner-akh")
    monkeypatch.setattr(s, "_GRAPH_ID", uid)
    mock_engine_admission(monkeypatch, {uid})

    def _cc(ref):
        return json.loads(s.connect_compute(
            access_method="api_key_http", protocol="openai_chat",
            model="moonshotai/kimi-k2", ref=ref,
        ))

    assert _cc("grant_ok")["status"] == "registered"
    # Every inaccessible non-empty ref -> the SAME uniform not_found (Codex adapt #1):
    # foreign-universe, foreign-owner, absent, and revoked are indistinguishable, so
    # the surface is not an existence/ownership oracle.
    assert _cc("grant_other_uni").get("error") == "not_found"
    assert _cc("grant_does_not_exist").get("error") == "not_found"

    # A DIFFERENT founder whose grant belongs to another owner is refused not_found.
    other_ledger = ConnectionLedger(
        tmp_path / "outbound.db", verify_authenticated_principal=lambda: "owner-akh"
    )
    other_ledger.grant_connection(
        grant_id="grant_foreign_owner", connection_id="http_akh",
        owner_user_id="owner-akh", universe_id="u-intruder",
        unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )
    grant_universe_access(
        tmp_path, universe_id="u-intruder", actor_id="intruder-akh",
        permission="admin", granted_by="intruder-akh",
    )
    (tmp_path / "u-intruder").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(s, "_ACTOR_ID", "intruder-akh")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-intruder")
    mock_engine_admission(monkeypatch, {"u-intruder"})
    # The grant is owned by owner-akh, not intruder-akh -> uniform not_found.
    assert _cc("grant_foreign_owner").get("error") == "not_found"


def test_connect_compute_is_exposed_in_both_provider_allowlists():
    """The server handler is dark unless the provider enabled-tools allowlists list
    it (live 2026-08-23: served agent got tool_search "Found 0 tools" because it was
    missing). Guard both served paths so it cannot silently drop out again."""
    from tinyassets.providers.base import ModelConfig
    from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES, model_tools
    from tinyassets.universe_intelligence import _ENGINE_MCP_ALLOWED, _ENGINE_MCP_TOOLS
    assert "connect_compute" in BACKEND_ENGINE_CAPABILITIES
    assert "bash" in model_tools(ModelConfig())  # Codex reaches it through ta.
    assert "bash" in _ENGINE_MCP_TOOLS  # Claude reaches it through ta.
    assert "mcp__tinyassets__bash" in _ENGINE_MCP_ALLOWED
    # And the server actually registers a handler by that name (exposure is real).
    from tinyassets import engine_mcp_server as s
    assert callable(getattr(s, "connect_compute", None))


def test_read_graph_compute_target_lists_own_providers_end_to_end(monkeypatch, tmp_path):
    """The served read_graph now accepts target=compute (pinned to this universe) and
    lists the universe's own registered providers — the read sibling of connect_compute."""
    from tinyassets import engine_mcp_server as s
    from tinyassets.api.compute_connection import connect_compute
    from tinyassets.auth.middleware import _current_identity
    from tinyassets.auth.provider import Identity
    from tinyassets.daemon_server import grant_universe_access

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    uid = "u-compute-read"
    (tmp_path / uid).mkdir(parents=True)
    grant_universe_access(tmp_path, universe_id=uid, actor_id="founder-cr",
                          permission="admin", granted_by="founder-cr")
    # Register a provider as the founder (direct impl, admin identity bound).
    tok = _current_identity.set(Identity(user_id="founder-cr", username="founder-cr",
                                         capabilities=["read", "list", "write"]))
    try:
        reg = connect_compute(universe_id=uid, payload={
            "access_method": "subscription_cli", "protocol": "cli:codex",
            "model": "gpt-5-codex", "ref": "codex"})
        assert reg["status"] == "registered"
    finally:
        _current_identity.reset(tok)

    # "compute" is now a pinned read target (not refused).
    assert "compute" in s._PINNED_READ_TARGETS
    monkeypatch.setattr(s, "_ACTOR_ID", "founder-cr")
    monkeypatch.setattr(s, "_GRAPH_ID", uid)
    mock_engine_admission(monkeypatch, {uid})
    out = json.loads(s.read_graph(target="compute"))
    assert out.get("count") == 1, out
    assert out["providers"][0]["definition_id"] == reg["definition_id"]
    # Graph-pinned: a served read cannot address another universe (the arg is fixed).
    monkeypatch.setattr(s, "_GRAPH_ID", "u-someone-else")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    other = json.loads(s.read_graph(target="compute"))
    assert other.get("error") == "not_found"  # no admin ACL there -> uniform not_found


def test_read_graph_connections_target_lists_own_http_connections_end_to_end(
    monkeypatch, tmp_path
):
    """The served read_graph accepts target=connections (pinned to this universe) and
    lists the universe's own http CHANNEL connections — so the agent self-serves the
    connection_id / grant_id / host / path for an authenticated_external_call node
    instead of asking the owner to paste them back. Channel-agnostic: any deposited
    destination appears identically, and no secret is included."""
    from tinyassets import engine_mcp_server as s
    from tinyassets.api.http_connection import connect_http
    from tinyassets.auth.middleware import _current_identity
    from tinyassets.auth.provider import Identity
    from tinyassets.daemon_server import grant_universe_access

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    uid = "u-conn-read"
    (tmp_path / uid).mkdir(parents=True)
    grant_universe_access(tmp_path, universe_id=uid, actor_id="founder-cx",
                          permission="admin", granted_by="founder-cx")
    tok = _current_identity.set(Identity(user_id="founder-cx", username="founder-cx",
                                         capabilities=["read", "list", "write"]))
    try:
        dep = connect_http(universe_id=uid, payload=json.dumps({
            "destination": "webhook:anything",
            "secret": "sk-not-echoed",
            "allowed_endpoints": [{"host": "api.example.com",
                                   "path_template": "/v1/messages", "methods": ["POST"]}],
        }))
        assert dep["status"] == "provisioned", dep
    finally:
        _current_identity.reset(tok)

    assert "connections" in s._PINNED_READ_TARGETS
    monkeypatch.setattr(s, "_ACTOR_ID", "founder-cx")
    monkeypatch.setattr(s, "_GRAPH_ID", uid)
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    out = json.loads(s.read_graph(target="connections"))
    rows = [c for c in out.get("connections", []) if c["destination"] == "webhook:anything"]
    assert len(rows) == 1, out
    assert rows[0]["connection_class"] == "http"
    assert rows[0]["connection_id"] and rows[0]["grant_id"]
    assert rows[0]["allowed_endpoints"][0]["host"] == "api.example.com"
    assert "sk-not-echoed" not in json.dumps(out)
    # Graph-pinned: pointed at a different (here connection-less) universe the served
    # read returns an empty list — and in production the agent cannot vary _GRAPH_ID
    # at all, so it only ever reads its own universe. (Owner-level isolation on a
    # SHARED universe is the job of
    # test_connections_list_isolates_by_owner_not_just_universe, which actually
    # deposits a second owner's connection; this assertion is only a graph-pin check.)
    # TWO cases, because they exercise different code. A name nobody owns is not
    # a universe since 2026-09-02, so that read is REFUSED at the access gate --
    # which is stronger, but it returns BEFORE the connection ledger is queried,
    # so on its own it no longer covers the ledger's universe filtering (Codex
    # review round 2, P2). The OWNED-but-connection-less universe is what keeps
    # that coverage: it reaches the ledger and the ledger must return nothing.
    second = "u-also-mine"
    (tmp_path / second).mkdir(parents=True)
    grant_universe_access(tmp_path, universe_id=second, actor_id="founder-cx",
                          permission="admin", granted_by="founder-cx")
    monkeypatch.setattr(s, "_GRAPH_ID", second)
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    owned_other = json.loads(s.read_graph(target="connections"))
    assert owned_other.get("error") is None, owned_other
    assert owned_other.get("connections") == [], owned_other
    assert owned_other.get("count") == 0, owned_other

    monkeypatch.setattr(s, "_GRAPH_ID", "u-not-mine")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    other = json.loads(s.read_graph(target="connections"))
    assert not other.get("connections"), other
    assert other.get("count", 0) == 0, other
    assert other.get("error") == "not_found", other


def test_read_graph_branches_target_lists_own_workflows_end_to_end(monkeypatch, tmp_path):
    """The served read_graph accepts target=branches (pinned to this universe) and lists
    the universe's OWN workflows by name + branch_def_id, so the agent can resolve a
    workflow the user names without asking for an internal id (the gap Claude.ai hit
    2026-08-25: "Global workflow enumeration is not exposed by the advertised handles")."""
    from tinyassets import engine_mcp_server as s
    from tinyassets.api.extensions import _extensions_impl
    from tinyassets.auth.middleware import _current_identity
    from tinyassets.auth.provider import Identity
    from tinyassets.daemon_server import grant_universe_access

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    uid = "u-branches-read"
    (tmp_path / uid).mkdir(parents=True)
    grant_universe_access(tmp_path, universe_id=uid, actor_id="founder-br",
                          permission="admin", granted_by="founder-br")
    tok = _current_identity.set(Identity(user_id="founder-br", username="founder-br",
                                         capabilities=["read", "list", "write"]))
    try:
        created = json.loads(_extensions_impl(
            action="create_branch", name="Compute smoke check", description="x",
        ))
        assert created.get("status") == "created", created
    finally:
        _current_identity.reset(tok)

    assert "branches" in s._PINNED_READ_TARGETS
    monkeypatch.setattr(s, "_ACTOR_ID", "founder-br")
    monkeypatch.setattr(s, "_GRAPH_ID", uid)
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})
    out = json.loads(s.read_graph(target="branches"))
    names = {r["name"]: r["branch_def_id"] for r in out.get("branches", [])}
    assert names.get("Compute smoke check") == created["branch_def_id"], out


def test_served_allowlists_do_not_drift():
    """The two served engine-MCP allowlists (codex + claude) MUST offer the SAME
    tools — a codex-served and a claude-served universe get identical capability
    (founder rule: all surfaces do the same things). run_graph drifting onto the
    claude list ONLY (caught 2026-08-23) meant a codex-served founder could not run
    automations at all; this guard prevents that class of silent divergence."""
    from tinyassets import served_tools
    from tinyassets.providers import codex_provider
    from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS
    from tinyassets.universe_intelligence import _ENGINE_MCP_TOOLS as claude_tools

    # Structural guarantee: both surfaces select from the SAME canonical tuple,
    # so they cannot drift by construction (not merely "equal today"). Codex
    # declares exactly model_tools(config) as its app-server dynamicTools.
    assert codex_provider.model_tools is served_tools.model_tools
    assert claude_tools is SERVED_ENGINE_MCP_TOOLS
    # run_graph is the capability that lets a universe RUN automations from the app.
    from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES
    assert SERVED_ENGINE_MCP_TOOLS == ("read", "write", "edit", "bash")
    assert "run_graph" in BACKEND_ENGINE_CAPABILITIES


def test_served_write_graph_refuses_unmounted_targets(monkeypatch):
    """Adding automation controls must not open the broad connector dispatcher.

    Connection/agent/goal/request/universe remain unavailable here. Branch create
    reaches build_branch; automation's separate owner adapter is tested in
    test_served_automation_lifecycle.py.
    """
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    captured = {"n": 0, "kw": None}

    def _fake(**kw):
        captured["n"] += 1
        captured["kw"] = kw
        return "{}"

    monkeypatch.setattr(ext, "_extensions_impl", _fake)

    for bad in ("connection", "agent", "goal", "request", "universe"):
        out = json.loads(s.write_graph(target=bad, operation="create"))
        expected = ("configure_provider_capability' only" if bad == "connection"
                    else "must be 'branch'")
        assert expected in out.get("error", ""), bad
    assert captured["n"] == 0, "a non-branch target must never reach the write impl"

    # target=branch create reaches the author-gated, effect-free build_branch.
    s.write_graph(target="branch", operation="create", payload_json="{}")
    assert captured["kw"]["action"] == "build_branch"


def test_served_write_graph_create_and_patch_only(monkeypatch):
    """Only operation=create/patch/delete are served — publish/remix/bind_provider
    are refused (publishing/forking stay in the browser flow), before any write.
    Delete joined on 2026-09-02 (branch-delete-on-write-graph)."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    calls = {"n": 0}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: (calls.update(n=1), "{}")[1])
    for op in ("publish", "remix", "bind_provider"):
        out = json.loads(s.write_graph(target="branch", operation=op))
        assert "'create'" in out.get("error", "") and "'delete'" in out.get("error", ""), op
    assert calls["n"] == 0


def test_served_write_graph_requires_explicit_operation(monkeypatch):
    """Empty operation must be refused, never fall through to a default write."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    calls = {"n": 0}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: (calls.update(n=1), "{}")[1])
    out = json.loads(s.write_graph(target="branch", operation=""))
    assert "operation='create'" in out.get("error", "")
    assert calls["n"] == 0


def test_served_write_graph_create_forces_private(monkeypatch):
    """A served create is PRIVATE to the universe — a spec cannot self-declare
    public/published (publishing is a separate, consent-gated browser step)."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    captured = {}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: captured.update(kw) or "{}")

    s.write_graph(target="branch", operation="create",
                  payload_json='{"name":"x","visibility":"public","published":true}')
    assert captured["action"] == "build_branch"
    spec = json.loads(captured["spec_json"])
    assert spec["visibility"] == "private", "served create must force private"
    assert "published" not in spec, "published must be stripped"


def test_served_write_graph_strips_node_approval_and_fork(monkeypatch):
    """CRITICAL (Codex adapt): a served create must not be able to smuggle an
    APPROVED source_code node (self-computable hash -> RCE via the live run_graph),
    a forged author, or a per-node fork. Every approval/author/fork field is
    stripped from every node before build_branch, and the top-level fork_from too."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    captured = {}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: captured.update(kw) or "{}")

    hostile = {
        "name": "x",
        "fork_from": "v-foreign",
        "node_defs": [{
            "node_id": "n1",
            "display_name": "n1",
            "source_code": "print('x')",
            "approved": True,
            "approved_source_hash": "deadbeef",
            "approved_by": "founder",
            "approved_at": "2026-01-01",
            "approval_reason": "trust me",
            "author": "someone-else",
            "fork_from": "v-foreign",
        }],
    }
    s.write_graph(target="branch", operation="create", payload_json=json.dumps(hostile))
    spec = json.loads(captured["spec_json"])
    assert "fork_from" not in spec, "top-level fork_from must be stripped"
    node = spec["node_defs"][0]
    for banned in ("approved", "approved_source_hash", "approved_by", "approved_at",
                   "approval_reason", "author", "fork_from"):
        assert banned not in node, f"{banned} must be stripped from a served node"
    # The legit shape survives.
    assert node["source_code"] == "print('x')"


def test_served_write_graph_strips_approval_in_alt_containers(monkeypatch):
    """CRITICAL bypass: build_branch reads nodes from `nodes` too (not only
    `node_defs`), so approval-stripping must cover EVERY container. A hostile
    approved node under `nodes` must persist unapproved."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    captured = {}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: captured.update(kw) or "{}")

    hostile = {
        "name": "x",
        "nodes": [{
            "node_id": "n1", "source_code": "import os",
            "approved": True, "approved_source_hash": "abc",
        }],
    }
    s.write_graph(target="branch", operation="create", payload_json=json.dumps(hostile))
    spec = json.loads(captured["spec_json"])
    node = spec["nodes"][0]
    assert "approved" not in node and "approved_source_hash" not in node


def test_served_write_graph_rejects_nested_graph_blob(monkeypatch):
    """CRITICAL bypass: nodes can hide under a nested `graph` blob
    (build_branch reads graph.node_defs). The served create rejects the blob
    outright rather than chase every container."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    calls = {"n": 0}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: (calls.update(n=1), "{}")[1])
    hostile = json.dumps({"name": "x", "graph": {"node_defs": [
        {"node_id": "n1", "source_code": "import os", "approved": True,
         "approved_source_hash": "abc"}]}})
    out = json.loads(s.write_graph(target="branch", operation="create", payload_json=hostile))
    assert "invalid branch spec" in out.get("error", "")
    assert "graph" in out.get("error", "")
    assert calls["n"] == 0


def test_served_write_graph_rejects_node_ref(monkeypatch):
    """CRITICAL (Codex round-2 #1): a node may carry node_ref, which build_branch
    dereferences and INHERITS the referenced node's stored (hash-only, self-
    computable) approval — a pre-forged public node copied this way would run.
    Reject node_ref (top-level and per-node) before it can reach build_branch."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    calls = {"n": 0}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: (calls.update(n=1), "{}")[1])
    # per-node node_ref (copy a foreign approved node)
    per_node = json.dumps({"name": "x", "node_defs": [
        {"node_id": "n1", "node_ref": {"source": "pub-branch", "node_id": "evil"}}]})
    out = json.loads(s.write_graph(target="branch", operation="create", payload_json=per_node))
    assert "node_ref is not allowed" in out.get("error", "")
    # top-level node_ref
    top = json.dumps({"name": "x", "node_ref": {"source": "pub-branch", "node_id": "evil"}})
    out2 = json.loads(s.write_graph(target="branch", operation="create", payload_json=top))
    assert "node_ref is not allowed" in out2.get("error", "")
    assert calls["n"] == 0


def test_served_write_graph_preserves_opaque_workflow_data(monkeypatch):
    """REQUIRED (Codex round-2 #2): stripping is NODE-LEVEL, not recursive — a
    user's opaque nested data (e.g. a state field default_value that happens to
    contain a key named 'author'/'public') must survive untouched, while the
    node's OWN top-level approval/author fields are still stripped."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    captured = {}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: captured.update(kw) or "{}")

    spec = {
        "name": "x",
        "state_schema": {"fields": [
            {"field_name": "meta",
             "default_value": {"author": "Ada", "public": True, "mode": "safe"}}]},
        "node_defs": [{
            "node_id": "n1", "prompt_template": "hi",
            "author": "someone-else", "approved": True,
            "config": {"author": "kept-here", "public": False},
        }],
    }
    s.write_graph(target="branch", operation="create", payload_json=json.dumps(spec))
    out = json.loads(captured["spec_json"])
    # `state_schema` is CANONICALIZED to the field list by the sanitizer as of
    # 2026-09-30 (PR #4123): the accepted shapes have to be one list shared with
    # the builder, or the widest of them skips this very guard. The claim this
    # test exists for is unchanged -- stripping is node-level, not recursive --
    # so it is asserted on the canonical shape.
    assert isinstance(out["state_schema"], list), out["state_schema"]
    # Opaque nested data preserved verbatim.
    dv = out["state_schema"][0]["default_value"]
    assert dv == {"author": "Ada", "public": True, "mode": "safe"}
    # And the wrapper carried the field through rather than dropping it.
    assert out["state_schema"][0]["field_name"] == "meta"
    node = out["node_defs"][0]
    assert node["config"] == {"author": "kept-here", "public": False}
    # But the node's OWN authoritative approval/author fields are stripped.
    assert "author" not in node and "approved" not in node


def test_served_write_graph_rejects_invoke_allows_channel_effect(monkeypatch):
    """Combined build+run confinement (Codex 2026-08-24 + channel/consent slice
    2026-08-25): a served build must not declare sub-branch invocation, a NON-channel
    effect sink, or the typed handoffs path — but the ONE channel-agnostic node
    (authenticated_external_call) IS allowed. Building declares only the sink name and
    fires nothing; its outbound call stays gated at run time by the connection grant +
    per-destination consent + the outbound flag + SSRF."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    calls = {"n": 0}

    def _impl(**kw):
        calls["n"] += 1
        return "{}"

    monkeypatch.setattr(ext, "_extensions_impl", _impl)

    def _spec(node):
        return {"name": "x", "node_defs": [{"node_id": "n1", **node}]}

    reject = [
        _spec({"invoke_branch_spec": {"branch_def_id": "b"}}),
        _spec({"invoke_branch_version_spec": {"v": "x"}}),
        _spec({"await_run_spec": {"run_id": "r"}}),
        _spec({"effects": ["wiki_write_back"]}),   # a real sink, but not the channel node
        _spec({"effects": ["arbitrary_sink"]}),    # unknown sink
        _spec({"handoffs": [{"adapter": "x"}]}),   # typed-effect path stays off-surface
    ]
    for spec in reject:
        out = json.loads(s.write_graph(target="branch", operation="create",
                                       payload_json=json.dumps(spec)))
        assert "not available on the served build surface" in out.get("error", ""), spec
    assert calls["n"] == 0
    # The ONE channel-agnostic effect node IS allowed — builds normally.
    ok = _spec({"prompt_template": "hi", "effects": ["authenticated_external_call"]})
    s.write_graph(target="branch", operation="create", payload_json=json.dumps(ok))
    assert calls["n"] == 1
    # Empty effects list is fine (means "no effects").
    ok2 = _spec({"prompt_template": "hi", "effects": []})
    s.write_graph(target="branch", operation="create", payload_json=json.dumps(ok2))
    assert calls["n"] == 2


def test_served_write_graph_effects_must_be_string_list(monkeypatch):
    """A malformed effects declaration is a structured rejection, never reaches build."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    calls = {"n": 0}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: (calls.update(n=1), "{}")[1])
    for bad in ("authenticated_external_call", [123], [{"sink": "x"}]):
        spec = {"name": "x", "node_defs": [{"node_id": "n1", "effects": bad}]}
        out = json.loads(s.write_graph(target="branch", operation="create",
                                       payload_json=json.dumps(spec)))
        assert "must be a JSON array of strings" in out.get("error", ""), bad
    assert calls["n"] == 0


def test_served_write_graph_rejects_duplicate_effect_sinks(monkeypatch):
    """One node with duplicate sink entries fires N outbound calls at run time,
    bypassing the node-count cap (Codex #1). A node may declare at most ONE
    effect sink — the wording moved from "the channel sink at most once" when
    `workspace` joined the allowlist (2026-08-31); the rule did not."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    calls = {"n": 0}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: (calls.update(n=1), "{}")[1])
    dup = {"name": "x", "node_defs": [{"node_id": "n1", "effects": [
        "authenticated_external_call", "authenticated_external_call",
    ]}]}
    out = json.loads(s.write_graph(target="branch", operation="create",
                                   payload_json=json.dumps(dup)))
    assert "at most one effect sink" in out.get("error", "")
    assert calls["n"] == 0


def test_served_write_graph_has_no_shape_cap(monkeypatch):
    """Founder 2026-08-30 (`no-graph-size-caps`): a universe may build as large
    a graph as it wants - 300 nodes, 60 of them effect nodes - the served
    surface refuses none of it. Usage (admissions, consent, at-most-once) is
    what bounds a big graph; its shape is the user's."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    calls = {"n": 0}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: (calls.update(n=1), "{}")[1])
    nodes = [
        {"node_id": f"e{i}", "effects": ["authenticated_external_call"]} for i in range(60)
    ] + [{"node_id": f"p{i}", "prompt_template": "x"} for i in range(240)]
    out = json.loads(s.write_graph(target="branch", operation="create",
                                   payload_json=json.dumps({"name": "x", "node_defs": nodes})))
    assert "error" not in out, out
    assert calls["n"] == 1
    assert not hasattr(s, "_SERVED_MAX_EFFECT_NODES") and not hasattr(s, "_SERVED_MAX_NODES")


def test_served_write_graph_byte_cap_counts_utf8(monkeypatch):
    """The payload DoS bound counts ENCODED UTF-8 bytes, not str length — a
    multibyte payload just under the char count but over the byte cap is refused."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    calls = {"n": 0}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: (calls.update(n=1), "{}")[1])
    # Just over the byte bound in 3-byte chars: fewer CHARS than the bound,
    # more BYTES - the bound must count encoded bytes.
    payload = "☃" * (s._SERVED_MAX_SPEC_BYTES // 3 + 1024)
    out = json.loads(s.write_graph(target="branch", operation="create", payload_json=payload))
    assert "too large" in out.get("error", "").lower()
    assert calls["n"] == 0


def test_served_write_graph_rejects_bad_types(monkeypatch):
    """A wrong-typed field ({"name":[]}) returns a STRUCTURED rejection, never
    crashes the served MCP server, and never reaches build_branch (Codex #6)."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    calls = {"n": 0}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: (calls.update(n=1), "{}")[1])
    for bad in ('{"name":[]}', '{"node_defs":"nope"}', '{"node_defs":[123]}',
                '{"node_defs":[{"node_id":[]}]}'):
        out = json.loads(s.write_graph(target="branch", operation="create", payload_json=bad))
        assert "invalid branch spec" in out.get("error", ""), bad
    assert calls["n"] == 0


def test_served_write_graph_payload_too_large(monkeypatch):
    """A served build payload past the DoS bound is refused before parse/persist."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-9")
    mock_engine_admission(monkeypatch, {"u-9"})
    monkeypatch.setattr(s, "_engine_run_admit", lambda **kw: True)
    calls = {"n": 0}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: (calls.update(n=1), "{}")[1])
    out = json.loads(s.write_graph(target="branch", operation="create",
                                   payload_json="x" * (s._SERVED_MAX_SPEC_BYTES + 1024)))
    assert "too large" in out.get("error", "").lower()
    assert calls["n"] == 0
    # The bound is transport sanity, not a shape cap: a legitimately large
    # graph under it builds (see test_served_write_graph_has_no_shape_cap).


def test_served_write_graph_refused_without_serving_authority(monkeypatch):
    """Failed current-owner admission refuses writes before the implementation."""
    import tinyassets.api.extensions as ext
    from tinyassets import engine_mcp_server as s

    _bind_ids(monkeypatch, graph="u-not-listed")
    mock_engine_admission(monkeypatch, {"u-other"})
    calls = {"n": 0}
    monkeypatch.setattr(ext, "_extensions_impl", lambda **kw: (calls.update(n=1), "{}")[1])
    out = json.loads(s.write_graph(target="branch", operation="create"))
    assert "current serving owner" in out.get("error", "")
    assert calls["n"] == 0


def test_read_graph_reads_one_branch_by_id(monkeypatch):
    """Live 2026-08-26: with the X connection deposited, the universe still refused to
    post - it could enumerate branches but not inspect one, so it could not know the
    input contract and (correctly) would not guess against a real post."""
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s

    captured: dict = {}
    monkeypatch.setattr(us, "read_graph", lambda **kw: (captured.update(kw), "{}")[1])
    monkeypatch.setattr(s, "_ACTOR_ID", "sub-1")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-pinned")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})

    s.read_graph(target="branch", branch_id="  8157e928f42c  ")
    assert captured == {
        "target": "branch",
        "graph_id": "u-pinned",
        "branch_id": "8157e928f42c",
    }

    # branch_id is meaningless for every other target and must not leak through.
    captured.clear()
    s.read_graph(target="branches", branch_id="8157e928f42c")
    assert captured == {"target": "branches", "graph_id": "u-pinned"}


def test_read_graph_reads_one_run_by_id(monkeypatch):
    """Live 2026-08-26: after run_graph the universe told the founder "the run was
    accepted and queued... I cannot confirm whether the post actually went out". The
    run had already failed (node_not_approved). Reading your own run must be possible
    from the surface that started it."""
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s

    captured: dict = {}
    monkeypatch.setattr(us, "read_graph", lambda **kw: (captured.update(kw), "{}")[1])
    monkeypatch.setattr(s, "_ACTOR_ID", "sub-1")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-pinned")
    mock_engine_admission(monkeypatch, {s._GRAPH_ID})

    s.read_graph(target="run", run_id="  08a17f75653b4fe3  ")
    assert captured == {
        "target": "run",
        "graph_id": "u-pinned",
        "run_id": "08a17f75653b4fe3",
    }

    captured.clear()
    s.read_graph(target="runs")
    assert captured == {"target": "runs", "graph_id": "u-pinned"}

    # A selector for one target must never leak into another.
    captured.clear()
    s.read_graph(target="graph", run_id="08a17f75653b4fe3", branch_id="x")
    assert captured == {"target": "graph", "graph_id": "u-pinned"}


def test_read_graph_docstring_tells_the_agent_to_read_the_outcome():
    """The docstring is the only instruction the served agent gets."""
    from tinyassets import engine_mcp_server as s

    doc = (s.read_graph.fn if hasattr(s.read_graph, "fn") else s.read_graph).__doc__ or ""
    assert "ALWAYS read this after" in doc
    assert "queued" in doc


def test_served_guidance_teaches_the_code_node_and_promises_no_approval():
    """Live 2026-08-26: the agent built a source_code node and the run died with
    node_not_approved - because approval was a gate nobody could grant. Since
    change `sandboxed-code-node` (2026-08-30) code runs in the OS sandbox, in the
    universe that authored it, with no approval step: the served guidance must
    teach the code node (fetch -> code -> write) and must never send the agent
    to a browser approval that does not exist."""
    from tinyassets import engine_mcp_server as s

    # REACHABLE, not resident (2026-09-26): the code-node contract moved into the
    # `code_nodes` handbook chapter the resident index names. The negative
    # assertions below -- no browser approval, no dead instructions -- now span
    # the whole reachable text, which is where they belong: a dead instruction in
    # a chapter would have misled the agent just as badly.
    doc = s.served_tool_guidance("write_graph")
    assert "CODE NODES" in doc
    assert "def run(state, effects)" in doc
    assert "accept_statuses" in doc
    assert "code_node_failed" in doc
    assert "DO NOT build the delivery node with ``source_code``" not in doc
    assert "no such button" not in doc
    assert "UNAPPROVED until you" not in doc
    # The packet contract itself must survive the switch.
    for key in ('"sink": "authenticated_external_call"', "connection_id", "grant_id"):
        assert key in doc
    # Nothing on this surface may promise a browser approval -- including a
    # handbook chapter, which the agent reads and would be misled by just as badly.
    whole = (s.__doc__ or "") + "".join(
        s.served_tool_guidance(name)
        for name in ("read_graph", "write_graph", "run_graph", "get_status")
    )
    assert "approves the source in the browser" not in whole
    assert "approves it in the browser" not in whole


@pytest.mark.parametrize("identity_only", [False, True])
def test_custom_agent_write_brain_refuses_main_identity_but_writes_shared_fields(
    monkeypatch, tmp_path, identity_only,
):
    from types import SimpleNamespace
    from urllib.parse import parse_qsl, urlsplit

    from fastmcp.server import dependencies

    from tinyassets import engine_mcp_server as s
    from tinyassets.engine_steering import route_with_session

    udir = _seed_brain_universe(monkeypatch, tmp_path)
    before = (udir / "identity.md").read_bytes()
    route = route_with_session("http://localhost/mcp", "thread:agent:weaver:principal:sub-brain")
    monkeypatch.setattr(dependencies, "get_http_request", lambda: SimpleNamespace(
        query_params=dict(parse_qsl(urlsplit(route).query))))
    out = json.loads(s.write_brain(identity="I am Weaver.", name="Weaver",
                                  founder="" if identity_only else "My founder studies tidepools."))
    assert "may not" in out["error"] and "identity.md" in out["error"]
    assert (udir / "identity.md").read_bytes() == before
    assert json.loads(s.read_brain())["self_model"].get("name") != "Weaver"
    if not identity_only:
        assert out["written"]
        assert "tidepools" in (udir / "founder.md").read_text(encoding="utf-8")



def test_custom_agent_write_tool_passes_session_agent_to_runner(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from urllib.parse import parse_qsl, urlsplit

    from fastmcp.server import dependencies

    from tinyassets import engine_mcp_server as s
    from tinyassets import universe_tools
    from tinyassets.engine_steering import route_with_session

    udir = _seed_brain_universe(monkeypatch, tmp_path)
    agent_id = "agent_binding_w1"
    route = route_with_session(
        "http://localhost/mcp", f"thread:agent:{agent_id}:principal:sub-brain")
    monkeypatch.setattr(dependencies, "get_http_request", lambda: SimpleNamespace(
        query_params=dict(parse_qsl(urlsplit(route).query))))
    calls = []

    def runner(universe_dir, inner, *, agent_id, **kwargs):
        calls.append((universe_dir, agent_id, inner, kwargs))
        return universe_tools.ToolRun(0, b"", None, 0.0)

    monkeypatch.setattr(universe_tools, "RUNNER", runner)
    out = asyncio.run(s.write_file(path="founder.md", content="shared learning"))
    assert out.startswith("wrote "), out
    assert len(calls) == 1
    assert calls[0][0] == udir
    assert calls[0][1] == agent_id
    assert calls[0][3]["stdin"] == b"shared learning"
