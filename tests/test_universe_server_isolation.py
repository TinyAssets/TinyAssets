"""Tests that universe action responses include `universe_id` for isolation.

Context: Task #15. Claude.ai cross-universe hallucination was enabled by
tool responses that didn't name the universe they came from. When the bot
reads premise, canon, world-state, activity, etc., the response shape now
leads with `universe_id` so downstream reasoning can ground each fact to
its source universe.

Pairs with #47 (on-disk quarantine), #48 (retrieval audit), #51/#49/#53
(live-code hardening). This is the chat-side half of the cross-universe
cluster.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tinyassets.api.universe as us
from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity
from tinyassets.daemon_server import (
    ensure_universe_registered,
    grant_universe_access,
)


class _StaticAuthProvider(AuthProvider):
    def __init__(self, identity: Identity | None) -> None:
        self.identity = identity

    def resolve_token(self, token: str) -> Identity | None:
        return self.identity if token == "ok" else None

    def is_auth_required(self) -> bool:
        return True

    def register_client(self, metadata: dict) -> dict:
        return {"client_id": "test-client", **metadata}

    def create_authorization(
        self,
        client_id: str,
        redirect_uri: str,
        scope: str,
        state: str,
        code_challenge: str,
        code_challenge_method: str,
    ) -> str:
        return "test-code"

    def exchange_code(
        self,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> dict | None:
        return None


@pytest.fixture
def universe_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    return base


@pytest.fixture(autouse=True)
def _reset_auth_provider() -> None:
    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _authenticate(user_id: str, scopes: list[str] | None = None) -> None:
    identity = Identity(
        user_id=user_id,
        username=user_id,
        capabilities=scopes or [
            "tinyassets.universe.read",
            "tinyassets.universe.write",
            "tinyassets.universe.admin",
        ],
    )
    set_provider(_StaticAuthProvider(identity))
    auth_middleware("ok")


def _own(base: Path, uid: str) -> None:
    """Give the directory an OWNER, which is what makes it a universe.

    2026-09-02: a universe exists because an ownership row names it, not because
    a folder is on disk (``tests/test_a_universe_needs_an_owner.py``). A
    ``founder_home`` binding, NOT an ACL grant -- the D0c model this module
    asserts hangs on "a universe with zero ACL rows is public", and seeding a
    grant would make every fixture here private.
    """
    from tinyassets.daemon_server import set_founder_home

    set_founder_home(base, founder_sub=f"test-owner::{uid}", universe_id=uid)


def _make_universe(base: Path, uid: str) -> Path:
    udir = base / uid
    udir.mkdir(parents=True)
    # Under the universe-visibility contract an undeclared universe fails closed
    # (withheld from list/status/wiki). Declare public explicitly so these tests
    # exercise public-universe behavior.
    from tinyassets.api.visibility import set_universe_visibility

    ensure_universe_registered(base, universe_id=uid, universe_path=udir)
    _own(base, uid)
    set_universe_visibility(uid, "public", source="owner")
    return udir


def _make_private_universe(base: Path, uid: str) -> Path:
    """Create a universe made *private* the ratified way — via the declared
    visibility level, NOT by seeding an ACL row. In the D0c model ownership
    (ACL grants) and visibility are orthogonal: an admin grant alone does not
    hide a universe.
    """
    from tinyassets.api.visibility import set_universe_visibility

    udir = base / uid
    udir.mkdir(parents=True)
    ensure_universe_registered(base, universe_id=uid, universe_path=udir)
    _own(base, uid)
    # `private` sets public_read=False AND declares the explicit level, so the
    # legacy gate and the visibility layer agree (no inconsistent row).
    set_universe_visibility(uid, "private", source="owner")
    return udir


class TestUniverseIdInResponses:
    """Every read response that pulls universe-scoped content must name it."""

    def test_read_premise_includes_universe_id(self, universe_base):
        udir = _make_universe(universe_base, "alpha")
        (udir / "PROGRAM.md").write_text("An alpha premise.", encoding="utf-8")
        out = json.loads(us._action_read_premise(universe_id="alpha"))
        assert out["universe_id"] == "alpha"
        assert out["premise"] == "An alpha premise."

    def test_read_premise_missing_still_includes_universe_id(self, universe_base):
        _make_universe(universe_base, "alpha")
        out = json.loads(us._action_read_premise(universe_id="alpha"))
        assert out["universe_id"] == "alpha"
        assert out["premise"] is None

    def test_read_output_includes_universe_id(self, universe_base):
        udir = _make_universe(universe_base, "alpha")
        (udir / "output").mkdir()
        (udir / "output" / "note.md").write_text("hello", encoding="utf-8")
        out = json.loads(us._action_read_output(universe_id="alpha", path="note.md"))
        assert out["universe_id"] == "alpha"
        assert out["content"] == "hello"

    def test_list_canon_includes_universe_id(self, universe_base):
        udir = _make_universe(universe_base, "alpha")
        (udir / "canon").mkdir()
        (udir / "canon" / "a.md").write_text("x", encoding="utf-8")
        out = json.loads(us._action_list_canon(universe_id="alpha"))
        assert out["universe_id"] == "alpha"

    def test_list_canon_no_canon_dir_still_includes_universe_id(self, universe_base):
        _make_universe(universe_base, "alpha")
        out = json.loads(us._action_list_canon(universe_id="alpha"))
        assert out["universe_id"] == "alpha"

    def test_read_canon_includes_universe_id(self, universe_base):
        udir = _make_universe(universe_base, "alpha")
        (udir / "canon").mkdir()
        (udir / "canon" / "a.md").write_text("hello", encoding="utf-8")
        out = json.loads(us._action_read_canon(universe_id="alpha", filename="a.md"))
        assert out["universe_id"] == "alpha"
        assert out["filename"] == "a.md"

    def test_query_world_no_data_includes_universe_id(self, universe_base):
        _make_universe(universe_base, "alpha")
        out = json.loads(us._action_query_world(universe_id="alpha", query_type="timeline"))
        # timeline has no store; should still echo the universe
        assert out["universe_id"] == "alpha"

    def test_get_activity_includes_universe_id(self, universe_base):
        udir = _make_universe(universe_base, "alpha")
        (udir / "activity.log").write_text("[..] line\n", encoding="utf-8")
        out = json.loads(us._action_get_activity(universe_id="alpha", limit=5))
        assert out["universe_id"] == "alpha"

    def test_get_activity_missing_log_includes_universe_id(self, universe_base):
        _make_universe(universe_base, "alpha")
        out = json.loads(us._action_get_activity(universe_id="alpha"))
        assert out["universe_id"] == "alpha"

    def test_get_ledger_empty_includes_universe_id(self, universe_base):
        _make_universe(universe_base, "alpha")
        out = json.loads(us._action_get_ledger(universe_id="alpha"))
        assert out["universe_id"] == "alpha"

    def test_control_daemon_status_includes_universe_id(self, universe_base):
        _make_universe(universe_base, "alpha")
        out = json.loads(us._action_control_daemon(universe_id="alpha", text="status"))
        assert out["universe_id"] == "alpha"

    def test_different_universes_stay_distinct(self, universe_base):
        """Two universes must never cross-contaminate through the response."""
        udir_a = _make_universe(universe_base, "alpha")
        udir_b = _make_universe(universe_base, "beta")
        (udir_a / "PROGRAM.md").write_text("Alpha premise.", encoding="utf-8")
        (udir_b / "PROGRAM.md").write_text("Beta premise.", encoding="utf-8")

        out_a = json.loads(us._action_read_premise(universe_id="alpha"))
        out_b = json.loads(us._action_read_premise(universe_id="beta"))

        assert out_a["universe_id"] == "alpha"
        assert out_a["premise"] == "Alpha premise."
        assert out_b["universe_id"] == "beta"
        assert out_b["premise"] == "Beta premise."

    def test_inspect_includes_universe_id(self, universe_base):
        """Pre-existing behaviour — regression guard."""
        _make_universe(universe_base, "alpha")
        out = json.loads(us._action_inspect_universe(universe_id="alpha"))
        assert out["universe_id"] == "alpha"


class TestUniverseAclEnforcement:
    """The D0c model: VISIBILITY is ``public_read`` (a missing rule = public);
    OWNERSHIP is the ACL grant set. Writes always require an explicit ``write``
    or ``admin`` grant. An admin grant does NOT make a universe private.
    """

    def test_anonymous_cannot_write_public_universe(self, universe_base, nobody):
        # Dev/no-auth caller (anonymous). Reads are open on a public universe,
        # but a write must be denied — an authenticated grant is required.
        _make_universe(universe_base, "public")

        out = json.loads(us._universe_impl(
            action="set_premise",
            universe_id="public",
            text="Anonymous write attempt.",
        ))

        # The write is still refused; the REASON moved. An unauthenticated
        # caller used to reach the ACL check as a principal named "anonymous"
        # and be denied there. There is no such principal, so the refusal
        # happens at the door -- which is the stronger version of the same
        # guarantee, and the universe is untouched either way.
        assert out["error"] == "Authentication required"
        assert "actor_id" not in out or not out.get("actor_id")
        assert not (universe_base / "public" / "PROGRAM.md").exists()

    def test_anonymous_can_read_public_universe(self, universe_base):
        _make_universe(universe_base, "public")

        out = json.loads(us._universe_impl(action="inspect", universe_id="public"))

        assert out["universe_id"] == "public"
        assert "error" not in out

    def test_authenticated_founder_cannot_write_public_universe_without_grant(
        self, universe_base,
    ):
        # A universe with no ACL rows returns the public "read" convention for
        # every actor; a write still requires an explicit write/admin grant.
        _make_universe(universe_base, "other")
        _authenticate(
            "alice", ["tinyassets.universe.read", "tinyassets.universe.write"],
        )

        out = json.loads(us._universe_impl(
            action="set_premise",
            universe_id="other",
            text="Cross-universe write attempt.",
        ))

        assert out["error"] == "universe_access_denied"
        assert out["required_permission"] == "write"
        assert out["actor_id"] == "alice"
        assert not (universe_base / "other" / "PROGRAM.md").exists()

    def test_public_universe_read_stays_open_for_authenticated_reader(
        self, universe_base,
    ):
        _make_universe(universe_base, "public")
        _authenticate("alice", ["tinyassets.universe.read"])

        out = json.loads(us._universe_impl(action="inspect", universe_id="public"))

        assert out["universe_id"] == "public"
        assert "error" not in out

    def test_create_universe_grants_creator_admin_write(self, universe_base):
        # D0a: an authenticated founder OWNS the universe they create.
        _authenticate(
            "alice",
            ["tinyassets.universe.costly", "tinyassets.universe.write"],
        )

        # universe-creation 5.2: public create self-serializes — a caller id is
        # rejected — so capture the generated serial for the follow-up write.
        created = json.loads(us._universe_impl(
            action="create_universe",
            text="A seed.",
        ))
        uid = created["universe_id"]
        updated = json.loads(us._universe_impl(
            action="set_premise",
            universe_id=uid,
            text="Founder-owned update.",
        ))

        assert created["status"] == "created"
        assert created["founder_id"] == "alice"
        assert updated["status"] == "updated"
        assert (universe_base / uid / "PROGRAM.md").read_text(
            encoding="utf-8",
        ) == "Founder-owned update."

    def test_create_universe_denies_other_founder_write(self, universe_base):
        # D0a: a *different* authenticated founder, even holding the write
        # scope, cannot write a universe founded by someone else.
        _authenticate(
            "alice",
            ["tinyassets.universe.costly", "tinyassets.universe.write"],
        )
        # universe-creation 5.2: public create self-serializes; use the serial.
        created = json.loads(us._universe_impl(
            action="create_universe",
            text="Alice's seed.",
        ))
        assert created["founder_id"] == "alice"
        alice_world = created["universe_id"]

        _authenticate("bob", ["tinyassets.universe.write"])
        out = json.loads(us._universe_impl(
            action="set_premise",
            universe_id=alice_world,
            text="Hostile cross-founder overwrite.",
        ))

        assert out["error"] == "universe_access_denied"
        assert out["required_permission"] == "write"
        assert out["actor_id"] == "bob"

    def test_owner_admin_grant_does_not_make_universe_private(self, universe_base):
        # Ownership != visibility. An admin grant with no public_read=False rule
        # leaves the universe publicly readable.
        _make_universe(universe_base, "owned-public")
        grant_universe_access(
            universe_base,
            universe_id="owned-public",
            actor_id="owner",
            permission="admin",
            granted_by="owner",
        )
        _authenticate("reader", ["tinyassets.universe.read"])

        out = json.loads(us._universe_impl(
            action="inspect",
            universe_id="owned-public",
        ))

        assert out["universe_id"] == "owned-public"
        assert "error" not in out

    def test_private_universe_rejects_unlisted_reader(self, universe_base):
        udir = _make_private_universe(universe_base, "private")
        (udir / "output").mkdir()
        (udir / "output" / "secret.md").write_text("secret", encoding="utf-8")
        grant_universe_access(
            universe_base,
            universe_id="private",
            actor_id="owner",
            permission="admin",
            granted_by="owner",
        )

        _authenticate("intruder", ["tinyassets.universe.read"])

        inspect_out = json.loads(us._universe_impl(
            action="inspect",
            universe_id="private",
        ))
        read_out = json.loads(us._universe_impl(
            action="read_output",
            universe_id="private",
            path="secret.md",
        ))

        assert inspect_out["error"] == "universe_access_denied"
        assert inspect_out["universe_id"] == "private"
        assert inspect_out["required_permission"] == "read"
        assert read_out["error"] == "universe_access_denied"

    def test_private_universe_rejects_reader_write(self, universe_base):
        _make_private_universe(universe_base, "private")
        grant_universe_access(
            universe_base,
            universe_id="private",
            actor_id="reader",
            permission="read",
            granted_by="owner",
        )

        _authenticate("reader", ["tinyassets.universe.write"])

        out = json.loads(us._universe_impl(
            action="set_premise",
            universe_id="private",
            text="Overwrite attempt.",
        ))

        assert out["error"] == "universe_access_denied"
        assert out["required_permission"] == "write"
        assert not (universe_base / "private" / "PROGRAM.md").exists()

    def test_private_universe_allows_granted_writer(self, universe_base):
        # {write, admin} write-set: a "write" grant CAN write (matches current
        # main). Only no-grant / read-only actors are denied writes.
        _make_private_universe(universe_base, "private")
        grant_universe_access(
            universe_base,
            universe_id="private",
            actor_id="writer",
            permission="write",
            granted_by="owner",
        )

        _authenticate("writer", ["tinyassets.universe.write"])

        out = json.loads(us._universe_impl(
            action="set_premise",
            universe_id="private",
            text="Allowed update.",
        ))

        assert out["status"] == "updated"
        assert (universe_base / "private" / "PROGRAM.md").read_text(
            encoding="utf-8",
        ) == "Allowed update."

    def test_private_universe_allows_owner_admin_write(self, universe_base):
        _make_private_universe(universe_base, "private")
        grant_universe_access(
            universe_base,
            universe_id="private",
            actor_id="owner",
            permission="admin",
            granted_by="owner",
        )

        _authenticate("owner", ["tinyassets.universe.write"])

        out = json.loads(us._universe_impl(
            action="set_premise",
            universe_id="private",
            text="Owner update.",
        ))

        assert out["status"] == "updated"
        assert (universe_base / "private" / "PROGRAM.md").read_text(
            encoding="utf-8",
        ) == "Owner update."

    def test_list_filters_private_universes_without_grant(self, universe_base):
        _make_universe(universe_base, "public")
        _make_private_universe(universe_base, "private")
        grant_universe_access(
            universe_base,
            universe_id="private",
            actor_id="owner",
            permission="admin",
            granted_by="owner",
        )

        _authenticate("intruder", ["tinyassets.universe.read"])

        out = json.loads(us._universe_impl(action="list"))

        assert [row["id"] for row in out["universes"]] == ["public"]


class TestUniverseWriteBoundaryBeyondUniverseTool:
    """Slice 3: the ACL write boundary is enforced on every universe-scoped
    write surface, not just the `universe` tool. `wiki`, branch runs, and
    auto-ship ledger/PR writes all delegate to `tinyassets.api.permissions`.
    """

    def test_wiki_write_to_unowned_universe_is_denied(self, universe_base):
        import tinyassets.api.wiki as wiki_mod

        _make_universe(universe_base, "other")
        _authenticate("alice", ["tinyassets.wiki.write"])

        out = json.loads(wiki_mod.wiki(
            action="write",
            universe_id="other",
            category="notes",
            filename="entry.md",
            content="should not land",
        ))

        assert out["error"] == "universe_access_denied"
        assert out["surface"] == "wiki"
        assert out["required_permission"] == "write"
        assert not (
            universe_base / "other" / "wiki" / "drafts" / "notes" / "entry.md"
        ).exists()
        # A denied write must have NO filesystem side effect — it must not have
        # scaffolded the target universe's wiki tree.
        assert not (universe_base / "other" / "wiki").exists()

    def test_wiki_read_of_private_universe_denied_without_scaffold(
        self, universe_base,
    ):
        import tinyassets.api.wiki as wiki_mod

        _make_private_universe(universe_base, "priv")
        # anonymous read of a private universe's wiki is denied (public_read=False)
        out = json.loads(wiki_mod.wiki(
            action="read", universe_id="priv", page="index",
        ))
        assert out["error"] == "universe_access_denied"
        assert out["surface"] == "wiki"
        assert out["required_permission"] == "read"
        # denied read must not scaffold the private universe's wiki tree
        assert not (universe_base / "priv" / "wiki").exists()

    def test_authenticated_human_cannot_run_branch_without_universe_context(
        self,
        universe_base,
    ):
        from tinyassets.api import runs

        _authenticate("alice", ["tinyassets.extensions.costly"])

        out = json.loads(runs._dispatch_run_action(
            "run_branch",
            lambda kwargs: json.dumps({
                "run_id": "should-not-run",
                "status": "queued",
                "error": "",
            }),
            {"branch_def_id": "branch-1"},
        ))

        assert out["error"] == "branch_run_requires_universe"

    def test_branch_run_to_unowned_universe_is_denied(self, universe_base):
        from tinyassets.api import runs

        _make_universe(universe_base, "other")
        _authenticate("alice", ["tinyassets.extensions.costly"])

        out = json.loads(runs._dispatch_run_action(
            "run_branch",
            lambda kwargs: json.dumps({
                "run_id": "should-not-run",
                "status": "queued",
                "error": "",
            }),
            {"branch_def_id": "branch-1", "universe_id": "other"},
        ))

        assert out["error"] == "universe_access_denied"
        assert out["surface"] == "extensions"
        assert out["required_permission"] == "write"

    def test_run_branch_via_extensions_forwards_universe_id_to_gate(
        self,
        universe_base,
    ):
        # Regression: the real MCP route (_extensions_impl) must forward
        # universe_id into run_kwargs so _branch_run_scope_error can gate it.
        # If universe_id were dropped, an authenticated caller would get
        # `branch_run_requires_universe`; asserting universe_access_denied
        # proves the id reached the gate.
        from tinyassets.api.extensions import _extensions_impl

        _make_universe(universe_base, "other")
        _authenticate("alice", ["tinyassets.extensions.costly"])

        out = json.loads(_extensions_impl(
            action="run_branch",
            universe_id="other",
            branch_def_id="branch-1",
        ))

        assert out["error"] == "universe_access_denied"
        assert out["surface"] == "extensions"
        assert out["required_permission"] == "write"

    def test_branch_run_with_owned_universe_uses_universe_actor(
        self,
        universe_base,
    ):
        from tinyassets.api import runs

        _make_universe(universe_base, "mine")
        grant_universe_access(
            universe_base,
            universe_id="mine",
            actor_id="alice",
            permission="admin",
            granted_by="alice",
        )
        _authenticate("alice", ["tinyassets.extensions.costly"])
        seen: dict[str, str] = {}

        def handler(kwargs):
            seen["actor"] = runs._run_actor_for_kwargs(kwargs)
            return json.dumps({
                "run_id": "run-1",
                "status": "queued",
                "error": "",
            })

        out = json.loads(runs._dispatch_run_action(
            "run_branch",
            handler,
            {"branch_def_id": "branch-1", "universe_id": "mine"},
        ))

        assert out["run_id"] == "run-1"
        assert seen["actor"] == "universe:mine"

    def test_auto_ship_ledger_write_to_unowned_universe_is_denied(
        self,
        universe_base,
        monkeypatch,
    ):
        from tinyassets.api import auto_ship_actions

        _make_universe(universe_base, "other")
        _authenticate("alice", ["tinyassets.extensions.write"])
        monkeypatch.setattr(
            "tinyassets.auto_ship.validate_ship_request",
            lambda _packet: {
                "ship_status": "skipped",
                "would_open_pr": True,
                "validation_result": "passed",
                "violations": [],
                "rollback_handle": "",
                "dry_run": True,
            },
        )

        out = json.loads(auto_ship_actions._action_validate_ship_packet({
            "body_json": "{}",
            "record_in_ledger": True,
            "universe_id": "other",
        }))

        assert out["error"] == "universe_access_denied"
        assert out["surface"] == "extensions"
        assert out["required_permission"] == "write"
        assert not (universe_base / "other" / "auto_ship_attempts.jsonl").exists()


class TestRunReadVisibility:
    """Slice-4 review (Codex): run read surfaces must not expose a private
    universe's runs. get_run / get_run_output / list_runs gate by the run's
    universe (derived from actor `universe:<uid>`) via universe_access_allows.
    """

    def test_run_read_allowed_helper(self, universe_base):
        from tinyassets.api import runs

        _make_universe(universe_base, "pub")           # public by default
        _make_private_universe(universe_base, "priv")  # public_read=False
        assert runs._run_read_allowed({"actor": "universe:pub"}) is True
        assert runs._run_read_allowed({"actor": "universe:priv"}) is False
        # A run not bound to a universe is not private-universe data.
        assert runs._run_read_allowed({"actor": "host"}) is True

    def test_get_run_output_denied_for_private_universe(
        self, universe_base, monkeypatch,
    ):
        from tinyassets.api import runs

        _make_private_universe(universe_base, "priv")
        monkeypatch.setattr(
            "tinyassets.runs.get_run",
            lambda base, rid: {
                "run_id": rid, "actor": "universe:priv",
                "branch_def_id": "b", "status": "completed",
                "output": {"secret": "leak"},
            },
        )
        out = json.loads(runs._action_get_run_output({"run_id": "r1"}))
        assert out["error"] == "universe_access_denied"
        assert "leak" not in json.dumps(out)

    def test_get_run_output_allowed_for_public_universe(
        self, universe_base, monkeypatch,
    ):
        from tinyassets.api import runs

        _make_universe(universe_base, "pub")
        monkeypatch.setattr(
            "tinyassets.runs.get_run",
            lambda base, rid: {
                "run_id": rid, "actor": "universe:pub",
                "branch_def_id": "b", "status": "completed",
                "output": {"ok": "1"},
            },
        )
        out = json.loads(runs._action_get_run_output({"run_id": "r1"}))
        assert out.get("error") != "universe_access_denied"
        assert out.get("output", {}).get("ok") == "1"

    def test_list_runs_filters_private_universe(self, universe_base, monkeypatch):
        from tinyassets.api import runs

        _make_universe(universe_base, "pub")
        _make_private_universe(universe_base, "priv")
        monkeypatch.setattr(
            "tinyassets.runs.list_runs",
            lambda base, **kw: [
                {"run_id": "r-pub", "actor": "universe:pub", "branch_def_id": "b",
                 "run_name": "", "status": "completed"},
                {"run_id": "r-priv", "actor": "universe:priv", "branch_def_id": "b",
                 "run_name": "", "status": "completed"},
            ],
        )
        out = json.loads(runs._action_list_runs({}))
        ids = {r["run_id"] for r in out["runs"]}
        assert "r-pub" in ids
        assert "r-priv" not in ids

    def test_stream_and_wait_read_denied_for_private_universe(
        self, universe_base, monkeypatch,
    ):
        from tinyassets.api import runs

        _make_private_universe(universe_base, "priv")
        monkeypatch.setattr(
            "tinyassets.runs.get_run",
            lambda base, rid: {
                "run_id": rid, "actor": "universe:priv", "status": "running",
            },
        )
        for action in (runs._action_stream_run, runs._action_wait_for_run):
            out = json.loads(action({"run_id": "r1"}))
            assert out["error"] == "universe_access_denied"

    def test_cancel_run_denied_for_private_universe(
        self, universe_base, monkeypatch,
    ):
        from tinyassets.api import runs

        _make_private_universe(universe_base, "priv")
        monkeypatch.setattr(
            "tinyassets.runs.get_run",
            lambda base, rid: {
                "run_id": rid, "actor": "universe:priv", "status": "running",
            },
        )
        canceled = {"v": False}
        monkeypatch.setattr(
            "tinyassets.runs.request_cancel",
            lambda base, rid: canceled.__setitem__("v", True),
        )
        out = json.loads(runs._action_cancel_run({"run_id": "r1"}))
        assert out["error"] == "universe_access_denied"
        assert canceled["v"] is False  # mutation blocked

    def test_resume_run_denied_for_private_universe(
        self, universe_base, monkeypatch,
    ):
        from tinyassets.api import runs

        _make_private_universe(universe_base, "priv")
        monkeypatch.setattr(
            "tinyassets.runs.get_run",
            lambda base, rid: {
                "run_id": rid, "actor": "universe:priv", "status": "interrupted",
            },
        )
        out = json.loads(runs._action_resume_run({"run_id": "r1"}))
        assert out["error"] == "universe_access_denied"

    def test_attach_child_denied_for_private_universe(
        self, universe_base, monkeypatch,
    ):
        from tinyassets.api import runs

        _make_private_universe(universe_base, "priv")
        monkeypatch.setattr(
            "tinyassets.runs.get_run",
            lambda base, rid: {"run_id": rid, "actor": "universe:priv"},
        )
        attached = {"v": False}

        def _attach(*a, **k):
            attached["v"] = True
            return {}

        monkeypatch.setattr("tinyassets.runs.attach_existing_child_run", _attach)
        out = json.loads(runs._action_attach_existing_child_run(
            {"run_id": "r1", "child_run_id": "c1"},
        ))
        assert out["error"] == "universe_access_denied"
        assert attached["v"] is False

    def test_record_receipt_denied_for_private_universe(
        self, universe_base, monkeypatch,
    ):
        from tinyassets.api import runs

        _make_private_universe(universe_base, "priv")
        monkeypatch.setattr(
            "tinyassets.runs.get_run",
            lambda base, rid: {"run_id": rid, "actor": "universe:priv"},
        )
        recorded = {"v": False}

        def _rec(*a, **k):
            recorded["v"] = True
            return {}

        monkeypatch.setattr("tinyassets.runs.record_run_receipt", _rec)
        out = json.loads(runs._action_record_run_receipt({
            "run_id": "r1", "receipt_type": "t", "payload_json": "{}",
        }))
        assert out["error"] == "universe_access_denied"
        assert recorded["v"] is False

    def test_routing_evidence_filters_private_universe(
        self, universe_base, monkeypatch,
    ):
        from tinyassets.api import runs

        _make_universe(universe_base, "pub")
        _make_private_universe(universe_base, "priv")
        monkeypatch.setattr(
            "tinyassets.runs.list_recent_runs",
            lambda base, **kw: [
                {"run_id": "r-pub", "actor": "universe:pub",
                 "status": "completed", "caveat": "c"},
                {"run_id": "r-priv", "actor": "universe:priv",
                 "status": "completed", "caveat": "c"},
            ],
        )
        out = json.loads(runs._action_run_routing_evidence({}))
        ids = {r["run_id"] for r in out["runs"]}
        assert "r-pub" in ids
        assert "r-priv" not in ids

    def test_query_runs_wires_read_filter(self, universe_base, monkeypatch):
        from tinyassets.api import runs

        _make_universe(universe_base, "pub")
        _make_private_universe(universe_base, "priv")
        captured = {}

        def _fake(base, *, row_filter=None, **kw):
            captured["f"] = row_filter
            return {"rows": [], "count": 0}

        monkeypatch.setattr("tinyassets.runs.query_runs", _fake)
        runs._action_query_runs({})
        f = captured["f"]
        assert f is not None
        # Real rows always carry queue_universe_id (query_runs selects it): an
        # owner-actor background run is gated by the universe it ran in.
        assert f({"actor": "universe:pub", "queue_universe_id": None}) is True
        assert f({"actor": "universe:priv", "queue_universe_id": None}) is False
        assert f({"actor": "host", "queue_universe_id": None}) is True
        assert f({"actor": "acct_owner", "queue_universe_id": "priv"}) is False
        assert f({"actor": "acct_owner", "queue_universe_id": "pub"}) is True

    def test_get_status_of_private_universe_denied(self, universe_base):
        from tinyassets.api.status import get_status

        _make_private_universe(universe_base, "priv")
        out = json.loads(get_status(universe_id="priv"))
        assert out["error"] == "universe_access_denied"
        assert out["required_permission"] == "read"

    def test_get_status_of_public_universe_allowed(self, universe_base):
        from tinyassets.api.status import get_status

        _make_universe(universe_base, "pub")
        out = json.loads(get_status(universe_id="pub"))
        assert out.get("error") != "universe_access_denied"

    def test_list_run_receipts_filters_private_in_all_modes(
        self, universe_base, monkeypatch,
    ):
        from tinyassets.api import runs

        _make_universe(universe_base, "pub")
        _make_private_universe(universe_base, "priv")
        monkeypatch.setattr(
            "tinyassets.runs.list_run_receipts",
            lambda base, **kw: [
                {"run_id": "r-pub", "receipt_type": "t"},
                {"run_id": "r-priv", "receipt_type": "t"},
            ],
        )
        monkeypatch.setattr(
            "tinyassets.runs.get_run",
            lambda base, rid: {
                "r-pub": {"run_id": "r-pub", "actor": "universe:pub"},
                "r-priv": {"run_id": "r-priv", "actor": "universe:priv"},
            }.get(rid),
        )
        # no run_id (enumeration mode) — private receipt must be filtered out
        out = json.loads(runs._action_list_run_receipts({}))
        rids = {r["run_id"] for r in out["receipts"]}
        assert "r-pub" in rids
        assert "r-priv" not in rids

    def test_get_memory_scope_status_private_denied(self, universe_base):
        from tinyassets.api import runs

        _make_private_universe(universe_base, "priv")
        out = json.loads(runs._action_get_memory_scope_status({"universe_id": "priv"}))
        assert out["error"] == "universe_access_denied"


class TestScopeHeader:
    """#15: the dispatcher wraps every universe-scoped response with a
    phone-legible `Universe: <id>` `text` lead-in, puts `universe_id`
    first, and leaves everything else structurally intact.
    """

    def test_dispatch_injects_text_header_on_read(self, universe_base):
        _make_universe(universe_base, "alpha")
        (universe_base / "alpha" / "PROGRAM.md").write_text(
            "An alpha premise.", encoding="utf-8",
        )
        out = json.loads(us._dispatch_with_ledger(
            "read_premise",
            us._action_read_premise,
            {"universe_id": "alpha"},
        ))
        assert "text" in out
        assert out["text"].startswith("Command center: alpha")
        assert out["premise"] == "An alpha premise."

    def test_universe_id_is_first_key(self, universe_base):
        _make_universe(universe_base, "alpha")
        out_str = us._dispatch_with_ledger(
            "read_premise",
            us._action_read_premise,
            {"universe_id": "alpha"},
        )
        out = json.loads(out_str)
        first_key = next(iter(out.keys()))
        assert first_key == "universe_id"

    def test_text_header_on_write_path(self, universe_base):
        _make_universe(universe_base, "alpha")
        out = json.loads(us._dispatch_with_ledger(
            "set_premise",
            us._action_set_premise,
            {"universe_id": "alpha", "text": "Fresh premise."},
        ))
        assert out["universe_id"] == "alpha"
        assert "text" in out
        assert out["text"].startswith("Command center: alpha")
        assert out["status"] == "updated"

    def test_error_without_universe_id_is_unchanged(self, universe_base):
        # An error response with no universe_id must NOT get a fake scope
        # header — we don't want to falsely claim a universe.
        out = json.loads(us._dispatch_with_ledger(
            "set_premise",
            us._action_set_premise,
            {"universe_id": "alpha", "text": ""},  # empty → error
        ))
        assert "error" in out
        if "text" in out:
            assert not out["text"].startswith("Command center: ")

    def test_multi_universe_list_not_scoped(self, universe_base):
        # list_universes returns a multi-universe response with no
        # single universe_id — must not get a scope header.
        _make_universe(universe_base, "alpha")
        _make_universe(universe_base, "beta")
        out = json.loads(us._dispatch_with_ledger(
            "list",
            us._action_list_universes,
            {},
        ))
        assert "universes" in out
        if "text" in out:
            assert not out["text"].startswith("Command center: ")

    def test_existing_text_field_preserved_under_header(self):
        # If a handler already emits a `text` field, the helper prepends
        # the header rather than clobbering it.
        fake = json.dumps({"universe_id": "alpha", "text": "Prior prose."})
        wrapped = json.loads(us._scope_universe_response(fake))
        assert wrapped["text"].startswith("Command center: alpha")
        assert "Prior prose." in wrapped["text"]

    def test_preserves_all_other_fields(self, universe_base):
        udir = _make_universe(universe_base, "alpha")
        (udir / "canon").mkdir()
        (udir / "canon" / "a.md").write_text("x", encoding="utf-8")
        out = json.loads(us._dispatch_with_ledger(
            "list_canon",
            us._action_list_canon,
            {"universe_id": "alpha"},
        ))
        assert out["universe_id"] == "alpha"
        assert out["count"] == 1
        assert out["canon_files"][0]["filename"] == "a.md"

    def test_non_universe_scoped_response_unchanged(self):
        # `_scope_universe_response` should leave dicts without
        # universe_id alone.
        payload = json.dumps({"branches": [], "count": 0})
        out = us._scope_universe_response(payload)
        assert json.loads(out) == {"branches": [], "count": 0}

    def test_non_json_response_unchanged(self):
        out = us._scope_universe_response("not json at all")
        assert out == "not json at all"
