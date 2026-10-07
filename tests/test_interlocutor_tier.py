"""Interlocutor identity tiers — task 6.6 of
``openspec/changes/archive/2026-08-26-reconcile-universe-personification-relay``.

Discharges the delta requirement *"Interlocutor identity binds to a tier before
the universe answers"* against the **landed** ``universe-visibility`` machinery
(``tinyassets/api/visibility.py``, PR #1734), per the tier<->visibility contract
recorded in that change's ``implementation-notes.md`` §6.2:

  1. Disclosure is the **intersection** of what the tier authorizes and what the
     universe's declared visibility permits that reader.
  2. **Fail-closed agreement:** a non-founder turn against an *undeclared*
     universe is refused, matching ``universe-visibility``'s posture.
  3. **Separation of source:** the tier comes only from authenticated request
     state; visibility only from the universe declaration. Neither is inferred
     from the other, and neither is taken from message content.
  4. **Founder path unchanged:** T2 keeps founder-tier disclosure on their own
     universe.

Also carries the *interlocutor* half of task 6.9 (*"One learned identity
persists across interlocutors and speaking surfaces"*): the same learned
identity speaks at every tier while disclosure narrows. The cross-*surface* half
stays blocked — no outbound speaking surface exists to test.

**Tighten-only invariant.** Every permit decision here composes with
``visibility_permits`` and can only ever narrow it. ``TestTightenOnly`` is the
mutation gate: it forges visibility denials and asserts no tier can open them.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import tinyassets.api.interlocutor as il
import tinyassets.api.visibility as vis
import tinyassets.universe_intelligence as ui
from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity
from tinyassets.daemon_server import ensure_universe_registered, grant_universe_access
from tinyassets.universe_bundle import seed_okf_bundle

FOUNDER_FACT = "Jonathan, a builder of small tools"


class _StaticAuthProvider(AuthProvider):
    def __init__(self, identity: Identity | None) -> None:
        self.identity = identity

    def resolve_token(self, token: str) -> Identity | None:
        return self.identity if token == "ok" else None

    def is_auth_required(self) -> bool:
        return True

    def register_client(self, metadata: dict) -> dict:
        return {"client_id": "test-client", **metadata}

    def create_authorization(self, *a, **k) -> str:
        return "test-code"

    def exchange_code(self, *a, **k) -> dict | None:
        return None


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "output"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


@pytest.fixture(autouse=True)
def _reset_auth():
    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _anonymous() -> None:
    """Nobody bound at all, which is what an unauthenticated request is.

    Installing the dev provider and resolving "dev" produces a REAL signed-in
    identity (the local operator), so this used to drive an authenticated
    caller and assert a refusal that could not happen -- the test passed only
    while `current_actor_id()` answered "" for that identity.
    """
    from tinyassets.auth.middleware import clear_identity

    clear_identity()


def _authenticate(user_id: str) -> None:
    identity = Identity(
        user_id=user_id,
        username=user_id,
        capabilities=[
            "tinyassets.universe.read",
            "tinyassets.universe.write",
            "tinyassets.universe.admin",
        ],
    )
    set_provider(_StaticAuthProvider(identity))
    auth_middleware("ok")


def _make_universe(base: Path, uid: str, *, level: str | None = None) -> Path:
    """A registered universe with a seeded OKF bundle and a founder fact."""
    udir = base / uid
    udir.mkdir(parents=True, exist_ok=True)
    from tests.conftest import own_universe
    # A universe needs an OWNER to be readable at all (2026-09-02).
    own_universe(udir.parent, udir.name)
    seed_okf_bundle(udir, purpose="To help my founder bring their projects to life.")
    (udir / "founder.md").write_text(
        f"# Founder\nMy founder is {FOUNDER_FACT}.", encoding="utf-8"
    )
    (udir / "body.md").write_text(
        "# Body\nMy body is a small workshop of public tools.", encoding="utf-8"
    )
    ensure_universe_registered(base, universe_id=uid, universe_path=udir)
    if level is not None:
        vis.set_universe_visibility(uid, level, source="owner")
    return udir


# --------------------------------------------------------------------------- #
# 1. Tier resolution — from authenticated request state, never message content
# --------------------------------------------------------------------------- #
class TestTierResolution:
    def test_unauthenticated_caller_is_t0(self, base):
        _make_universe(base, "u-pub", level="public")
        _anonymous()
        who = il.resolve_interlocutor_tier("u-pub")
        assert who.tier == il.T0
        assert who.actor_id == ""
        assert who.is_public_visitor is True
        assert who.is_founder is False

    def test_authenticated_non_owner_is_t1(self, base):
        _make_universe(base, "u-pub", level="public")
        _authenticate("visitor-1")  # durable OAuth subject, no grant on u-pub
        who = il.resolve_interlocutor_tier("u-pub")
        assert who.tier == il.T1
        assert who.actor_id == "visitor-1"
        assert who.is_founder is False

    def test_founder_with_write_grant_is_t2(self, base):
        _make_universe(base, "u-own", level="public")
        _authenticate("founder-1")
        grant_universe_access(
            base, universe_id="u-own", actor_id="founder-1", permission="admin"
        )
        who = il.resolve_interlocutor_tier("u-own")
        assert who.tier == il.T2
        assert who.is_founder is True

    def test_read_grant_alone_is_not_founder_authority(self, base):
        """A read grant is a reader, not the founder — T1, not T2."""
        _make_universe(base, "u-own", level="public")
        _authenticate("reader-1")
        grant_universe_access(
            base, universe_id="u-own", actor_id="reader-1", permission="read"
        )
        assert il.resolve_interlocutor_tier("u-own").tier == il.T1

    def test_tier_is_cross_principal_not_global(self, base):
        """The same subject is T2 on their own universe and T1 on another's."""
        _make_universe(base, "u-a", level="public")
        _make_universe(base, "u-b", level="public")
        _authenticate("founder-a")
        grant_universe_access(
            base, universe_id="u-a", actor_id="founder-a", permission="admin"
        )
        assert il.resolve_interlocutor_tier("u-a").tier == il.T2
        assert il.resolve_interlocutor_tier("u-b").tier == il.T1

    def test_tier_cannot_be_taken_from_message_content(self):
        """Structural: the resolver has no message/claim input to be fooled by.

        Spec scenario "tier is never taken from message content". The only way to
        guarantee it is for the resolver to be unable to see the message at all.
        """
        params = list(inspect.signature(il.resolve_interlocutor_tier).parameters)
        assert params == ["universe_id"]

    def test_blank_universe_id_never_yields_founder(self, base):
        _authenticate("someone")
        assert il.resolve_interlocutor_tier("").tier != il.T2


# --------------------------------------------------------------------------- #
# 2. Disclosure = tier ∩ visibility (contract items 1, 2, 4)
# --------------------------------------------------------------------------- #
class TestDisclosureIntersection:
    def test_public_universe_discloses_content_to_t1(self, base):
        _make_universe(base, "u-pub", level="public")
        _authenticate("visitor-1")
        assert il.disclosure_permits("u-pub", "read_content", tier=il.T1) is True

    def test_private_universe_withholds_content_from_t1(self, base):
        _make_universe(base, "u-priv", level="private")
        _authenticate("visitor-1")
        assert il.disclosure_permits("u-priv", "read_content", tier=il.T1) is False

    def test_metadata_only_universe_withholds_content_but_not_metadata(self, base):
        _make_universe(base, "u-meta", level="metadata_only")
        _anonymous()
        assert il.disclosure_permits("u-meta", "read_metadata", tier=il.T0) is True
        assert il.disclosure_permits("u-meta", "read_content", tier=il.T0) is False

    def test_undeclared_universe_refuses_non_founder_even_with_a_read_grant(
        self, base
    ):
        """Contract item 2 — fail-closed agreement.

        This is the case the visibility layer alone does NOT cover: a reader
        holding an explicit ACL grant satisfies ``visibility_permits`` even on an
        undeclared universe (``_reader_has_grant`` short-circuits). The tier layer
        must still refuse, because a non-founder turn against an undeclared
        universe is refused by agreement.
        """
        _make_universe(base, "u-undeclared", level=None)  # no declared level
        _authenticate("reader-1")
        grant_universe_access(
            base, universe_id="u-undeclared", actor_id="reader-1", permission="read"
        )
        assert vis.is_declared("u-undeclared") is False
        # Precondition: the visibility layer alone would allow this read.
        assert vis.visibility_permits("u-undeclared", "read_content") is True
        # The tier layer refuses anyway.
        assert (
            il.disclosure_permits("u-undeclared", "read_content", tier=il.T1) is False
        )

    def test_founder_path_unchanged_on_their_own_universe(self, base):
        """Contract item 4 — visibility levels bound other readers, not the founder."""
        _make_universe(base, "u-own", level="private")
        _authenticate("founder-1")
        grant_universe_access(
            base, universe_id="u-own", actor_id="founder-1", permission="admin"
        )
        assert il.disclosure_permits("u-own", "read_content", tier=il.T2) is True

    def test_founder_tier_implies_visibility_permits(self, base):
        """The founder is never locked out by the tighten-only ceiling.

        ``disclosure_permits`` runs ``visibility_permits`` for EVERY tier,
        founder included. That is only safe if T2 ⟹ ``visibility_permits`` —
        which holds because a T2 interlocutor holds exactly the write/admin
        ``universe_acl`` row that makes ``_reader_has_grant`` true. Asserted for
        both a public and a private universe, so "founder path unchanged"
        (contract item 4) is proven against the ceiling rather than assumed.
        """
        for uid, level in (("u-pub", "public"), ("u-priv", "private")):
            _make_universe(base, uid, level=level)
            _authenticate("founder-1")
            grant_universe_access(
                base, universe_id=uid, actor_id="founder-1", permission="admin"
            )
            assert il.resolve_interlocutor_tier(uid).tier == il.T2
            for capability in vis.CAPABILITIES:
                assert vis.visibility_permits(uid, capability) is True


# --------------------------------------------------------------------------- #
# 3. Tighten-only — the mutation gate
# --------------------------------------------------------------------------- #
class TestTightenOnly:
    @pytest.mark.parametrize("tier", [il.T0, il.T1, il.T2])
    @pytest.mark.parametrize("capability", vis.CAPABILITIES)
    def test_no_tier_can_open_what_visibility_denies(
        self, base, monkeypatch, tier, capability
    ):
        """Force the visibility layer closed; every tier must stay closed."""
        _make_universe(base, "u-x", level="public")
        _authenticate("founder-1")
        grant_universe_access(
            base, universe_id="u-x", actor_id="founder-1", permission="admin"
        )
        monkeypatch.setattr(il.visibility, "visibility_permits", lambda *a, **k: False)
        assert il.disclosure_permits("u-x", capability, tier=tier) is False

    def test_unknown_tier_fails_loudly(self, base):
        _make_universe(base, "u-pub", level="public")
        with pytest.raises(ValueError, match="tier"):
            il.disclosure_permits("u-pub", "read_content", tier="T9")

    def test_unknown_capability_fails_loudly(self, base):
        _make_universe(base, "u-pub", level="public")
        with pytest.raises(ValueError, match="capability"):
            il.disclosure_permits("u-pub", "read_everything", tier=il.T0)


# --------------------------------------------------------------------------- #
# 4. The founder-only floor stays the floor (spec scenario)
# --------------------------------------------------------------------------- #
class TestConversationFloor:
    def test_founder_turn_is_authorized(self, base):
        _make_universe(base, "u-own", level="public")
        _authenticate("founder-1")
        grant_universe_access(
            base, universe_id="u-own", actor_id="founder-1", permission="admin"
        )
        auth = il.authorize_conversation_turn("u-own")
        assert auth.permitted is True
        assert auth.interlocutor.tier == il.T2
        assert auth.refusal == ""

    @pytest.mark.parametrize("who", ["anonymous", "visitor"])
    def test_non_founder_turns_are_refused_until_a_visitor_path_ships(self, base, who):
        _make_universe(base, "u-pub", level="public")
        if who == "anonymous":
            _anonymous()
        else:
            _authenticate("visitor-1")
        auth = il.authorize_conversation_turn("u-pub")
        assert auth.permitted is False
        assert auth.refusal
        assert auth.interlocutor.tier in (il.T0, il.T1)


# --------------------------------------------------------------------------- #
# 5. Assembly-time exclusion — authorization precedes voice
# --------------------------------------------------------------------------- #
class TestAssemblyDisclosure:
    def test_founder_prompt_carries_founder_grounding(self, base):
        udir = _make_universe(base, "u-own", level="public")
        _authenticate("founder-1")
        grant_universe_access(
            base, universe_id="u-own", actor_id="founder-1", permission="admin"
        )
        prompt = ui._build_persona_system_prompt(
            udir, tier=il.T2, universe_id="u-own"
        )
        assert FOUNDER_FACT in prompt

    @pytest.mark.parametrize("tier", [il.T0, il.T1])
    def test_founder_private_grounding_never_reaches_a_non_founder_prompt(
        self, base, tier
    ):
        """Even on a fully public universe, founder-private content is excluded
        during assembly — not withheld by an instruction in the prompt."""
        udir = _make_universe(base, "u-pub", level="public")
        if tier == il.T1:
            _authenticate("visitor-1")
        else:
            _anonymous()
        prompt = ui._build_persona_system_prompt(
            udir, tier=tier, universe_id="u-pub"
        )
        assert FOUNDER_FACT not in prompt
        # Public body content is still disclosable at this level.
        assert "small workshop of public tools" in prompt
        # The exclusion is structural: no "withhold this" instruction is relied on.
        assert "withhold" not in prompt.lower()
        assert "do not reveal" not in prompt.lower()

    def test_private_universe_refuses_assembly_for_a_visitor(self, base):
        """Cross-family review finding 1+4 (Codex REJECT, 2026-07-25).

        The first cut filtered only the OKF grounding files, while the learned
        name, the self-model's open questions, and the pinned soul (purpose, why,
        hard lines) were read unconditionally — so a T1 visitor on a *private*
        universe still received its identity and secret purpose in the assembled
        prompt while ``visibility_permits(..., "read_content")`` was False. The
        original test passed because it only asserted on two grounding strings.

        With no authorized content there is nothing for the universe to speak
        from, and a hollow prompt would have to either lie ("you are newly born")
        or carry a withhold instruction, which is not a boundary. So assembly
        refuses outright.
        """
        udir = _make_universe(base, "u-priv", level="private")
        (udir / "identity.md").write_text(
            "---\nname: Lumen\nstatus: learned\n---\n\n# Identity\nI am Lumen.",
            encoding="utf-8",
        )
        _authenticate("visitor-1")
        with pytest.raises(PermissionError, match="no authorized content"):
            ui._build_persona_system_prompt(udir, tier=il.T1, universe_id="u-priv")

    def test_withheld_universe_leaks_nothing_through_the_raised_error(self, base):
        """The refusal itself must not become the disclosure channel."""
        udir = _make_universe(base, "u-priv", level="private")
        (udir / "identity.md").write_text(
            "---\nname: Lumen\nstatus: learned\n---\n\n# Identity\nI am Lumen.",
            encoding="utf-8",
        )
        _authenticate("visitor-1")
        with pytest.raises(PermissionError) as exc:
            ui._build_persona_system_prompt(udir, tier=il.T1, universe_id="u-priv")
        message = str(exc.value)
        assert "Lumen" not in message
        assert FOUNDER_FACT not in message
        assert "bring their projects to life" not in message

    def test_non_founder_tier_without_a_universe_id_fails_loudly(self, base):
        """No silent fallback: disclosure cannot be evaluated without a target."""
        udir = _make_universe(base, "u-pub", level="public")
        with pytest.raises(ValueError, match="universe_id"):
            ui._build_persona_system_prompt(udir, tier=il.T0, universe_id="")

    def test_permitted_grounding_files_excludes_founder_private(self, base):
        _make_universe(base, "u-pub", level="public")
        _anonymous()
        allowed = il.permitted_grounding_files(
            "u-pub", ("identity.md", "founder.md", "origin.md", "body.md"),
            tier=il.T0,
        )
        assert "founder.md" not in allowed
        assert "body.md" in allowed


# --------------------------------------------------------------------------- #
# 6. Task 6.9 (interlocutor half) — one identity, modulated not replaced
# --------------------------------------------------------------------------- #
class TestOneIdentityAcrossInterlocutors:
    def _named_universe(self, base: Path) -> Path:
        udir = _make_universe(base, "u-pub", level="public")
        (udir / "identity.md").write_text(
            "---\nname: Lumen\nstatus: learned\n---\n\n# Identity\nI am Lumen.",
            encoding="utf-8",
        )
        return udir

    def test_same_learned_name_speaks_at_every_tier(self, base):
        udir = self._named_universe(base)
        prompts = {}
        for tier in (il.T0, il.T1, il.T2):
            if tier == il.T2:
                _authenticate("founder-1")
                grant_universe_access(
                    base, universe_id="u-pub", actor_id="founder-1",
                    permission="admin",
                )
            elif tier == il.T1:
                _authenticate("visitor-1")
            else:
                _anonymous()
            prompts[tier] = ui._build_persona_system_prompt(
                udir, tier=tier, universe_id="u-pub"
            )
        for tier, prompt in prompts.items():
            assert "You are Lumen." in prompt, f"identity replaced at {tier}"

    def test_disclosure_modulates_while_identity_does_not(self, base):
        udir = self._named_universe(base)
        _authenticate("founder-1")
        grant_universe_access(
            base, universe_id="u-pub", actor_id="founder-1", permission="admin"
        )
        founder_prompt = ui._build_persona_system_prompt(
            udir, tier=il.T2, universe_id="u-pub"
        )
        _anonymous()
        visitor_prompt = ui._build_persona_system_prompt(
            udir, tier=il.T0, universe_id="u-pub"
        )
        # Who is speaking does not change...
        assert "You are Lumen." in founder_prompt
        assert "You are Lumen." in visitor_prompt
        # ...but what is disclosed does.
        assert FOUNDER_FACT in founder_prompt
        assert FOUNDER_FACT not in visitor_prompt

    def test_soul_never_becomes_a_second_identity(self, base):
        """Soul governs; it never supplies the name the persona speaks under."""
        udir = self._named_universe(base)
        _anonymous()
        prompt = ui._build_persona_system_prompt(
            udir, tier=il.T0, universe_id="u-pub"
        )
        assert "You are Lumen." in prompt
        assert "You are To help my founder" not in prompt


# --------------------------------------------------------------------------- #
# 7. The live entrypoint binds a REAL tier (not the in-process default)
# --------------------------------------------------------------------------- #
class TestEntrypointBindsTier:
    def test_mcp_converse_passes_the_resolved_tier(self, base, monkeypatch):
        """Mutation gate: drop the resolution at the MCP boundary and this reds.

        ``universe_intelligence.converse`` resolves an omitted ``tier`` itself —
        there is no founder default (that fail-open default was removed; see
        ``test_converse_without_an_explicit_tier_resolves_it``). That fallback
        must still not be what the live path relies on: the boundary that
        actually holds the authenticated request state has to resolve and pass
        the tier explicitly.
        """
        import tinyassets.universe_server as us
        from tinyassets.api import helpers, permissions

        _make_universe(base, "u-own", level="public")
        _authenticate("founder-1")
        grant_universe_access(
            base, universe_id="u-own", actor_id="founder-1", permission="admin"
        )
        monkeypatch.setattr(helpers, "_request_universe", lambda gid="": "u-own")
        monkeypatch.setattr(permissions, "current_actor_id", lambda: "founder-1")

        seen: dict = {}

        def _capture(uid, msg, *, actor_id="", tier=None, **_kw):
            seen.update(uid=uid, actor_id=actor_id, tier=tier)
            return "hello back"

        monkeypatch.setattr(ui, "converse", _capture)
        import json

        out = json.loads(us.converse(message="hi", graph_id="u-own"))
        assert out["reply"] == "hello back"
        assert seen["tier"] == il.T2, "the entrypoint must bind a resolved tier"

    def test_converse_without_an_explicit_tier_resolves_it(self, base, monkeypatch):
        """Cross-family review finding 2 (Codex REJECT, 2026-07-25).

        The first cut defaulted an omitted ``tier`` to FOUNDER on the grounds that
        the only production caller is the founder-gated MCP handle. Codex
        reproduced the hole directly: a T1 visitor calling
        ``universe_intelligence.converse("u", "hi")`` got ``founder.md`` into the
        persona prompt. A fail-OPEN default is not licensed by "no caller does
        that today" — the default must resolve the real tier and fail closed.
        """
        udir = _make_universe(base, "u-pub", level="public")
        _authenticate("visitor-1")  # T1: authenticated, no grant on u-pub
        monkeypatch.setattr(ui, "_request_universe", lambda universe_id="": "u-pub")
        monkeypatch.setattr(ui, "_universe_dir", lambda uid: udir)

        seen: list[str] = []

        def _fake_provider(prompt, system="", **_kw):
            seen.append(system)
            return "{}" if "strict JSON" in system else "hello"

        monkeypatch.setattr(ui, "call_provider", _fake_provider)
        ui.converse("u-pub", "hi")  # tier omitted — the exploited path

        assert seen, "provider was never called"
        assert FOUNDER_FACT not in seen[0]

    def test_entrypoint_store_failure_is_an_honest_error_not_an_unhandled_raise(
        self, base, monkeypatch
    ):
        """Cross-family review finding 3 (Codex REJECT, 2026-07-25).

        Tier binding adds a second ACL read at the entrypoint. A transient store
        failure there must surface through the same honest error envelope the
        handle already uses, not escape as an unhandled exception.
        """
        import json

        import tinyassets.universe_server as us
        from tinyassets.api import helpers

        _make_universe(base, "u-own", level="public")
        _authenticate("founder-1")
        grant_universe_access(
            base, universe_id="u-own", actor_id="founder-1", permission="admin"
        )
        monkeypatch.setattr(helpers, "_request_universe", lambda gid="": "u-own")

        def _boom(universe_id):
            raise RuntimeError("universe_acl store unavailable")

        monkeypatch.setattr(il, "authorize_conversation_turn", _boom)
        out = json.loads(us.converse(message="hi", graph_id="u-own"))
        assert "reply" not in out  # never fakes a reply (Hard Rule 8)
        assert "error" in out
        assert "universe_acl store unavailable" not in json.dumps(out)

    def test_mcp_converse_still_refuses_a_non_founder(self, base, monkeypatch):
        """The tier layer composes with — never replaces — the landed floor."""
        import json

        import tinyassets.universe_server as us
        from tinyassets.api import helpers

        _make_universe(base, "u-own", level="public")
        _authenticate("visitor-1")  # authenticated, but no grant on u-own
        monkeypatch.setattr(helpers, "_request_universe", lambda gid="": "u-own")
        out = json.loads(us.converse(message="hi", graph_id="u-own"))
        assert "reply" not in out
        assert out.get("auth_scope_required") is True


# --- write is a collaborator, not the founder --------------------------------
#
# This distinction was documented the other way ("a write/admin grant") and no test
# exercised it: every case above grants `admin`, including the one named
# `test_founder_with_write_grant_is_t2`. With exactly one founder the two sets are
# identical, so nothing showed. They diverge the moment a universe has collaborators.


class TestWriteGrantIsNotFounder:
    def test_a_write_only_collaborator_is_t1_not_t2(self, base):
        from tinyassets.api import interlocutor

        _make_universe(base, "u-own", level="private")
        grant_universe_access(
            base, universe_id="u-own", actor_id="owner-1", permission="admin"
        )
        grant_universe_access(
            base, universe_id="u-own", actor_id="helper-1", permission="write"
        )
        _authenticate("helper-1")
        who = interlocutor.resolve_interlocutor_tier("u-own")
        assert who.tier == interlocutor.T1, (
            "a write grant lets someone change the universe's work; it must not hand "
            "them the founder's private grounding"
        )

    def test_the_admin_owner_is_still_t2(self, base):
        from tinyassets.api import interlocutor

        _make_universe(base, "u-own", level="private")
        grant_universe_access(
            base, universe_id="u-own", actor_id="owner-1", permission="admin"
        )
        _authenticate("owner-1")
        assert interlocutor.resolve_interlocutor_tier("u-own").tier == interlocutor.T2

    def test_an_unreadable_acl_does_not_confer_founder_authority(self, monkeypatch):
        from tinyassets.api import interlocutor

        def _boom(*_a, **_kw):
            raise OSError("acl store unavailable")

        monkeypatch.setattr(
            "tinyassets.daemon_server.universe_access_permission", _boom
        )
        assert interlocutor._holds_admin_grant("u-own", "someone") is False

    def test_the_founder_notion_agrees_with_the_ownership_signal_elsewhere(self):
        """`source_channel.universe_owner_actor` calls admin the canonical ownership
        signal and excludes write. Two notions of "owner" in one codebase was the
        actual defect; this asserts they now say the same thing."""
        import pathlib as _p

        from tinyassets.api import interlocutor

        src = _p.Path(interlocutor.__file__).read_text(encoding="utf-8")
        body = src.split("def _holds_admin_grant(")[1].split("\ndef ")[0]
        assert '== "admin"' in body
        # Strip the docstring before looking for the permissive helper: the prose
        # deliberately MENTIONS `write=True` to explain why it is not used, and an
        # earlier version of this assertion failed on its own explanation.
        code = body.split('"""')[-1]
        assert "universe_access_allows" not in code, (
            "the permissive helper accepts write OR admin, which is the conflation "
            "this exists to undo"
        )


class TestTheSinkResolvesItsOwnAuthority:
    """Codex REJECT 2026-08-28, finding 1, reproduced before it was fixed.

    The resolver was correct and the public MCP caller bound it correctly. The
    defect was one layer in: `universe_intelligence.converse` accepted a caller's
    `tier` and skipped resolution entirely when one was given, so an actor holding
    only `write` resolved to T1 and then asked for T2 by argument. Every assertion
    in `TestWriteGrantIsNotFounder` stayed green through that route, which is what
    makes these separate tests rather than more of the same.
    """

    def test_a_supplied_tier_cannot_raise_the_resolved_one(self, base):
        from tinyassets.api import interlocutor

        _make_universe(base, "u-own", level="private")
        grant_universe_access(
            base, universe_id="u-own", actor_id="owner-1", permission="admin"
        )
        grant_universe_access(
            base, universe_id="u-own", actor_id="helper-1", permission="write"
        )
        _authenticate("helper-1")
        resolved = interlocutor.resolve_interlocutor_tier("u-own").tier
        assert resolved == interlocutor.T1
        assert (
            interlocutor.clamp_tier(interlocutor.T2, resolved=resolved)
            == interlocutor.T1
        ), "asking for founder tier must not grant it"

    def test_a_supplied_tier_may_still_narrow(self):
        from tinyassets.api import interlocutor

        assert (
            interlocutor.clamp_tier(interlocutor.T0, resolved=interlocutor.T2)
            == interlocutor.T0
        ), "handing authority DOWN is legitimate and must keep working"

    def test_no_opinion_yields_the_resolved_tier(self):
        from tinyassets.api import interlocutor

        assert (
            interlocutor.clamp_tier(None, resolved=interlocutor.T2) == interlocutor.T2
        )

    def test_an_unrecognized_tier_narrows_rather_than_widens(self):
        """Ranking an unknown string below T0 is what makes nonsense safe."""
        from tinyassets.api import interlocutor

        assert (
            interlocutor.clamp_tier("T9-superuser", resolved=interlocutor.T1)
            == "T9-superuser"
        )

    def test_a_write_collaborator_asking_for_t2_does_not_get_founder_grounding(
        self, base, monkeypatch
    ):
        """Codex's reproduction, verbatim, as a test.

        This is the one that matters: not "does the helper return the right
        string" but "does the founder's dossier reach the prompt". It captures
        the assembled system prompt the way the 2026-07-25 finding-2 test does,
        because that is the only place the disclosure is observable.
        """
        udir = _make_universe(base, "u-own", level="private")
        grant_universe_access(
            base, universe_id="u-own", actor_id="owner-1", permission="admin"
        )
        grant_universe_access(
            base, universe_id="u-own", actor_id="helper-1", permission="write"
        )
        _authenticate("helper-1")
        monkeypatch.setattr(ui, "_request_universe", lambda universe_id="": "u-own")
        monkeypatch.setattr(ui, "_universe_dir", lambda uid: udir)

        seen: list[str] = []

        def _fake_provider(prompt, system="", **_kw):
            seen.append(system)
            return "{}" if "strict JSON" in system else "hello"

        monkeypatch.setattr(ui, "call_provider", _fake_provider)
        ui.converse("u-own", "hi", tier=il.T2)  # asking for authority they lack

        assert seen, "provider was never called"
        assert FOUNDER_FACT not in seen[0], (
            "a write-grant collaborator asked for founder tier by argument and got "
            "the founder's private grounding"
        )

    @pytest.mark.parametrize("requested", [il.T0, il.T1, il.T2, None])
    @pytest.mark.parametrize("resolved", [il.T0, il.T1, il.T2])
    def test_every_pair_of_known_tiers_never_widens(self, requested, resolved):
        """The exhaustive matrix, because a one-case fix passes a one-case test.

        Codex round 2 built a mutant that special-cased exactly the reproduction
        it had reported -- `if requested == T2 and resolved == T1: return T1` --
        and every assertion here stayed green while anonymous T0 asking for T2
        still disclosed. A CVE-shaped test invites a CVE-shaped fix.
        """
        from tinyassets.api import interlocutor

        out = interlocutor.clamp_tier(requested, resolved=resolved)
        if requested is None:
            assert out == resolved
        else:
            assert interlocutor.TIERS.index(out) <= interlocutor.TIERS.index(
                resolved
            ), f"clamp({requested!r}, resolved={resolved!r}) widened to {out!r}"

    def test_an_anonymous_caller_asking_for_t2_gets_no_founder_grounding(
        self, base, monkeypatch
    ):
        """The weakest caller asking for the strongest tier -- the case the
        round-1 fix happened to handle and the round-1 tests did not check."""
        udir = _make_universe(base, "u-pub", level="public")
        # No _authenticate(): anonymous, so the resolver binds T0.
        monkeypatch.setattr(ui, "_request_universe", lambda universe_id="": "u-pub")
        monkeypatch.setattr(ui, "_universe_dir", lambda uid: udir)

        seen: list[str] = []

        def _fake_provider(prompt, system="", **_kw):
            seen.append(system)
            return "{}" if "strict JSON" in system else "hello"

        monkeypatch.setattr(ui, "call_provider", _fake_provider)
        ui.converse("u-pub", "hi", tier=il.T2)

        assert seen, "provider was never called"
        assert FOUNDER_FACT not in seen[0], (
            "an anonymous caller asked for founder tier and got the founder's "
            "private grounding"
        )

    def test_the_sink_routes_the_tier_through_the_clamp(self):
        """Kept, but demoted: on its own this is satisfiable by a dead
        `clamp_tier(...)` call beside a raw assignment, which is why the two
        disclosure tests above are the real gate. It earns its place only by
        naming the exact bypass that must not come back."""
        import pathlib as _p

        from tinyassets import universe_intelligence as ui

        src = _p.Path(ui.__file__).read_text(encoding="utf-8")
        body = src.split("def converse(")[1].split("\ndef ")[0]
        assert "clamp_tier(" in body
        assert "if tier is None else tier" not in body, (
            "that is the bypass itself: a supplied tier skipping resolution"
        )


class TestConverseSurvivesAnUnreadableAcl:
    """Codex REJECT 2026-08-28, finding 3. The tier binding was wrapped in an
    honest error envelope; the ACL read one line ABOVE it was not, so a store
    failure escaped the public handle as a raw OSError."""

    def test_a_store_failure_is_a_refusal_not_an_exception(self, base, monkeypatch):
        import json as _json

        from tinyassets import universe_server as us

        _make_universe(base, "u-own", level="private")
        grant_universe_access(
            base, universe_id="u-own", actor_id="owner-1", permission="admin"
        )
        _authenticate("owner-1")

        def _boom(*_a, **_kw):
            raise OSError("acl store unavailable")

        monkeypatch.setattr(
            "tinyassets.daemon_server.universe_access_permission", _boom
        )
        out = _json.loads(us.converse("u-own", "hello"))
        assert "error" in out, "an unreadable ACL must refuse, not raise"
        assert out.get("auth_scope_required") is True

    def test_a_home_store_failure_is_also_a_refusal(self, base, monkeypatch):
        """Codex round 2: the first fix wrapped the ACL read and left the read one
        call EARLIER — resolving the home when no `graph_id` is given — bare. Same
        defect, one line up, which is what "fix the pattern not the instance"
        means in practice."""
        import json as _json

        from tinyassets import universe_server as us

        _make_universe(base, "u-own", level="private")
        grant_universe_access(
            base, universe_id="u-own", actor_id="owner-1", permission="admin"
        )
        _authenticate("owner-1")

        def _boom(*_a, **_kw):
            raise OSError("home store unavailable")

        monkeypatch.setattr("tinyassets.daemon_server.get_founder_home", _boom)
        # A real message: the first version passed "" and passed under the
        # mutation too, because an empty turn is refused before the home is ever
        # resolved. A test that goes green for the wrong reason is the failure
        # mode this whole review is about.
        out = _json.loads(us.converse("hello", ""))  # no graph_id: the home path
        assert "error" in out, "an unreadable home store must refuse, not raise"
        assert out.get("auth_scope_required") is True, (
            "and it must be the honest envelope, not some other refusal that "
            "happens to also contain 'error'"
        )


class TestDisclosureMatchesResolutionForEveryCallerInput:
    """The invariant at the POINT OF USE, not at the helper.

    Codex round 3 made the fair objection that the tests above prove `clamp_tier`
    in isolation and two specific sink calls, but never that the `bound_tier`
    actually used for prompt assembly is no stronger than the authenticated
    resolution. Its mutant inserted a widening branch AFTER the clamp, keyed on
    `actor_id`, and the whole suite stayed green. (`actor_id` is attribution
    only -- engine identity deliberately binds to the verified request principal,
    not this parameter -- so the mutant invented that coupling. The gap in
    coverage was real regardless.)

    So sweep every caller-controlled input to the sink and assert the one thing
    that actually matters: the founder's private grounding reaches the prompt
    **if and only if** the resolver bound T2. Any post-clamp widening from any
    argument fails this, which the per-argument tests could never promise.
    """

    @pytest.mark.parametrize("requested", [None, il.T0, il.T1, il.T2])
    @pytest.mark.parametrize("supplied_actor", ["", "forged-founder"])
    @pytest.mark.parametrize(
        "grant", [None, "read", "write", "admin"], ids=["anon", "read", "write", "admin"]
    )
    def test_founder_grounding_appears_iff_resolution_says_t2(
        self, base, monkeypatch, grant, supplied_actor, requested
    ):
        udir = _make_universe(base, "u-own", level="public")
        grant_universe_access(
            base, universe_id="u-own", actor_id="owner-1", permission="admin"
        )
        if grant is not None:
            grant_universe_access(
                base, universe_id="u-own", actor_id="caller-1", permission=grant
            )
            _authenticate("caller-1")

        resolved = il.resolve_interlocutor_tier("u-own").tier

        monkeypatch.setattr(ui, "_request_universe", lambda universe_id="": "u-own")
        monkeypatch.setattr(ui, "_universe_dir", lambda uid: udir)
        seen: list[str] = []

        def _fake_provider(prompt, system="", **_kw):
            seen.append(system)
            return "{}" if "strict JSON" in system else "hello"

        monkeypatch.setattr(ui, "call_provider", _fake_provider)
        ui.converse("u-own", "hi", actor_id=supplied_actor, tier=requested)

        assert seen, "provider was never called"
        disclosed = FOUNDER_FACT in seen[0]
        where = (
            f"grant={grant!r} actor_id={supplied_actor!r} tier={requested!r}: "
            f"resolved {resolved}, disclosed={disclosed}"
        )

        # SAFETY, stated without reference to `clamp_tier` on purpose. If the
        # expectation were `clamp_tier(...) == T2` then a clamp mutated to widen
        # would move both sides together and this would stay green — the exact
        # trap the source-shape assertions fell into.
        if disclosed:
            assert resolved == il.T2, (
                "founder grounding reached the prompt for a caller the resolver "
                f"did not bind as founder. {where}"
            )

        # LIVENESS: when the caller does not ask to be narrowed, the founder must
        # actually get their own grounding — a gate that discloses to nobody
        # would satisfy the safety half and break the product.
        if requested in (None, il.T2):
            assert disclosed == (resolved == il.T2), where
