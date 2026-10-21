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

import tinyassets.api.universe as us
import tinyassets.api.visibility as vis

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







# --------------------------------------------------------------------------- #
# 2. The owner keeps full access to their own private universe
# --------------------------------------------------------------------------- #
class TestTheOwnerIsUnaffected:
    pass






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







# --------------------------------------------------------------------------- #
# 4. Exposure is the owner's explicit choice
# --------------------------------------------------------------------------- #
class TestExposure:




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



# --------------------------------------------------------------------------- #
# 5. The canonical public surface carries the verb
# --------------------------------------------------------------------------- #
class TestWriteGraphSurface:
    pass





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
    See openspec/changes/archive/2026-09-30-private-by-default-universes/.
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






class TestProvenance:
    pass




pytestmark = pytest.mark.verifies('owner-daemon-in-box:T10')
