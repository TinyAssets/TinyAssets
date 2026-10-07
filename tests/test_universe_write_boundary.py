"""Executable acceptance gate for the founder/universe write boundary (D0a).

Encodes the invariant from the founder/universe identity design — see
``docs/design-notes/2026-06-26-founder-and-universe-identity.md`` (decision
D0a) and ``openspec/changes/archive/2026-08-26-universe-creation`` requirement *"MCP writes are
scoped to the founder's own command center"*:

    A universe created through ``universe action=create_universe`` is OWNED by
    the founder who created it. Another authenticated founder — even one
    holding the ``tinyassets.universe.write`` scope — MUST NOT be able to
    write that universe's brain.

Status (Claude, 2026-06-30 — ACL-synthesis slice): the invariant is now
ENFORCED. ``_action_create_universe`` grants the authenticated founder an
``admin`` ACL row on create (D0a founder-grant-on-create), and the single ACL
path in ``tinyassets.api.permissions`` denies writes to any actor without a
``write``/``admin`` grant. The two cross-founder gates below therefore now
PASS as permanent regression guards; their prior ``xfail(strict=True)`` markers
were removed once D0a landed (as their design note prescribed — an xpass under
strict was the signal to promote them to hard guards).

The anonymous guard is satisfied by the scope gate, which blocks all anonymous
writes when auth is required; it has always been a plain green guard.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tinyassets.api.universe as us
from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity
from tinyassets.daemon_server import universe_access_permission


class _StaticAuthProvider(AuthProvider):
    """Auth-required provider that resolves the bearer token ``"ok"`` to a
    fixed identity, mirroring tests/test_universe_server_isolation.py."""

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


# Full founder scope set: read + write + admin + costly (create_universe is a
# costly action, so the creating founder needs the costly scope).
_FOUNDER_SCOPES = [
    "tinyassets.universe.read",
    "tinyassets.universe.write",
    "tinyassets.universe.admin",
    "tinyassets.universe.costly",
]


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


def _authenticate(user_id: str, scopes: list[str]) -> None:
    identity = Identity(
        user_id=user_id,
        username=user_id,
        capabilities=list(scopes),
    )
    set_provider(_StaticAuthProvider(identity))
    auth_middleware("ok")


def _authenticate_anonymous() -> None:
    """Auth REQUIRED, but no valid token → identity resolves to ANONYMOUS."""
    set_provider(_StaticAuthProvider(None))
    auth_middleware(None)


def _create_universe_as(
    founder: str, _legacy_uid: str, text: str = "A founder seed."
) -> dict:
    _authenticate(founder, _FOUNDER_SCOPES)
    return json.loads(us._universe_impl(
        action="create_universe",
        text=text,
    ))


class TestFounderWriteBoundary:
    """A founder owns the universe they create; other founders cannot write it."""

    def test_created_universe_not_writable_by_other_founder(self, universe_base):
        created = _create_universe_as("alice", "u-acceptance-alice")
        assert created.get("status") == "created", created
        target = created["universe_id"]

        # A *different* authenticated founder, holding the write scope but with
        # no grant on alice's universe, must be denied.
        _authenticate("mallory", ["tinyassets.universe.write"])
        out = json.loads(us._universe_impl(
            action="set_premise",
            universe_id=target,
            text="Hostile cross-founder overwrite.",
        ))

        assert out.get("error") == "universe_access_denied", out
        assert out.get("required_permission") == "write"

    def test_create_universe_grants_founder_owner_acl(self, universe_base):
        created = _create_universe_as("alice", "u-acceptance-owner")
        assert created.get("status") == "created", created
        target = created["universe_id"]

        # The founder must hold an owner-grade (write/admin) ACL on their own
        # created universe — the mechanism that makes the write boundary real.
        # On a zero-ACL universe this returns the public "read" convention.
        perm = universe_access_permission(
            universe_base,
            universe_id=target,
            actor_id="alice",
        )
        assert perm in {"write", "admin"}, (
            f"founder 'alice' has permission {perm!r} on her own created "
            "universe; expected an owner (write/admin) grant"
        )

    def test_created_universe_rejects_anonymous_write(self, universe_base):
        # Green guard: already enforced on origin/main in auth-required mode —
        # the scope gate blocks every anonymous write regardless of ownership.
        created = _create_universe_as("alice", "u-acceptance-anon")
        assert created.get("status") == "created", created
        target = created["universe_id"]

        _authenticate_anonymous()
        out = json.loads(us._universe_impl(
            action="set_premise",
            universe_id=target,
            text="Anonymous overwrite.",
        ))

        assert "error" in out, out
        # Denied — never an accepted write.
        assert out.get("status") != "updated", out


class TestPrivateCanonRelay:
    """Relay reshape (2026-07-02, design note §13/§14): the chatbot does NOT
    write a universe's private canon — the universe's OWN intelligence does. A
    page write that targets a universe is RELAYED (``relay_to_command_center``), never
    written here, so the brain stays one coherent mind whether reached via app
    or chatbot. Issue filings (``kind=``) and no-target writes stay on the
    shared commons."""

    def test_founder_page_write_is_relayed_not_written(self, universe_base):
        from tinyassets.universe_server import write_page

        created = _create_universe_as("carol", "u-canon-carol")
        assert created.get("status") == "created", created
        uid = created["universe_id"]
        # carol is the founder (home = her universe) + holds wiki write scope —
        # yet the chatbot still must not write her private canon directly.
        _authenticate(
            "carol",
            _FOUNDER_SCOPES + ["tinyassets.wiki.write", "tinyassets.wiki.read"],
        )
        out = json.loads(write_page(
            category="lore",
            filename="the-resonance",
            content="The Resonance links cells and bonds across Aurelith.",
            dry_run=False,
        ))
        # Relayed to the universe — the chatbot did not write the brain.
        assert out.get("status") == "relay_to_command_center", out
        assert out.get("universe_id") == uid, out
        assert out.get("relay", {}).get("content"), out
        # Nothing landed in the universe's own wiki…
        universe_hits = list(
            (universe_base / uid / "wiki").rglob("the-resonance.md")
        )
        assert not universe_hits, f"chatbot wrote private canon: {universe_hits}"
        # …nor leaked to the shared commons.
        commons = universe_base / "wiki"
        commons_hits = (
            list(commons.rglob("the-resonance.md")) if commons.is_dir() else []
        )
        assert not commons_hits, f"private canon leaked to commons: {commons_hits}"

    def test_founder_can_explicitly_write_shared_commons(
        self, universe_base, monkeypatch
    ):
        from tinyassets.api import helpers
        from tinyassets.universe_server import write_page

        monkeypatch.setattr(
            helpers, "_request_universe", lambda _requested: "u-commons-carol"
        )
        _authenticate(
            "carol",
            _FOUNDER_SCOPES + ["tinyassets.wiki.write", "tinyassets.wiki.read"],
        )

        out = json.loads(write_page(
            scope="commons",
            category="notes",
            filename="shared-reference",
            content="A public reference for every universe.",
            dry_run=False,
        ))

        assert out.get("status") != "relay_to_command_center", out
        commons_hits = list(
            (universe_base / "wiki").rglob("shared-reference.md")
        )
        assert commons_hits, out
        universe_hits = list(
            (universe_base / "u-commons-carol" / "wiki").rglob(
                "shared-reference.md"
            )
        )
        assert not universe_hits

    def test_explicit_universe_scope_uses_the_sole_writer_relay(
        self, universe_base
    ):
        from tinyassets.universe_server import write_page

        _authenticate(
            "carol",
            _FOUNDER_SCOPES + ["tinyassets.wiki.write", "tinyassets.wiki.read"],
        )

        out = json.loads(write_page(
            scope="command_center",
            command_center_id="u-relay-carol",
            category="lore",
            filename="private-reference",
            content="This belongs to the universe's learned mind.",
            dry_run=False,
        ))

        assert out.get("status") == "relay_to_command_center", out
        assert out.get("universe_id") == "u-relay-carol", out
        assert out.get("relay", {}).get("content"), out

    def test_explicit_universe_scope_without_a_target_fails_closed(
        self, universe_base, monkeypatch
    ):
        from tinyassets.api import helpers
        from tinyassets.universe_server import write_page

        monkeypatch.setattr(helpers, "_request_universe", lambda _requested: "")
        _authenticate(
            "carol",
            _FOUNDER_SCOPES + ["tinyassets.wiki.write", "tinyassets.wiki.read"],
        )

        out = json.loads(write_page(
            scope="command_center",
            category="notes",
            filename="must-not-exist",
            content="No universe target resolved.",
            dry_run=False,
        ))

        assert out["error"] == (
            "scope=command_center requires command_center_id or a founder home"
        )
        assert not list(universe_base.rglob("must-not-exist.md"))

    def test_universe_scope_rejects_a_commons_filing(
        self, universe_base
    ):
        from tinyassets.universe_server import write_page

        _authenticate(
            "carol",
            _FOUNDER_SCOPES + ["tinyassets.wiki.write", "tinyassets.wiki.read"],
        )

        out = json.loads(write_page(
            scope="command_center",
            command_center_id="u-relay-carol",
            kind="bug",
            title="must not file",
        ))

        assert out["error"] == "scope=command_center cannot be combined with kind"
        assert not list(universe_base.rglob("*.md"))

    @pytest.mark.parametrize("scope", ["elsewhere", " COMMONS "])
    def test_unknown_scope_fails_closed_before_mutation(
        self, universe_base, scope
    ):
        from tinyassets.universe_server import write_page

        _authenticate(
            "carol",
            _FOUNDER_SCOPES + ["tinyassets.wiki.write", "tinyassets.wiki.read"],
        )

        out = json.loads(write_page(
            scope=scope,
            category="notes",
            filename="must-not-exist",
            content="This target is invalid.",
            dry_run=False,
        ))

        assert out["error"] == (
            "scope must be one of: commons, command_center"
        )
        assert not list(universe_base.rglob("must-not-exist.md"))

    def test_commons_scope_rejects_a_simultaneous_universe_target(
        self, universe_base
    ):
        from tinyassets.universe_server import write_page

        _authenticate(
            "carol",
            _FOUNDER_SCOPES + ["tinyassets.wiki.write", "tinyassets.wiki.read"],
        )

        out = json.loads(write_page(
            scope="commons",
            command_center_id="u-contradictory",
            category="notes",
            filename="must-not-exist",
            content="Contradictory targets must not mutate.",
            dry_run=False,
        ))

        assert out["error"] == (
            "scope=commons cannot be combined with command_center_id"
        )
        assert not list(universe_base.rglob("must-not-exist.md"))

    def test_issue_filing_stays_on_commons_not_founder_home(self, universe_base):
        from tinyassets.universe_server import write_page

        created = _create_universe_as("dave", "u-commons-dave")
        assert created.get("status") == "created", created
        uid = created["universe_id"]
        _authenticate(
            "dave",
            _FOUNDER_SCOPES + ["tinyassets.wiki.write", "tinyassets.wiki.read"],
        )
        out = json.loads(write_page(
            kind="bug",
            title="Widget crashes on save",
            component="widget",
            severity="major",
            repro="open widget, click save",
            observed="crash",
            expected="saves cleanly",
        ))
        assert "error" not in out, out
        # The bug filing did NOT land in dave's private universe wiki.
        home_bugs_dir = universe_base / uid / "wiki" / "pages" / "bugs"
        home_bugs = (
            list(home_bugs_dir.rglob("*.md")) if home_bugs_dir.is_dir() else []
        )
        assert not home_bugs, (
            f"issue filing leaked into founder's universe: {home_bugs}"
        )

    def test_anonymous_page_write_does_not_resolve_to_a_universe(
        self, universe_base
    ):
        # Anonymous/dev callers have no founder home to resolve to, so a plain
        # page write is a shared-commons write — never a universe brain.
        from tinyassets.universe_server import write_page

        created = _create_universe_as("erin", "u-anon-guard-erin")
        _authenticate_anonymous()
        write_page(
            category="lore",
            filename="stray-note",
            content="An anonymous stray note.",
            dry_run=False,
        )
        erin_hits = list(
            (
                universe_base
                / created["universe_id"]
                / "wiki"
            ).rglob("stray-note.md")
        )
        assert not erin_hits, f"anonymous write leaked into a founder universe: {erin_hits}"

    def test_page_write_never_writes_a_universe_brain(self, universe_base):
        # A founder can never get private canon written into ANY universe brain
        # via the chatbot — not their own (relayed) and not one they do not own.
        from tinyassets.universe_server import write_page

        created = _create_universe_as("alice", "u-owned-by-alice")
        assert created.get("status") == "created", created
        # frank owns nothing; his page write must not land in alice's universe.
        _authenticate(
            "frank",
            _FOUNDER_SCOPES + ["tinyassets.wiki.write", "tinyassets.wiki.read"],
        )
        write_page(
            category="lore",
            filename="sneaky",
            content="frank owns nothing.",
            dry_run=False,
        )
        for udir in universe_base.glob("u-*"):
            hits = list((udir / "wiki").rglob("sneaky.md"))
            assert not hits, f"chatbot wrote into a universe brain: {hits}"


class TestOpenCommonsContribution:
    """Any authenticated contributor can publish and rediscover typed commons pages."""

    def test_ordinary_identity_writes_and_discovers_custom_agent_contribution(
        self, universe_base
    ):
        from tinyassets.universe_server import read_page, write_page

        _authenticate(
            "ordinary-contributor",
            ["tinyassets.wiki.read", "tinyassets.wiki.write"],
        )
        decoy = (
            universe_base
            / "wiki"
            / "drafts"
            / "aaa-decoys"
            / "remixable-agent-blueprint.md"
        )
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_text(
            "---\ntitle: Wrong same-slug page\ntype: note\n"
            "audience: coordination\n---\n\nThis is not the contribution.\n",
            encoding="utf-8",
        )
        content = (
            "---\n"
            "title: Remixable Agent Blueprint\n"
            "type: contribution\n"
            "audience: discovery\n"
            "updated: 2026-08-01T00:00:00Z\n"
            "contribution_kind: agent-definition\n"
            "related_goals: custom-agents, remix\n"
            "license_id: Apache-2.0\n"
            "provenance_class: original\n"
            "integrity_references: sha256:test-fixture\n"
            "---\n\n"
            "An interchange blueprint with remix ancestry for custom agents.\n"
        )

        written = json.loads(
            write_page(
                scope="commons",
                category="Agent Blueprints",
                filename="remixable-agent-blueprint",
                content=content,
                dry_run=False,
            )
        )
        assert written["path"] == (
            "drafts/agent-blueprints/remixable-agent-blueprint.md"
        )

        searched = json.loads(read_page(query="interchange remix ancestry"))
        paths = {item["path"] for item in searched["results"]}
        assert written["path"] in paths

        changed = json.loads(read_page(changed_since="2026-07-31T00:00:00Z"))
        changed_paths = {item["path"] for item in changed["results"]}
        assert written["path"] in changed_paths

        reread = json.loads(read_page(page=written["path"]))
        assert reread["source_read_proof"]["path"] == written["path"]
        assert "title: Remixable Agent Blueprint" in reread["content"]
        assert "contribution_kind: agent-definition" in reread["content"]
        assert "Wrong same-slug page" not in reread["content"]

    @pytest.mark.parametrize(
        ("requested_path", "stored_path"),
        [
            ("../outside.md", "outside.md"),
            ("raw/private-source.md", "wiki/raw/private-source.md"),
            ("drafts/../../outside.md", "outside.md"),
            (
                "drafts/notes/private-source.txt",
                "wiki/drafts/notes/private-source.txt",
            ),
            (
                "pages/custom/extensionless-entry",
                "wiki/pages/custom/extensionless-entry.md",
            ),
            (
                "drafts/custom/extensionless-entry",
                "wiki/drafts/custom/extensionless-entry.md",
            ),
        ],
    )
    def test_exact_page_paths_stay_within_public_markdown_roots(
        self, universe_base, requested_path, stored_path
    ):
        from tinyassets.universe_server import read_page

        _authenticate("ordinary-reader", ["tinyassets.wiki.read"])
        private_source = universe_base / stored_path
        private_source.parent.mkdir(parents=True, exist_ok=True)
        private_source.write_text("private source", encoding="utf-8")

        result = json.loads(read_page(page=requested_path))

        assert result["error"] == f"Page not found: {requested_path}"
