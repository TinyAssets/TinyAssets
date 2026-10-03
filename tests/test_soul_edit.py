"""The learn/write path: `universe action=soul.edit` (OpenSpec universe-creation).

The execution path reads and follows the universe's own `soul.edit.md` policy:
edits are proposed learning with source + context (never blind overwrites),
update only explicitly changed governed files, append `log.md`, and write a
`soul_versions/` snapshot. This is what lets a founder's universe actually
REMEMBER what it learns (the 2026-07-01 dogfood found the bonding conversation
persisted nothing).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets.soul_edit import SoulEditError, apply_soul_edit
from tinyassets.universe_bundle import seed_okf_bundle


@pytest.fixture
def universe(tmp_path: Path) -> Path:
    udir = tmp_path / "u-test"
    udir.mkdir()
    seed_okf_bundle(udir)
    return udir


def _frontmatter_value(path: Path, key: str) -> str:
    import yaml

    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    meta = yaml.safe_load(parts[1]) or {}
    return str(meta.get(key, ""))


# ── core learning contract ──────────────────────────────────────────────────


def test_soul_edit_updates_governed_file_and_flips_learned(universe):
    result = apply_soul_edit(
        universe,
        changes={"identity.md": "# Identity\n\nI am Orion, a universe of maps.\n"},
        agent_id="main", source="founder conversation",
        context="founder named me during first contact",
    )
    assert result["updated_files"] == ["identity.md"]
    text = (universe / "identity.md").read_text(encoding="utf-8")
    assert "I am Orion" in text
    # OKF frontmatter preserved + learning recorded
    assert _frontmatter_value(universe / "identity.md", "type") == "Universe Identity"
    assert _frontmatter_value(universe / "identity.md", "status") == "learned"
    assert (
        _frontmatter_value(universe / "identity.md", "learned_from")
        == "founder conversation"
    )


def test_soul_edit_sets_identity_name_frontmatter(universe):
    apply_soul_edit(
        universe,
        changes={"identity.md": "# Identity\n\nMy name is Orion.\n"},
        agent_id="main", source="founder",
        context="naming",
        name="Orion",
    )
    from tinyassets.universe_self_model import read_self_model

    model = read_self_model(universe)
    assert model["name"] == "Orion"
    assert "identity" in {k["slug"] for k in model["known"]}


def test_soul_edit_name_only_learns_identity(universe):
    # "Your name is Orion" should be one call — no body required.
    result = apply_soul_edit(
        universe, changes={}, agent_id="main", source="founder", context="naming", name="Orion",
    )
    assert result["updated_files"] == ["identity.md"]
    from tinyassets.universe_self_model import read_self_model

    assert read_self_model(universe)["name"] == "Orion"


def test_soul_edit_body_carries_projects_as_body(universe):
    # Host intent: the founder's projects ARE the universe's body.
    apply_soul_edit(
        universe,
        changes={
            "body.md": (
                "# Body\n\nMy body is my founder's projects: TinyAssets is my "
                "trunk; its site, connector, and daemons are my limbs.\n"
            )
        },
        agent_id="main", source="founder conversation",
        context="founder taught me my body is their projects",
    )
    from tinyassets.universe_self_model import read_self_model

    assert "body" in {k["slug"] for k in read_self_model(universe)["known"]}


def test_soul_edit_multiple_governed_files_one_edit(universe):
    result = apply_soul_edit(
        universe,
        changes={
            "founder.md": "# Founder\n\nMy founder is Jonathan.\n",
            "origin.md": "# Origin\n\nI grew from the TinyAssets project.\n",
        },
        agent_id="main", source="founder conversation",
        context="first bonding conversation",
    )
    assert sorted(result["updated_files"]) == ["founder.md", "origin.md"]


def test_soul_edit_updates_soul_md_body_preserving_frontmatter(universe):
    before_fm = _frontmatter_value(universe / "soul.md", "okf_source")
    apply_soul_edit(
        universe,
        changes={
            "soul.md": "# Universe Soul\n\nMy purpose: bring my founder's projects to life.\n",
        },
        agent_id="main", source="founder",
        context="purpose statement",
    )
    text = (universe / "soul.md").read_text(encoding="utf-8")
    assert "bring my founder's projects to life" in text
    assert _frontmatter_value(universe / "soul.md", "okf_source") == before_fm
    assert _frontmatter_value(universe / "soul.md", "edit_authority") == "soul.edit"


# ── policy enforcement ──────────────────────────────────────────────────────


def test_soul_edit_rejects_non_governed_files(universe):
    # orgchart.md is now GOVERNED (joined SOUL_EDIT_GOVERNED 2026-08-23 so the agent
    # can record its org chart); projects/goals/log/index remain non-governed.
    for bad in ("projects.md", "goals.md", "log.md", "index.md"):
        with pytest.raises(SoulEditError):
            apply_soul_edit(
                universe, changes={bad: "x"}, agent_id="main", source="s", context="c",
            )


def test_soul_edit_rejects_path_traversal(universe):
    for bad in ("../evil.md", "soul_versions/0001.md", "..\\evil.md", "/etc/x"):
        with pytest.raises(SoulEditError):
            apply_soul_edit(
                universe, changes={bad: "x"}, agent_id="main", source="s", context="c",
            )


def test_soul_edit_requires_source_and_context(universe):
    with pytest.raises(SoulEditError):
        apply_soul_edit(
            universe, changes={"identity.md": "x"}, agent_id="main", source="", context="c",
        )
    with pytest.raises(SoulEditError):
        apply_soul_edit(
            universe, changes={"identity.md": "x"}, agent_id="main", source="s", context="",
        )


def test_soul_edit_requires_some_change(universe):
    with pytest.raises(SoulEditError):
        apply_soul_edit(universe, changes={}, agent_id="main", source="s", context="c")


def test_soul_edit_refuses_without_policy_file(universe):
    (universe / "soul.edit.md").unlink()
    with pytest.raises(SoulEditError):
        apply_soul_edit(
            universe, changes={"identity.md": "x"}, agent_id="main", source="s", context="c",
        )


def test_policy_governs_beyond_the_baseline(universe):
    # The policy file is still read as authority for what it lists BEYOND the
    # baseline grounding files: a non-baseline file the universe adds to its policy
    # becomes governable, and a file that is neither baseline nor policy-listed is
    # rejected.
    from tinyassets.soul_edit import read_governed_files

    assert "projects.md" not in read_governed_files(universe)  # not baseline, not listed
    policy = (universe / "soul.edit.md").read_text(encoding="utf-8")
    policy = policy.replace("## Governed files\n", "## Governed files\n\n- `projects.md`\n")
    (universe / "soul.edit.md").write_text(policy, encoding="utf-8")
    assert "projects.md" in read_governed_files(universe)  # policy addition honored


def test_baseline_grounding_files_are_always_governed(universe):
    # Anti-drift floor (2026-08-23): the platform baseline of grounding files stays
    # governed even if a universe's (possibly stale) soul.edit.md text omits one — so
    # an existing universe gains a newly-baselined file (e.g. orgchart.md) without a
    # data migration, and a core soul file can never be silently un-governed.
    from tinyassets.soul_edit import read_governed_files
    from tinyassets.universe_bundle import SOUL_EDIT_GOVERNED

    policy = (universe / "soul.edit.md").read_text(encoding="utf-8")
    policy = policy.replace("- `identity.md`\n", "")  # stale/omitted policy text
    (universe / "soul.edit.md").write_text(policy, encoding="utf-8")
    governed = read_governed_files(universe)
    for baseline in SOUL_EDIT_GOVERNED:
        assert baseline in governed, baseline  # floor holds despite the omission


# ── history: log + snapshot ─────────────────────────────────────────────────


def test_soul_edit_appends_log_and_writes_snapshot(universe):
    result = apply_soul_edit(
        universe,
        changes={"identity.md": "# Identity\n\nI am Orion.\n"},
        agent_id="main", source="founder",
        context="naming",
        summary="founder named me Orion",
    )
    log = (universe / "log.md").read_text(encoding="utf-8")
    assert "founder named me Orion" in log
    assert result["snapshot"].startswith("soul_versions/")
    snap = universe / result["snapshot"]
    assert snap.is_file()
    assert "identity.md" in snap.read_text(encoding="utf-8")
    index = (universe / "soul_versions" / "index.md").read_text(encoding="utf-8")
    assert snap.stem in index


def test_every_edit_writes_a_new_snapshot(universe):
    r1 = apply_soul_edit(
        universe, changes={"identity.md": "# A\n"}, agent_id="main", source="s", context="c",
    )
    r2 = apply_soul_edit(
        universe, changes={"identity.md": "# A\n"}, agent_id="main", source="s", context="c2",
    )
    assert r1["snapshot"] != r2["snapshot"]


# ── concurrency + version guard (Codex ADAPT 2026-07-02) ────────────────────
# The reshape makes apply_soul_edit a per-turn path for the universe
# intelligence, so it must be safe against concurrent writes and stale reads.


def test_soul_edit_expected_version_mismatch_rejected(universe):
    # Compare-and-swap: a stale expected hash must reject the write and leave
    # the governed file untouched (no lost-update clobber).
    before = (universe / "identity.md").read_text(encoding="utf-8")
    with pytest.raises(SoulEditError):
        apply_soul_edit(
            universe,
            changes={"identity.md": "# Identity\n\nstale write\n"},
            agent_id="main", source="s",
            context="c",
            expected_versions={"identity.md": "0" * 64},
        )
    assert (universe / "identity.md").read_text(encoding="utf-8") == before


def test_soul_edit_expected_version_match_applies(universe):
    from tinyassets.soul_edit import current_soul_versions

    versions = current_soul_versions(universe, ["identity.md"])
    assert "identity.md" in versions
    result = apply_soul_edit(
        universe,
        changes={"identity.md": "# Identity\n\nI am Orion.\n"},
        agent_id="main", source="s",
        context="c",
        expected_versions=versions,
    )
    assert result["updated_files"] == ["identity.md"]
    assert "I am Orion" in (universe / "identity.md").read_text(encoding="utf-8")


def test_soul_edit_runs_under_per_universe_lock(universe, monkeypatch):
    # The critical section must hold the per-universe soul lock.
    import contextlib as _contextlib

    import tinyassets.soul_edit as se

    real_lock = se._soul_lock
    entered = {"n": 0}

    @_contextlib.contextmanager
    def _tracking(universe_dir):
        entered["n"] += 1
        with real_lock(universe_dir):
            yield

    monkeypatch.setattr(se, "_soul_lock", _tracking)
    apply_soul_edit(
        universe, changes={"identity.md": "# I\n"}, agent_id="main", source="s", context="c",
    )
    assert entered["n"] == 1


def test_soul_edit_concurrent_edits_get_distinct_snapshots(universe):
    # Without the lock, concurrent edits race on the snapshot number (derived
    # from a directory listing) and collide, losing an update. Under the lock
    # every concurrent edit allocates a distinct snapshot.
    import threading

    n = 6
    snap_glob = "[0-9][0-9][0-9][0-9].md"
    before = len(list((universe / "soul_versions").glob(snap_glob)))
    barrier = threading.Barrier(n)
    guard = threading.Lock()
    snaps: list[str] = []
    errs: list[Exception] = []

    def worker(i: int) -> None:
        barrier.wait()
        try:
            r = apply_soul_edit(
                universe,
                changes={"identity.md": f"# Identity\n\nedit {i}\n"},
                agent_id="main", source="founder",
                context=f"concurrent edit {i}",
            )
            with guard:
                snaps.append(r["snapshot"])
        except Exception as exc:  # noqa: BLE001 — surfaced via errs assertion
            with guard:
                errs.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errs, errs
    # No two concurrent edits returned the same snapshot (no allocation race)…
    assert len(set(snaps)) == n, f"snapshot collision: {snaps}"
    # …and each produced a distinct file on disk (none overwritten).
    after = sorted((universe / "soul_versions").glob("[0-9][0-9][0-9][0-9].md"))
    assert len(after) == before + n, [f.name for f in after]
    for rel in snaps:
        assert (universe / rel).is_file(), rel


# ── MCP action wiring (scope + ACL + ledger) ────────────────────────────────


def _login_founder(base: Path, universe_id: str, sub: str = "founder-1") -> None:
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import AuthProvider, Identity
    from tinyassets.daemon_server import grant_universe_access

    class _P(AuthProvider):
        def __init__(self, ident): self.ident = ident
        def resolve_token(self, t): return self.ident if t == "ok" else None
        def is_auth_required(self): return False
        def resolve_always_writes(self): return True
        def register_client(self, m): return {"client_id": "t", **m}
        def create_authorization(self, *a, **k): return "c"
        def exchange_code(self, *a, **k): return None

    set_provider(_P(Identity(
        user_id=sub, username=sub,
        capabilities=["read", "write", "costly", "admin"],
    )))
    auth_middleware("ok")
    grant_universe_access(
        base, universe_id=universe_id, actor_id=sub,
        permission="admin", granted_by=sub,
    )


@pytest.fixture(autouse=True)
def _reset_auth():
    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import DevAuthProvider

    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def test_mcp_soul_edit_action_learns_and_ledgers(tmp_path, monkeypatch):
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    udir = base / "u-home"
    udir.mkdir()
    seed_okf_bundle(udir)
    _login_founder(base, "u-home")

    from tinyassets.api import universe as universe_api

    monkeypatch.setattr(universe_api, "_base_path", lambda: base)
    out = json.loads(universe_api._universe_impl(
        action="soul.edit",
        universe_id="u-home",
        inputs_json=json.dumps({
            "changes": {"identity.md": "# Identity\n\nI am Orion.\n"},
            "source": "founder conversation",
            "context": "founder named me",
            "name": "Orion",
        }),
    ))
    assert out.get("error") is None, out
    assert out["universe_id"] == "u-home"
    assert "identity.md" in out["updated_files"]
    assert out["persona_name"] == "Orion"
    # Ledgered (WRITE_ACTIONS contract)
    ledger = json.loads((udir / "ledger.json").read_text(encoding="utf-8"))
    assert any(e["action"] == "soul.edit" for e in ledger)


def test_mcp_soul_edit_denied_for_non_owner(tmp_path, monkeypatch):
    base = tmp_path / "data"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    udir = base / "u-alices"
    udir.mkdir()
    seed_okf_bundle(udir)
    # Alice owns u-alices and makes it private is not needed: write requires a
    # write/admin grant, which Bob does not hold.
    _login_founder(base, "u-alices", sub="alice")

    from tinyassets.auth.middleware import auth_middleware, set_provider
    from tinyassets.auth.provider import AuthProvider, Identity

    class _P(AuthProvider):
        def __init__(self, ident): self.ident = ident
        def resolve_token(self, t): return self.ident if t == "ok" else None
        def is_auth_required(self): return False
        def resolve_always_writes(self): return True
        def register_client(self, m): return {"client_id": "t", **m}
        def create_authorization(self, *a, **k): return "c"
        def exchange_code(self, *a, **k): return None

    set_provider(_P(Identity(
        user_id="bob", username="bob",
        capabilities=["read", "write", "costly"],
    )))
    auth_middleware("ok")

    from tinyassets.api import universe as universe_api

    monkeypatch.setattr(universe_api, "_base_path", lambda: base)
    out = json.loads(universe_api._universe_impl(
        action="soul.edit",
        universe_id="u-alices",
        inputs_json=json.dumps({
            "changes": {"identity.md": "# Hacked\n"},
            "source": "bob",
            "context": "cross-founder write",
        }),
    ))
    assert out.get("error") == "universe_access_denied"
    assert "Hacked" not in (udir / "identity.md").read_text(encoding="utf-8")


def test_policy_absence_still_fails_closed_despite_baseline_floor(universe):
    # The baseline floor is a UNION with a PRESENT policy — it must NOT resurrect a
    # missing policy into an implicit allow. No soul.edit.md => hard fail (Codex #5).
    from tinyassets.soul_edit import read_governed_files

    (universe / "soul.edit.md").unlink()
    with pytest.raises(SoulEditError):
        read_governed_files(universe)
    with pytest.raises(SoulEditError):
        apply_soul_edit(
            universe, changes={"identity.md": "x"}, agent_id="main", source="s", context="c",
        )


def test_soul_edit_policy_is_never_self_editable(universe):
    # An agent must not be able to edit its OWN policy or promote soul.md through the
    # brain surface (Codex #4): soul.edit.md is neither in the governed baseline nor a
    # brain section, and write_brain exposes no soul/soul.edit section.
    from tinyassets.engine_mcp_server import _BRAIN_SECTIONS
    from tinyassets.soul_edit import read_governed_files
    from tinyassets.universe_bundle import SOUL_EDIT_GOVERNED

    assert "soul.edit.md" not in SOUL_EDIT_GOVERNED
    assert "soul.edit.md" not in read_governed_files(universe)
    assert "soul.edit.md" not in _BRAIN_SECTIONS.values()
    assert "soul.md" not in _BRAIN_SECTIONS.values()  # soul.md not agent-writable


def test_only_the_main_agent_writes_its_name_and_identity(universe):
    """The door every soul write goes through (harness §4.18): another agent's
    learning lands in the shared brain, but never as the main agent's identity."""
    for kwargs in ({"changes": {"identity.md": "# Identity\n\nI am Weave.\n"}},
                   {"changes": {}, "name": "Weave"}):
        with pytest.raises(SoulEditError, match="only the main agent"):
            apply_soul_edit(universe, agent_id="agent_binding_w1", source="s",
                            context="c", **kwargs)
    assert "Weave" not in (universe / "identity.md").read_text(encoding="utf-8")
    result = apply_soul_edit(universe, agent_id="agent_binding_w1", source="s", context="c",
                             changes={"founder.md": "# Founder\n\nStudies tidepools.\n"})
    assert result["updated_files"] == ["founder.md"]
    with pytest.raises(TypeError):
        apply_soul_edit(universe, source="s", context="c",  # no agent stated
                        changes={"founder.md": "# Founder\n\nx\n"})
    with pytest.raises(SoulEditError, match="agent_id is required"):
        apply_soul_edit(universe, agent_id=" ", source="s", context="c",
                        changes={"founder.md": "# Founder\n\nx\n"})
