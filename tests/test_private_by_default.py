"""Private by default: nothing in a universe reaches another user unmeant.

Founder, 2026-09-26: "nodes in users universes should be private unless they make
them other user accessible or visible or interactable in some way."

Every test here drives a REAL caller — `_action_create_universe`,
`_action_list_universes`, `_action_inspect_universe`, `get_status`, `wiki`, the
`set_visibility` action and `write_graph` — not `visibility_permits` directly.
The layered resolver was already strict before this change; what was wrong was
what the two DECLARING paths wrote, and only an end-to-end caller shows that.

This module is in `conftest._STRICT_VISIBILITY_MODULES`, so the repo-wide
`_emulate_deployed_visibility_backfill` double is off here. It would not change
these results — creation writes an EXPLICIT declaration and the double defers to
the real resolver on those — but a test whose subject is the visibility boundary
must not be reading a stand-in.

**The mutation check** (openspec/changes/archive/2026-09-30-private-by-default-universes):
`TestAnotherUserIsRefused` goes red if `DEFAULT_CREATE_VISIBILITY` is reverted to
`"public"`. That is the assertion the change exists to make, so it is named and
kept narrow.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tinyassets.api.status as status_mod
import tinyassets.api.universe as us
import tinyassets.api.visibility as vis
import tinyassets.api.wiki as wiki_mod

# A universe exists because an ownership row names it (#4012). A `founder_home`
# BINDING, not an ACL grant: granting would flip a fixture to private and change
# what the surrounding assertion means.
from tests.conftest import own_universe
from tinyassets.api.wiki import _ensure_wiki_scaffold
from tinyassets.auth.middleware import auth_middleware, clear_identity, set_provider
from tinyassets.auth.provider import AuthProvider, DevAuthProvider, Identity

OWNER = "user_01OWNER"
STRANGER = "user_01STRANGER"


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
    wiki_root = tmp_path / "wiki"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(wiki_root))
    _ensure_wiki_scaffold(wiki_root)
    return root


@pytest.fixture(autouse=True)
def _reset_auth():
    set_provider(DevAuthProvider())
    auth_middleware("dev")
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


_FULL_SCOPES = [
    "tinyassets.universe.read",
    "tinyassets.universe.write",
    "tinyassets.universe.admin",
    "tinyassets.wiki.read",
    "tinyassets.extensions.read",
]


def _authenticate_with(user_id: str, capabilities: list[str]) -> None:
    identity = Identity(
        user_id=user_id, username=user_id, capabilities=capabilities,
    )
    set_provider(_StaticAuthProvider(identity))
    auth_middleware("ok")


def _authenticate(user_id: str) -> None:
    """A real signed-in person, with the ordinary universe + wiki scopes.

    Deliberately NOT anonymous: an anonymous refusal would prove only that the
    transport auth gate fired. The claim under test is that a *legitimate other
    user* of the platform is refused.
    """
    _authenticate_with(user_id, list(_FULL_SCOPES))


def _anonymous() -> None:
    clear_identity()


def _born(uid: str, *, owner: str = OWNER, visibility: str = "") -> dict:
    """Create a universe through the real creation path, as ``owner``."""
    _authenticate(owner)
    out = json.loads(
        us._action_create_universe(universe_id=uid, text="a purpose", visibility=visibility)
    )
    assert out.get("status") == "created", out
    return out


# --------------------------------------------------------------------------- #
# 1. Birth
# --------------------------------------------------------------------------- #
class TestBirth:
    def test_a_universe_is_born_private(self, base):
        out = _born("u-new")
        assert out["visibility"] == "private"
        assert vis.universe_visibility("u-new") is vis.PRIVATE

    def test_birth_records_the_level_as_defaulted_not_chosen(self, base):
        _born("u-new")
        assert vis.declared_level_source("u-new") == "default"
        assert not vis.level_was_chosen_by_owner("u-new")

    def test_a_creator_may_still_state_a_level(self, base):
        out = _born("u-open", visibility="public")
        assert out["visibility"] == "public"
        assert vis.level_was_chosen_by_owner("u-open")

    def test_birth_offers_the_same_levels_the_verb_offers(self, base):
        """Birth must not quietly accept a level the post-birth verb refuses.

        This became reachable when the dispatcher started forwarding `visibility`
        — which it has to, for `set_visibility` — so the deprecated
        `universe(action="create_universe", visibility="metadata_only")` could
        otherwise mint a universe whose content boundary five readers do not
        enforce (docs/concerns/2026-09-26-content-readers-gate-on-the-legacy-bit.md).
        """
        _authenticate(OWNER)
        for level in ("metadata_only", "unlisted"):
            out = json.loads(
                us._action_create_universe(
                    universe_id=f"u-{level}", text="hi", visibility=level
                )
            )
            assert "error" in out, (level, out)
            assert "private" in out["error"] and "public" in out["error"], level
            assert not (base / f"u-{level}").exists(), level  # no partial create

    def test_birth_through_the_dispatcher_refuses_it_too(self, base):
        """The dispatcher path, which is what made this reachable at all.

        `create_universe` needs `tinyassets.universe.costly`, so the token carries
        it — without it this test would pass on the SCOPE refusal and prove nothing
        about the level. The assertion names the offered set for the same reason.
        """
        _authenticate_with(OWNER, [*_FULL_SCOPES, "tinyassets.universe.costly"])
        out = json.loads(
            us._universe_impl(
                action="create_universe",
                visibility="metadata_only",
                allow_named_universe_id=True,
                universe_id="u-via-dispatch",
            )
        )
        assert "error" in out, out
        assert out.get("auth_scope_required") is not True, out
        assert "Invalid visibility 'metadata_only'" in out["error"], out
        assert "private" in out["error"] and "public" in out["error"], out
        assert not (base / "u-via-dispatch").exists()

    def test_the_dispatcher_can_still_birth_an_offered_level(self, base):
        """Guard against the test above passing because birth is broken outright."""
        _authenticate_with(OWNER, [*_FULL_SCOPES, "tinyassets.universe.costly"])
        out = json.loads(
            us._universe_impl(
                action="create_universe",
                visibility="public",
                allow_named_universe_id=True,
                universe_id="u-via-dispatch-ok",
            )
        )
        assert out.get("status") == "created", out
        assert out["visibility"] == "public"
        assert vis.level_was_chosen_by_owner("u-via-dispatch-ok")


# --------------------------------------------------------------------------- #
# 2. The owner keeps full access to their own private universe
# --------------------------------------------------------------------------- #
class TestTheOwnerIsUnaffected:
    def test_the_owner_lists_their_own_private_universe(self, base):
        _born("u-mine")
        _authenticate(OWNER)
        ids = {u["id"] for u in json.loads(us._action_list_universes())["universes"]}
        assert "u-mine" in ids

    def test_the_owner_inspects_their_own_private_universe(self, base):
        _born("u-mine")
        _authenticate(OWNER)
        out = json.loads(us._action_inspect_universe(universe_id="u-mine"))
        assert out.get("error") != "universe_access_denied", out
        assert out["visibility"] == "private"

    def test_the_owner_reads_their_own_private_universes_content(self, base):
        _born("u-mine")
        _authenticate(OWNER)
        out = json.loads(wiki_mod.wiki(action="read", universe_id="u-mine", page="index"))
        assert out.get("error") != "universe_access_denied", out

    def test_the_owner_writes_their_own_private_universe(self, base):
        _born("u-mine")
        _authenticate(OWNER)
        out = json.loads(us._action_set_premise(universe_id="u-mine", text="a new premise"))
        assert out.get("status") == "updated", out

    def test_a_grant_holder_is_not_limited_by_privacy(self, base):
        """Privacy binds readers holding NO grant. Someone the owner let in is
        not "another user" for this purpose."""
        from tinyassets.daemon_server import grant_universe_access

        _born("u-mine")
        grant_universe_access(
            base, universe_id="u-mine", actor_id=STRANGER, permission="read",
            granted_by=OWNER,
        )
        _authenticate(STRANGER)
        out = json.loads(us._action_inspect_universe(universe_id="u-mine"))
        assert out.get("error") != "universe_access_denied", out
        ids = {u["id"] for u in json.loads(us._action_list_universes())["universes"]}
        assert "u-mine" in ids


# --------------------------------------------------------------------------- #
# 3. THE MUTATION TARGET — another authenticated user is refused
# --------------------------------------------------------------------------- #
class TestAnotherUserIsRefused:
    """Revert `DEFAULT_CREATE_VISIBILITY` to `"public"` and every test in this
    class goes red. That is the mutation check for this change.

    `STRANGER` is a fully authenticated platform user with read/write/admin
    universe scopes and wiki read scope — they simply hold no grant on this
    universe. An anonymous caller would prove far less.
    """

    def test_discovery_is_refused(self, base):
        _born("u-mine")
        _authenticate(STRANGER)
        out = json.loads(us._action_list_universes())
        assert "u-mine" not in {u["id"] for u in out["universes"]}
        assert out["count"] == 0
        # And the refusal does not leak that something was withheld.
        assert "u-mine" not in json.dumps(out)

    def test_metadata_is_refused(self, base):
        _born("u-mine")
        _authenticate(STRANGER)
        out = json.loads(us._action_inspect_universe(universe_id="u-mine"))
        assert out["error"] == "universe_access_denied", out

    def test_status_metadata_is_refused(self, base):
        _born("u-mine")
        _authenticate(STRANGER)
        out = json.loads(status_mod.get_status("u-mine"))
        assert out["error"] == "universe_access_denied", out

    def test_content_is_refused(self, base):
        _born("u-mine")
        _authenticate(STRANGER)
        out = json.loads(wiki_mod.wiki(action="read", universe_id="u-mine", page="index"))
        assert out["error"] == "universe_access_denied", out
        assert out["surface"] == "wiki"

    def test_an_unauthenticated_reader_is_refused_too(self, base):
        _born("u-mine")
        _anonymous()
        out = json.loads(us._action_inspect_universe(universe_id="u-mine"))
        assert out["error"] == "universe_access_denied", out


# --------------------------------------------------------------------------- #
# 4. Exposure is the owner's explicit choice
# --------------------------------------------------------------------------- #
class TestExposure:
    def test_an_explicitly_public_universe_is_readable_by_another_user(self, base):
        _born("u-open", visibility="public")
        _authenticate(STRANGER)
        assert json.loads(
            us._action_inspect_universe(universe_id="u-open")
        ).get("error") != "universe_access_denied"
        assert "u-open" in {
            u["id"] for u in json.loads(us._action_list_universes())["universes"]
        }
        assert json.loads(
            wiki_mod.wiki(action="read", universe_id="u-open", page="index")
        ).get("error") != "universe_access_denied"

    def test_the_owner_can_publish_a_private_universe_after_birth(self, base):
        """Before this change `set_universe_visibility` had no production caller
        outside creation and the boot backfill, so private-by-default would have
        been a wall rather than a boundary."""
        _born("u-mine")
        _authenticate(OWNER)
        out = json.loads(
            us._action_set_universe_visibility(universe_id="u-mine", visibility="public")
        )
        assert out["status"] == "updated", out
        assert out["visibility"] == "public"
        assert out["previous_visibility"] == "private"
        assert vis.universe_visibility("u-mine") is vis.PUBLIC

        _authenticate(STRANGER)
        assert "u-mine" in {
            u["id"] for u in json.loads(us._action_list_universes())["universes"]
        }

    def test_publishing_records_the_owner_as_the_source(self, base):
        _born("u-mine")
        _authenticate(OWNER)
        us._action_set_universe_visibility(universe_id="u-mine", visibility="public")
        assert vis.declared_level_source("u-mine") == "owner"
        assert vis.level_was_chosen_by_owner("u-mine")

    def test_the_owner_can_take_it_back(self, base):
        _born("u-mine", visibility="public")
        _authenticate(OWNER)
        us._action_set_universe_visibility(universe_id="u-mine", visibility="private")
        _authenticate(STRANGER)
        out = json.loads(us._action_inspect_universe(universe_id="u-mine"))
        assert out["error"] == "universe_access_denied"

    def test_an_omitted_level_is_never_read_as_publish(self, base):
        """`write_graph.visibility` used to default to `"public"` on the
        signature. An exposure verb reading that default would publish a
        universe nobody asked to publish."""
        _born("u-mine")
        _authenticate(OWNER)
        out = json.loads(us._action_set_universe_visibility(universe_id="u-mine"))
        assert "error" in out
        assert "visibility is required" in out["error"]
        assert vis.universe_visibility("u-mine") is vis.PRIVATE

    def test_an_unrecognized_level_is_refused_naming_the_offered_set(self, base):
        _born("u-mine")
        _authenticate(OWNER)
        out = json.loads(
            us._action_set_universe_visibility(universe_id="u-mine", visibility="wide-open")
        )
        assert "error" in out
        assert out["reason"] == "unknown_level"
        assert "private" in out["error"] and "public" in out["error"]
        assert vis.universe_visibility("u-mine") is vis.PRIVATE

    def test_a_real_but_unenforced_level_is_refused_as_such(self, base):
        """`metadata_only` and `unlisted` exist and are NOT offered here, because
        six universe content readers still gate on the legacy `public_read` bit
        alone (docs/concerns/2026-09-26-content-readers-gate-on-the-legacy-bit.md).
        Offering a level whose boundary nothing enforces is a promise the platform
        breaks, so the refusal says which kind of no this is."""
        _born("u-mine")
        _authenticate(OWNER)
        for level in ("metadata_only", "unlisted"):
            out = json.loads(
                us._action_set_universe_visibility(universe_id="u-mine", visibility=level)
            )
            assert out["reason"] == "level_not_enforced", (level, out)
            assert "is a real level" in out["detail"], (level, out)
            assert vis.universe_visibility("u-mine") is vis.PRIVATE, level

    def test_another_user_cannot_expose_someone_elses_universe(self, base):
        _born("u-mine")
        _authenticate(STRANGER)
        out = json.loads(
            us._universe_impl(
                action="set_visibility", universe_id="u-mine", visibility="public"
            )
        )
        assert out["error"] == "universe_access_denied", out
        assert vis.universe_visibility("u-mine") is vis.PRIVATE

    def test_a_read_only_grant_holder_cannot_expose_it_either(self, base):
        """Reading someone's universe is not authority to publish it."""
        from tinyassets.daemon_server import grant_universe_access

        _born("u-mine")
        grant_universe_access(
            base, universe_id="u-mine", actor_id=STRANGER, permission="read",
            granted_by=OWNER,
        )
        _authenticate(STRANGER)
        out = json.loads(
            us._universe_impl(
                action="set_visibility", universe_id="u-mine", visibility="public"
            )
        )
        assert out["error"] == "universe_access_denied", out
        assert vis.universe_visibility("u-mine") is vis.PRIVATE

    def test_a_delegated_WRITER_cannot_publish_someone_elses_universe(self, base):
        """Publication authority is OWNER, strictly narrower than write.

        Codex cross-family review of PR #4019 reproduced this end-to-end against
        the first cut: `WRITE_ACTIONS` membership makes the central gate demand
        write access, and `permissions._WRITE_PERMISSIONS` accepts `write` OR
        `admin` — so a collaborator granted only `write` published the owner's
        private universe, got `chosen_by="owner"` back, and the migration then
        classified that universe as owner-chosen and left it public.

        Editing a universe and deciding who else may SEE it are different
        authorities once a universe has collaborators.
        """
        from tinyassets.daemon_server import grant_universe_access

        _born("u-mine")
        grant_universe_access(
            base, universe_id="u-mine", actor_id=STRANGER, permission="write",
            granted_by=OWNER,
        )
        _authenticate(STRANGER)
        # The writer really does hold write authority — the refusal below is the
        # narrower owner gate, not a missing grant.
        from tinyassets.api import permissions as _perms

        assert _perms.universe_access_allows("u-mine", write=True) is True

        out = json.loads(
            us._universe_impl(
                action="set_visibility", universe_id="u-mine", visibility="public"
            )
        )
        assert out["error"] == "universe_access_denied", out
        assert vis.universe_visibility("u-mine") is vis.PRIVATE
        # And nothing was laundered into the provenance record.
        assert vis.declared_level_source("u-mine") == "default"

    def test_a_writer_cannot_publish_through_the_public_surface_either(self, base):
        """The same refusal through `write_graph`, which is how it was reproduced."""
        from tinyassets.daemon_server import grant_universe_access
        from tinyassets.universe_server import write_graph

        _born("u-mine")
        grant_universe_access(
            base, universe_id="u-mine", actor_id=STRANGER, permission="write",
            granted_by=OWNER,
        )
        _authenticate(STRANGER)
        out = json.loads(
            write_graph(
                target="command_center", operation="set_visibility",
                graph_id="u-mine", visibility="public",
            )
        )
        assert out.get("error") == "universe_access_denied", out
        assert out.get("chosen_by") != "owner"
        assert vis.universe_visibility("u-mine") is vis.PRIVATE

    def test_a_read_scoped_token_cannot_publish(self, base):
        """Authority is two gates: the OAuth scope and the ownership predicate.

        `set_visibility` derives `tinyassets.universe.write` from its
        `WRITE_ACTIONS` membership, so even the owner's own token is refused when
        it only carries read scope.
        """
        _born("u-mine")
        _authenticate_with(OWNER, ["tinyassets.universe.read"])
        out = json.loads(
            us._universe_impl(
                action="set_visibility", universe_id="u-mine", visibility="public"
            )
        )
        assert out.get("auth_scope_required") is True, out
        assert "tinyassets.universe.write" in out["error"]
        assert vis.universe_visibility("u-mine") is vis.PRIVATE

    def test_the_action_derives_a_write_scope(self, base):
        from tinyassets.auth.provider import build_action_scope_registry

        row = build_action_scope_registry()["universe.set_visibility"]
        assert row.oauth_scope == "tinyassets.universe.write"
        assert row.effect == "write"


# --------------------------------------------------------------------------- #
# 5. The canonical public surface carries the verb
# --------------------------------------------------------------------------- #
class TestWriteGraphSurface:
    def test_write_graph_exposes_the_owners_universe(self, base):
        from tinyassets.universe_server import write_graph

        _born("u-mine")
        _authenticate(OWNER)
        out = json.loads(
            write_graph(
                target="command_center",
                operation="set_visibility",
                graph_id="u-mine",
                visibility="public",
            )
        )
        assert out["status"] == "updated", out
        assert out["visibility"] == "public"
        assert vis.universe_visibility("u-mine") is vis.PUBLIC

    def test_write_graph_without_a_level_does_not_publish(self, base):
        from tinyassets.universe_server import write_graph

        _born("u-mine")
        _authenticate(OWNER)
        out = json.loads(
            write_graph(
                target="command_center", operation="set_visibility", graph_id="u-mine"
            )
        )
        assert "error" in out
        assert vis.universe_visibility("u-mine") is vis.PRIVATE

    def test_the_verb_is_advertised_to_the_agent(self, base):
        """An owner-facing capability the served agent is never told about is a
        capability nobody uses. The docstring is the contract every MCP client
        reads."""
        from tinyassets.universe_server import write_graph

        # Whitespace-normalized: the docstring is hard-wrapped, so a phrase
        # assertion against the raw text silently depends on where it wraps.
        doc = " ".join((write_graph.__doc__ or "").split())
        assert "set_visibility" in doc
        # It must say the default AND that the verb is owner-only, because both
        # are things the agent will otherwise get wrong on a user's behalf.
        assert "private until its owner uses this" in doc
        assert "Owner-only" in doc
        # And it must not advertise a level the verb refuses.
        assert "metadata_only" not in doc.split("set_visibility", 1)[1][:700]

    def test_write_graph_still_admits_a_request_with_no_visibility(self, base):
        """`visibility`'s signature default changed from `"public"` to `""`; the
        request target's stray-parameter guard has to keep accepting both."""
        from tinyassets.universe_server import write_graph

        _born("u-mine")
        _authenticate(OWNER)
        for passed in ({}, {"visibility": "public"}):
            out = json.loads(
                write_graph(
                    target="request",
                    graph_id="u-mine",
                    text="please do a thing",
                    idempotency_key=f"idem-{len(passed)}-0123456789abcdef",
                    **passed,
                )
            )
            assert out.get("error") != "request_validation_error", out


# --------------------------------------------------------------------------- #
# 6. The migration over existing records
# --------------------------------------------------------------------------- #
class TestABranchIsBornPrivateToo:
    """Founder, 2026-09-26: "universes AND THE NODES IN THEM need to be default
    private". A Branch is nodes, so its default is the same rule.

    The canonical `write_graph target=branch` already did
    `setdefault("visibility", "private")`. These cover the two INTERNAL writers
    behind it, reachable through the deprecated `extensions` tool — and a
    deprecated path is still a path.
    """

    @staticmethod
    def _create(base: Path, name: str, **kw) -> dict:
        from tinyassets.api import branches as br
        from tinyassets.daemon_server import initialize_author_server

        initialize_author_server(base)
        return json.loads(br._ext_branch_create({"name": name, **kw}))

    def test_create_branch_defaults_private(self, base):
        _authenticate(OWNER)
        out = self._create(base, "my-shape")
        assert out.get("status") == "created", out
        assert out["visibility"] == "private"

    def test_an_unrecognized_visibility_does_not_publish(self, base):
        """The fallback mattered as much as the default: it used to turn every
        value that was not exactly "private" into `public`, so a typo published."""
        _authenticate(OWNER)
        for i, bad in enumerate(("publik", "world", "yes", "1", "PRIVATE")):
            out = self._create(base, f"shape-{i}", visibility=bad)
            assert out.get("status") == "created", (bad, out)
            assert out["visibility"] == "private", (bad, out)

    def test_case_and_whitespace_still_normalize_to_public(self, base):
        """`"PUBLIC "` IS an explicit public — normalization is deliberate, so the
        fail-closed fallback must not swallow a real request to publish."""
        _authenticate(OWNER)
        for i, ok in enumerate(("PUBLIC ", " public", "Public")):
            out = self._create(base, f"norm-{i}", visibility=ok)
            assert out["visibility"] == "public", (ok, out)

    def test_an_explicit_public_is_still_honoured(self, base):
        _authenticate(OWNER)
        out = self._create(base, "shared-shape", visibility="public")
        assert out["visibility"] == "public", out

    def test_a_row_whose_visibility_field_is_absent_is_private(self, base):
        """The READ side, forged at the row.

        A branch definition written before the field existed must not be readable
        by everyone BECAUSE the field is absent. `_resolve_readable_branch` used to
        default a missing value to `"public"`.
        """
        from tinyassets.api import branches as br

        # Injected at the row rather than forged in storage: branch definitions go
        # through a git-backed backend, so a raw SQL UPDATE is not what
        # `get_branch_definition` reads. Driving the REAL gate with the REAL
        # pre-field row shape is the honest way to reach the branch under test.
        fieldless = {"branch_def_id": "legacy-1", "name": "legacy-shape",
                     "author": OWNER}
        assert "visibility" not in fieldless

        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(
                "tinyassets.daemon_server.get_branch_definition",
                lambda *a, **k: dict(fieldless),
            )
            _authenticate(STRANGER)
            assert br._resolve_readable_branch("legacy-1", str(base)) is None
            # ... and its AUTHOR still reads it, so this closed a read rather than
            # breaking the branch.
            _authenticate(OWNER)
            assert br._resolve_readable_branch("legacy-1", str(base)) is not None
        finally:
            monkey.undo()


class TestMigration:
    """`scripts/migrate_private_by_default.py` — the one-shot the host runs.

    The records it exists for are the ones live production held on 2026-09-02:
    maintenance buckets and IdP-migration backups declared `public` by the old
    backfill, never by a person
    (observed live 2026-09-02; see openspec/changes/archive/2026-09-30-private-by-default-universes/).
    """

    @staticmethod
    def _declare(base: Path, uid: str, level: str, source: str) -> None:
        from tinyassets.daemon_server import ensure_universe_registered

        (base / uid).mkdir(parents=True, exist_ok=True)
        own_universe(base, uid)  # a universe exists because an owner names it (#4012)
        ensure_universe_registered(base, universe_id=uid, universe_path=base / uid)
        vis.set_universe_visibility(uid, level, source=source)

    def _fixture_estate(self, base: Path) -> None:
        # What production looked like: buckets and legacy universes declared by
        # the old backfill, plus one universe an owner actually published.
        self._declare(base, "_removed_universes_20260829", "public", "backfill")
        self._declare(base, "scratch", "public", "backfill")
        self._declare(base, "u-legacy", "public", "default")
        self._declare(base, "u-listed", "metadata_only", "default")
        self._declare(base, "u-chosen", "public", "owner")
        self._declare(base, "u-already", "private", "backfill")

    def test_dry_run_lists_what_it_would_flip_and_writes_nothing(self, base):
        from scripts.migrate_private_by_default import plan, run

        self._fixture_estate(base)
        listed = plan(base)
        assert {r["universe_id"] for r in listed["candidates"]} == {
            "_removed_universes_20260829", "scratch", "u-legacy", "u-listed",
        }
        assert {r["universe_id"] for r in listed["kept"]} == {"u-chosen"}
        assert {r["universe_id"] for r in listed["already_private"]} == {"u-already"}

        summary = run(base)  # apply defaults to False
        assert summary["applied"] is False
        assert summary["flipped"] == []
        assert vis.universe_visibility("u-legacy") is vis.PUBLIC

    def test_apply_flips_every_defaulted_declaration(self, base):
        from scripts.migrate_private_by_default import run

        self._fixture_estate(base)
        summary = run(base, apply=True)
        assert summary["failed"] == []
        assert {r["universe_id"] for r in summary["flipped"]} == {
            "_removed_universes_20260829", "scratch", "u-legacy", "u-listed",
        }
        for uid in ("_removed_universes_20260829", "scratch", "u-legacy", "u-listed"):
            assert vis.universe_visibility(uid) is vis.PRIVATE, uid
            assert vis.declared_level_source(uid) == "migration", uid

    def test_apply_does_not_touch_a_level_its_owner_chose(self, base):
        from scripts.migrate_private_by_default import run

        self._fixture_estate(base)
        run(base, apply=True)
        assert vis.universe_visibility("u-chosen") is vis.PUBLIC
        assert vis.declared_level_source("u-chosen") == "owner"

    def test_a_universe_with_no_provenance_counts_as_defaulted(self, base):
        """Every row written before 2026-09-26 has no `visibility_level_source`.
        The migration must flip those, or it does nothing on real production
        data."""
        from scripts.migrate_private_by_default import plan
        from tinyassets.storage import _connect

        self._declare(base, "u-pre", "public", "owner")
        with _connect(base) as conn:  # strip the key the old writer never wrote
            conn.execute(
                "UPDATE universe_rules SET metadata_json = ? WHERE universe_id = ?",
                (json.dumps({vis.LEVEL_METADATA_KEY: "public"}), "u-pre"),
            )
        assert vis.declared_level_source("u-pre") == ""
        assert {r["universe_id"] for r in plan(base)["candidates"]} == {"u-pre"}

    def test_an_undeclared_universe_is_a_candidate_not_already_private(self, base):
        """The first cut read `declared_level_name() == "private"` as "already
        closed" and skipped every undeclared universe. Codex reproduced that it is
        NOT closed: `public_read` is a separate gate whose column default is 1, and
        readers predating the visibility layer consult it alone. The migration
        reported zero candidates while the universe stayed readable.
        """
        from scripts.migrate_private_by_default import plan, run
        from tinyassets.daemon_server import (
            ensure_universe_registered,
            get_universe_rules,
        )

        (base / "u-undeclared").mkdir()
        own_universe(base, "u-undeclared")  # owned-only discovery (#4012)
        ensure_universe_registered(
            base, universe_id="u-undeclared", universe_path=base / "u-undeclared"
        )
        assert not vis.is_declared("u-undeclared")
        # The layered resolver says closed; the legacy bit says open.
        assert vis.declared_level_name("u-undeclared") == "private"
        assert get_universe_rules(base, universe_id="u-undeclared")["public_read"] is True

        listed = plan(base)
        assert "u-undeclared" in {r["universe_id"] for r in listed["candidates"]}
        assert "u-undeclared" not in {
            r["universe_id"] for r in listed["already_private"]
        }

        run(base, apply=True)
        assert vis.is_declared("u-undeclared")
        assert get_universe_rules(base, universe_id="u-undeclared")["public_read"] is False

    def test_a_private_declaration_over_an_open_legacy_bit_is_repaired(self, base):
        """An inconsistent row the layered resolver hides and the legacy path
        honours. Both gates or neither."""
        from scripts.migrate_private_by_default import plan, run
        from tinyassets.daemon_server import get_universe_rules
        from tinyassets.storage import _connect

        self._declare(base, "u-inconsistent", "private", "backfill")
        with _connect(base) as conn:  # forge the legacy bit back open
            conn.execute(
                "UPDATE universe_rules SET public_read = 1 WHERE universe_id = ?",
                ("u-inconsistent",),
            )
        assert "u-inconsistent" in {r["universe_id"] for r in plan(base)["candidates"]}
        run(base, apply=True)
        assert get_universe_rules(
            base, universe_id="u-inconsistent"
        )["public_read"] is False

    def test_an_unregistered_bare_directory_is_closed_not_crashed(self, base):
        """A universe directory with NO database rows at all.

        `universe_rules` has an FK onto `universes`, so declaring before
        registering died with `FOREIGN KEY constraint failed` — and this is the
        record that most needs closing, because with no rules row the legacy bit
        defaults open and serves its content. A second `--apply` just repeated the
        failure. Codex cross-family review of PR #4019, round 2, reproduced with a
        readable secret in `activity.log`.
        """
        from scripts.migrate_private_by_default import plan, run
        from tinyassets.daemon_server import get_universe_rules

        (base / "u-bare").mkdir()
        own_universe(base, "u-bare")  # owned-only discovery (#4012)
        (base / "u-bare" / "activity.log").write_text("SECRET\n", encoding="utf-8")

        assert "u-bare" in {r["universe_id"] for r in plan(base)["candidates"]}
        summary = run(base, apply=True)
        assert summary["failed"] == [], summary["failed"]
        assert "u-bare" in {r["universe_id"] for r in summary["flipped"]}
        assert vis.is_declared("u-bare")
        assert vis.universe_visibility("u-bare") is vis.PRIVATE
        assert get_universe_rules(base, universe_id="u-bare")["public_read"] is False

    def test_the_closed_bare_directory_stops_leaking_its_activity_log(self, base):
        """The end of the reviewer's reproduction: the reader, not the row."""
        from scripts.migrate_private_by_default import run

        (base / "u-bare").mkdir()
        own_universe(base, "u-bare")  # owned-only discovery (#4012)
        (base / "u-bare" / "activity.log").write_text("SECRET\n", encoding="utf-8")
        _authenticate(STRANGER)
        assert "SECRET" in us._universe_impl(
            action="get_activity", universe_id="u-bare"
        ), "precondition: the unmigrated bare directory leaks"

        run(base, apply=True)
        _authenticate(STRANGER)
        after = us._universe_impl(action="get_activity", universe_id="u-bare")
        assert "SECRET" not in after, after
        assert json.loads(after)["error"] == "universe_access_denied"

    def test_it_does_not_register_a_directory_nobody_owns(self, base):
        """The registration fix must not turn non-universes into universes.

        `run(apply=True)` calls `ensure_universe_registered` on a discovered
        directory, so the guarantee rests on what "discovered" admits. It is
        `_discover_universe_ids`, which since #4012 is OWNED-ONLY and fails closed:
        ownership is the definition of a universe, so an operational directory is
        excluded because nobody owns it rather than because its name is on a list.
        (This test used to assert against that list, `_TOP_LEVEL_OPERATIONAL_DATA_DIRS`;
        #4012 deleted it, and the ownership property it replaced it with is
        strictly stronger — no new operational directory needs a name added.)

        Without this, a migration run would mint a `universes` row for the wiki
        store and the platform's own backups.
        """
        from scripts.migrate_private_by_default import run
        from tinyassets.storage import _connect

        # Operational dirs, a past prune's archive, and a dotfile dir — none owned.
        for name in ("lance", "output", "runs", "wiki", "lancedb",
                     "_removed_universes_20260829", ".hidden"):
            (base / name).mkdir(exist_ok=True)

        summary = run(base, apply=True)
        touched = {
            r["universe_id"]
            for key in ("candidates", "flipped", "already_private", "kept")
            for r in summary[key]
        }
        assert touched == set(), touched
        assert summary["failed"] == [], summary["failed"]
        with _connect(base) as conn:
            rows = conn.execute("SELECT universe_id FROM universes").fetchall()
        assert [r["universe_id"] for r in rows] == []

    def test_the_registered_host_path_matches_the_other_writers(self, base):
        """One definition of where a universe lives.

        The boot backfill registers `base / uid` and the create path registers
        `_universe_dir(uid)`; a migration writing a different `host_path` would be
        a third answer to the same question.
        """
        from scripts.migrate_private_by_default import run
        from tinyassets.storage import _connect

        (base / "u-bare").mkdir()
        own_universe(base, "u-bare")  # owned-only discovery (#4012)
        run(base, apply=True)
        with _connect(base) as conn:
            row = conn.execute(
                "SELECT host_path FROM universes WHERE universe_id = ?", ("u-bare",)
            ).fetchone()
        assert Path(row["host_path"]) == base / "u-bare"

    def test_a_consistent_private_row_is_left_alone(self, base):
        from scripts.migrate_private_by_default import plan

        self._declare(base, "u-settled", "private", "backfill")
        listed = plan(base)
        assert "u-settled" in {r["universe_id"] for r in listed["already_private"]}
        assert "u-settled" not in {r["universe_id"] for r in listed["candidates"]}

    def test_it_is_idempotent(self, base):
        from scripts.migrate_private_by_default import run

        self._fixture_estate(base)
        run(base, apply=True)
        again = run(base, apply=True)
        assert again["candidates"] == []
        assert again["flipped"] == []

    def test_it_deletes_nothing(self, base):
        from scripts.migrate_private_by_default import run

        self._fixture_estate(base)
        before = sorted(p.name for p in base.iterdir())
        marker = base / "_removed_universes_20260829" / "backup.json"
        marker.write_text('{"kept": true}', encoding="utf-8")
        run(base, apply=True)
        assert sorted(p.name for p in base.iterdir()) == before
        assert marker.read_text(encoding="utf-8") == '{"kept": true}'

    def test_skip_leaves_a_record_alone(self, base):
        from scripts.migrate_private_by_default import run

        self._fixture_estate(base)
        summary = run(base, apply=True, skip=frozenset({"scratch"}))
        assert {r["universe_id"] for r in summary["skipped"]} == {"scratch"}
        assert vis.universe_visibility("scratch") is vis.PUBLIC

    def test_it_reaches_an_unowned_maintenance_bucket(self, base):
        """The candidates that matter most hold no ownership row, so the plan
        cannot be built from the owned-universe discovery helper."""
        from scripts.migrate_private_by_default import plan
        from tinyassets.daemon_server import list_universe_acl

        bucket = "_backup_subject_migration_20260829T055340Z"
        self._declare(base, bucket, "public", "backfill")
        assert list_universe_acl(base, universe_id=bucket) == []
        assert bucket in {r["universe_id"] for r in plan(base)["candidates"]}


# --------------------------------------------------------------------------- #
# 7. The provenance record itself
# --------------------------------------------------------------------------- #
class TestALevelOnlyPromisesWhatItEnforces:
    """`metadata_only` withholds content, and a legacy reader did not honour it.

    `set_universe_visibility` turns the legacy `public_read` bit on whenever a
    level grants a public visitor ANY capability — correct as a ceiling for
    `visibility_permits`, and a hole for a reader that consults that bit ALONE.
    `get_memory_scope_status` returns raw `activity.log` lines and did exactly
    that, so a `metadata_only` universe disclosed its literal log content to any
    authenticated principal. Codex reproduced it against PR #4019's first cut.

    The reader predates this change. What this change did was hand an owner a way
    to SELECT `metadata_only`, which made a latent hole reachable on purpose — so
    it is fixed here rather than filed.
    """

    @staticmethod
    def _plant_secret(base: Path, uid: str) -> str:
        secret = "retrieval.scope_mismatch SECRET_PRIVATE_ACTIVITY"
        (base / uid / "activity.log").write_text(secret + "\n", encoding="utf-8")
        return secret

    def test_metadata_only_withholds_raw_activity_lines(self, base):
        from tinyassets.api.runs import _action_get_memory_scope_status

        _born("u-mine")
        secret = self._plant_secret(base, "u-mine")
        # The owner verb does NOT offer this level (see
        # `_OFFERED_VISIBILITY_LEVELS`); a dev/migration caller can still produce
        # it, which is the residual exposure this test pins closed.
        vis.set_universe_visibility("u-mine", "metadata_only", source="migration")
        assert vis.universe_visibility("u-mine") is vis.METADATA_ONLY

        _authenticate(STRANGER)
        raw = _action_get_memory_scope_status({"universe_id": "u-mine"})
        assert secret not in raw, raw
        assert json.loads(raw)["error"] == "universe_access_denied"

    def test_a_private_universe_withholds_them_too(self, base):
        from tinyassets.api.runs import _action_get_memory_scope_status

        _born("u-mine")
        secret = self._plant_secret(base, "u-mine")
        _authenticate(STRANGER)
        raw = _action_get_memory_scope_status({"universe_id": "u-mine"})
        assert secret not in raw, raw

    def test_an_explicitly_public_universe_still_serves_them(self, base):
        """The fix narrows; it must not close a level the owner DID open."""
        from tinyassets.api.runs import _action_get_memory_scope_status

        _born("u-open", visibility="public")
        secret = self._plant_secret(base, "u-open")
        _authenticate(STRANGER)
        raw = _action_get_memory_scope_status({"universe_id": "u-open"})
        assert json.loads(raw).get("error") is None, raw
        assert secret in raw

    def test_the_owner_always_sees_their_own(self, base):
        from tinyassets.api.runs import _action_get_memory_scope_status

        _born("u-mine")
        secret = self._plant_secret(base, "u-mine")
        _authenticate(OWNER)
        raw = _action_get_memory_scope_status({"universe_id": "u-mine"})
        assert json.loads(raw).get("error") is None, raw
        assert secret in raw


class TestProvenance:
    def test_source_is_required(self, base):
        """No default, on purpose: a silent `owner` would make the next
        migration skip a universe nobody chose to expose."""
        from tinyassets.daemon_server import ensure_universe_registered

        (base / "u").mkdir()
        own_universe(base, "u")  # owned-only discovery (#4012)
        ensure_universe_registered(base, universe_id="u", universe_path=base / "u")
        with pytest.raises(TypeError):
            vis.set_universe_visibility("u", "public")  # type: ignore[call-arg]

    def test_an_unknown_source_is_refused(self, base):
        from tinyassets.daemon_server import ensure_universe_registered

        (base / "u").mkdir()
        own_universe(base, "u")  # owned-only discovery (#4012)
        ensure_universe_registered(base, universe_id="u", universe_path=base / "u")
        with pytest.raises(ValueError, match="unknown visibility level source"):
            vis.set_universe_visibility("u", "public", source="the-owner-probably")

    def test_an_unrecognized_recorded_source_reads_as_not_chosen(self, base):
        from tinyassets.daemon_server import ensure_universe_registered
        from tinyassets.storage import _connect

        (base / "u").mkdir()
        own_universe(base, "u")  # owned-only discovery (#4012)
        ensure_universe_registered(base, universe_id="u", universe_path=base / "u")
        vis.set_universe_visibility("u", "public", source="owner")
        with _connect(base) as conn:  # forge a junk provenance
            conn.execute(
                "UPDATE universe_rules SET metadata_json = ? WHERE universe_id = ?",
                (
                    json.dumps({
                        vis.LEVEL_METADATA_KEY: "public",
                        vis.LEVEL_SOURCE_METADATA_KEY: {"not": "a string"},
                    }),
                    "u",
                ),
            )
        assert vis.declared_level_source("u") == ""
        assert not vis.level_was_chosen_by_owner("u")
