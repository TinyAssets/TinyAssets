"""A universe exists because an ownership row says so, not because a folder is
on disk.

The founder read a universe count and said:

    a universe should only exist if it belongs to a user and I'm really the
    only user and have made only the universe that was created when I workos
    logged in for the first time

The definition used to be "any directory under the data root whose name is not
one of four hardcoded operational names" (``_TOP_LEVEL_OPERATIONAL_DATA_DIRS`` =
``lance``/``output``/``runs``/``wiki``), so a migration backup, a past prune's
archive, and every operational store the denylist had not heard of became a
universe: enumerated, declared ``public`` by the boot backfill, and readable by
id.

The denylist could not be completed either. Five live stores were already
missing from it -- daemon memory, retained user inputs, the brain's vector store
(``lancedb``, which is not the listed ``lance``), the workspace pool, and stored
offers -- and every store added next month would need another name.

What every reader asks instead is one question: **does an ownership row name
this directory?** ``universe_acl`` grants and ``founder_home`` bindings are the
union, because first contact binds the home before any grant is written and a
universe with a live founder must not depend on which landed first.

Ownership is matched EXACTLY, never case-folded: a universe id is both a path
component and an authority key (``universe_access_permission`` matches it with
exact SQL), so a resolver answering with a different spelling than it was given
breaks one of the two. See
``TestOneDefinition.test_a_directory_whose_row_differs_only_in_case_is_NOT_owned``.

Routed here: the listing, both direct-id readers (``inspect``, ``switch``), the
``available`` list both of them publish on a miss, the visibility
backfill/startup enumeration, both default/home resolvers, the branch-dependents
scan, the platform work probe, and -- the half a first pass missed --
``visibility_permits`` itself, which is the gate every by-id CONTENT and METADATA
reader passes through (``read_page`` via the wiki gate, explicit-id
``get_status``). Hiding a directory from discovery does NOT retract a
``visibility_level=public`` row the old boot backfill already wrote, and
production's maintenance directories are in exactly that state
(``docs/host-actions.md``).

NOT routed, deliberately:
  * ``sync_universes_from_filesystem`` -- a path INDEX, not the definition. A
    self-hoster restoring a directory from a backup needs it indexed before
    anything can grant on it. Indexed is not owned, and this file proves an
    indexed-but-unowned directory is still invisible.
  * ``tinyassets.reset.universe_dirs`` -- a DESTRUCTIVE reader. A cut needs a
    positive reason to believe a directory was a universe, which is a separate
    lane; conflating it with this predicate would have made ``reset`` start
    sparing directories on a read-only change.

Unowned directories become invisible and unreadable. Nothing here deletes.

One harness note that decides what these tests prove: ``conftest``'s autouse
``_emulate_deployed_visibility_backfill`` makes an UNDECLARED universe resolve
``public`` in every module but ``test_universe_visibility``. So the visibility
gate hides nothing here, and every refusal below is the ownership predicate
doing the work -- which is the stricter arrangement and the one worth asserting.
Where a test's subject IS the visibility gate it says so and uses a second
account, because a granted reader is exempt from its own universe's level.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tinyassets.api.helpers as helpers
import tinyassets.api.universe as us
import tinyassets.api.visibility as vis
from tinyassets.daemon_server import (
    ensure_universe_registered,
    grant_universe_access,
    owned_universe_id,
    owned_universe_ids,
    set_founder_home,
    sync_universes_from_filesystem,
)

# The name a past prune gave its archive, and the name production actually held.
ARCHIVE = "_removed_universes_20260829"
# Operational stores the four-name denylist never covered.
UNLISTED_STORES = ("lancedb", "daemon_wikis", "cloud-automation-inputs", "scratch")


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.delenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", raising=False)
    return root


def _owned_universe(base: Path, uid: str, owner: str, *, level: str = "public") -> Path:
    """A real universe: a directory, an admin grant, and a declared level."""
    udir = base / uid
    udir.mkdir(parents=True, exist_ok=True)
    (udir / "soul.md").write_text(f"# {uid}\n", encoding="utf-8")
    ensure_universe_registered(base, universe_id=uid, universe_path=udir)
    grant_universe_access(
        base, universe_id=uid, actor_id=owner, permission="admin", granted_by=owner,
    )
    # `source="owner"` because this helper stands in for a universe whose owner
    # declared a level; an omitted provenance would read as a platform default
    # and the private-by-default migration would flip it.
    vis.set_universe_visibility(uid, level, source="owner")
    return udir


def _bare_directory(base: Path, name: str) -> Path:
    """A directory nobody owns. What the platform's own operations leave behind."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "soul.md").write_text(f"# {name}\n", encoding="utf-8")
    (d / "status.json").write_text(json.dumps({"phase": "idle"}), encoding="utf-8")
    return d


def _boot_backfill(base: Path) -> dict[str, str]:
    """What the daemon runs at boot, and how the archive became ``public``.

    ``backfill_universe_visibility`` declares a level for every id
    ``_discover_universe_ids`` returns. With the denylist as the definition it
    registered the archive as a universe and declared it ``public`` (the
    ``public_read`` default), which is what made the graveyard both listable and
    readable.
    """
    return vis.backfill_universe_visibility()


def _listed_ids(base: Path) -> list[str]:
    return [u["id"] for u in json.loads(us._action_list_universes())["universes"]]


def _filesystem_is_case_sensitive(base: Path) -> bool:
    """Probed, never assumed from the platform name.

    A case-sensitive volume mounted on Windows and a case-insensitive one on
    Linux both exist, and the state under test -- two spellings of one name side
    by side -- is reachable only where the volume keeps them apart.
    """
    probe = base / ".case-probe-Aa"
    probe.mkdir()
    try:
        return not (base / ".case-probe-aA").exists()
    finally:
        probe.rmdir()


# --------------------------------------------------------------------------- #
# 1. The leak, reproduced. Every assertion here is RED without the predicate.
# --------------------------------------------------------------------------- #


class TestTheGraveyardWasBrowsable:
    def test_an_archive_directory_is_not_listed(self, base, signed_in):
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        _bare_directory(base, ARCHIVE)
        _boot_backfill(base)

        assert _listed_ids(base) == ["u-mine"]

    def test_reading_an_archive_directory_by_id_is_refused(self, base, signed_in):
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        _bare_directory(base, ARCHIVE)
        _boot_backfill(base)

        result = json.loads(us._action_inspect_universe(universe_id=ARCHIVE))

        # Filtering the enumeration is only half: reading one BY ID answered
        # with a full universe payload, reproduced against production.
        assert "not found" in result.get("error", "")
        assert "daemon" not in result
        assert "soul" not in result

    def test_switching_to_an_archive_directory_is_refused(self, base, signed_in):
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        _bare_directory(base, ARCHIVE)
        _boot_backfill(base)

        result = json.loads(us._action_switch_universe(universe_id=ARCHIVE))

        assert "not found" in result.get("error", "")
        assert result.get("status") != "selected"

    def test_a_not_found_answer_does_not_publish_the_graveyard(self, base, signed_in):
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        _bare_directory(base, ARCHIVE)
        for name in UNLISTED_STORES:
            _bare_directory(base, name)
        _boot_backfill(base)

        result = json.loads(us._action_inspect_universe(universe_id="no-such-universe"))

        assert result.get("available") == ["u-mine"]

    def test_switch_not_found_does_not_publish_the_graveyard(self, base, signed_in):
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        _bare_directory(base, ARCHIVE)
        _boot_backfill(base)

        result = json.loads(us._action_switch_universe(universe_id="no-such-universe"))

        assert result.get("available") == ["u-mine"]

    def test_a_not_found_answer_still_honours_the_enumeration_gate(self, base, signed_in):
        """``available`` is an enumeration, so the level that withholds
        discovery withholds it here too -- otherwise a wrong id published every
        owned universe, including another account's unlisted one.

        The unlisted universe belongs to a DIFFERENT account on purpose: a
        granted reader is exempt from their own universe's level, so asking as
        its owner would assert a refusal that cannot happen.
        """
        _owned_universe(base, "u-public", "workos|other", level="public")
        _owned_universe(base, "u-unlisted", "workos|other", level="unlisted")
        signed_in("workos|stranger")

        result = json.loads(us._action_inspect_universe(universe_id="no-such-universe"))

        assert result.get("available") == ["u-public"]

    @pytest.mark.parametrize("name", UNLISTED_STORES)
    def test_an_operational_store_the_denylist_missed_is_not_a_universe(
        self, base, signed_in, name,
    ):
        """No name needs adding to any list: these were never owned."""
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        _bare_directory(base, name)
        _boot_backfill(base)

        assert _listed_ids(base) == ["u-mine"]
        assert "not found" in json.loads(
            us._action_inspect_universe(universe_id=name)
        ).get("error", "")

    def test_the_boot_backfill_does_not_declare_an_unowned_directory(
        self, base, signed_in,
    ):
        """The step that TURNED the archive public. It declared a level for
        every directory the denylist allowed, which is how an archive got the
        ``public`` row that made it listable.

        Asserted against the DURABLE ROWS rather than the resolver, because the
        harness emulation answers ``public`` for an undeclared universe -- the
        artefact the backfill leaves behind is the honest oracle here.
        """
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id, level="private")
        _bare_directory(base, ARCHIVE)

        written = _boot_backfill(base)

        assert ARCHIVE not in written

        from tinyassets.storage import _connect

        with _connect(base) as conn:
            assert conn.execute(
                "SELECT 1 FROM universe_rules WHERE universe_id = ?", (ARCHIVE,),
            ).fetchone() is None
            assert conn.execute(
                "SELECT 1 FROM universes WHERE universe_id = ?", (ARCHIVE,),
            ).fetchone() is None

    def test_the_readiness_gate_does_not_wait_on_an_unowned_directory(
        self, base, signed_in,
    ):
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        _bare_directory(base, ARCHIVE)

        assert ARCHIVE not in vis._discover_universe_ids()


# --------------------------------------------------------------------------- #
# 2. The default / home resolvers. A pointer is not a grant.
# --------------------------------------------------------------------------- #


class TestTheResolversAskTheSameQuestion:
    def test_the_default_resolver_skips_an_unowned_directory(self, base, signed_in):
        """``_aaa-scratch`` sorts before ``u-mine``, and the resolver returned
        the first non-hidden directory -- so an operational store sorting first
        was handed out as the default universe."""
        owner = signed_in("workos|founder")
        _bare_directory(base, "_aaa-scratch")
        _owned_universe(base, "u-mine", owner.user_id)

        assert helpers._default_universe() == "u-mine"

    def test_the_public_landing_resolver_skips_an_unowned_directory(
        self, base, signed_in,
    ):
        owner = signed_in("workos|founder")
        _bare_directory(base, "_aaa-scratch")
        _owned_universe(base, "aaa-public", owner.user_id)

        assert helpers._designated_public_universe() == "aaa-public"

    def test_an_active_universe_marker_is_a_pointer_not_a_grant(self, base, signed_in):
        """``.active_universe`` was returned before any ownership check, so a
        stale marker routed requests into an operational directory."""
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        _bare_directory(base, ARCHIVE)
        (base / ".active_universe").write_text(ARCHIVE, encoding="utf-8")

        assert helpers._default_universe() == "u-mine"

    def test_a_configured_default_is_a_pointer_not_a_grant(
        self, base, signed_in, monkeypatch,
    ):
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        _bare_directory(base, "scratch")
        monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "scratch")

        assert helpers._default_universe() == "u-mine"
        assert helpers._designated_public_universe() == "u-mine"

    def test_a_configured_default_still_answers_on_a_fresh_install(
        self, base, monkeypatch,
    ):
        """Nothing is owned yet because nothing has been created yet. The
        configured name is what the install is about to create, and it never
        wins over a real universe (the test above)."""
        monkeypatch.setenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "first-universe")

        assert helpers._default_universe() == "first-universe"
        assert helpers._designated_public_universe() == "first-universe"

    def test_an_unowned_data_root_resolves_to_the_literal_default(self, base):
        _bare_directory(base, ARCHIVE)

        assert helpers._default_universe() == "default-universe"
        assert helpers._designated_public_universe() == "default-universe"


# --------------------------------------------------------------------------- #
# 3. Nothing an OWNED universe could do before is lost.
# --------------------------------------------------------------------------- #


class TestOwnedUniversesKeepEverything:
    def test_an_acl_grant_alone_makes_a_universe(self, base, signed_in):
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-acl-only", owner.user_id)

        assert _listed_ids(base) == ["u-acl-only"]
        result = json.loads(us._action_inspect_universe(universe_id="u-acl-only"))
        assert result["universe_id"] == "u-acl-only"
        assert result["has_soul"] is True

    def test_a_founder_home_binding_alone_makes_a_universe(self, base, signed_in):
        """First contact binds the home BEFORE any grant is written. A universe
        with a live founder must not depend on which landed first."""
        owner = signed_in("workos|founder")
        udir = base / "u-home-only"
        udir.mkdir()
        (udir / "soul.md").write_text("# home\n", encoding="utf-8")
        ensure_universe_registered(base, universe_id="u-home-only", universe_path=udir)
        set_founder_home(base, founder_sub=owner.user_id, universe_id="u-home-only")
        vis.set_universe_visibility("u-home-only", "public", source="owner")

        assert _listed_ids(base) == ["u-home-only"]
        assert json.loads(
            us._action_inspect_universe(universe_id="u-home-only")
        )["universe_id"] == "u-home-only"

    def test_a_write_only_collaborator_grant_still_makes_it_a_universe(
        self, base, signed_in,
    ):
        """Ownership here is "somebody has a row", not "somebody is admin".
        A universe shared write-only is still somebody's."""
        signed_in("workos|founder")
        udir = base / "u-shared"
        udir.mkdir()
        ensure_universe_registered(base, universe_id="u-shared", universe_path=udir)
        grant_universe_access(
            base, universe_id="u-shared", actor_id="workos|collab",
            permission="write", granted_by="workos|founder",
        )
        vis.set_universe_visibility("u-shared", "public", source="owner")

        assert _listed_ids(base) == ["u-shared"]

    def test_two_accounts_one_code_path(self, base, signed_in):
        """The founder's universe and a second account's universe are owned the
        same way and read through the same predicate -- no free/paid branch."""
        founder = signed_in("workos|founder")
        second = signed_in("workos|second-account")
        _owned_universe(base, "u-founder", founder.user_id)
        _owned_universe(base, "u-second", second.user_id)
        _bare_directory(base, ARCHIVE)
        _boot_backfill(base)

        assert _listed_ids(base) == ["u-founder", "u-second"]
        for uid in ("u-founder", "u-second"):
            assert json.loads(
                us._action_inspect_universe(universe_id=uid)
            )["universe_id"] == uid

    def test_the_operational_denylist_names_are_still_not_universes(
        self, base, signed_in,
    ):
        """The four names the denylist carried stay excluded -- by ownership,
        which is why the list itself is gone."""
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        for name in ("lance", "output", "runs", "wiki"):
            _bare_directory(base, name)
        _boot_backfill(base)

        assert _listed_ids(base) == ["u-mine"]

    def test_the_founder_home_still_resolves_on_an_omitted_scope(
        self, base, signed_in,
    ):
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        set_founder_home(base, founder_sub=owner.user_id, universe_id="u-mine")
        _bare_directory(base, "_aaa-scratch")

        assert json.loads(us._action_inspect_universe())["universe_id"] == "u-mine"


# --------------------------------------------------------------------------- #
# 4. One definition of ownership, and its edges.
# --------------------------------------------------------------------------- #


class TestOneDefinition:
    def test_owned_universe_ids_is_the_union_of_both_stores(self, base, signed_in):
        owner = signed_in("workos|founder")
        grant_universe_access(
            base, universe_id="u-acl", actor_id=owner.user_id,
            permission="admin", granted_by=owner.user_id,
        )
        set_founder_home(base, founder_sub=owner.user_id, universe_id="u-home")

        assert owned_universe_ids(base) == {"u-acl", "u-home"}

    def test_a_directory_whose_row_differs_only_in_case_is_NOT_owned(
        self, base, signed_in,
    ):
        """Exact match, deliberately, and this is the assertion that says why.

        An earlier revision resolved case-insensitively so a directory restored
        as ``U-Mine`` would still be the row's ``u-mine``. A universe id is TWO
        things -- a path component and an authority key that
        ``universe_access_permission`` matches with exact SQL -- so resolving
        them to different spellings breaks whichever one gets the other's
        answer. Requiring them identical is the only arrangement in which they
        cannot disagree. The directory is hidden, NOT deleted, and
        scripts/universe_ownership_inventory.py names it so the row gets written.
        """
        owner = signed_in("workos|founder")
        udir = base / "U-Mine"
        udir.mkdir()
        (udir / "soul.md").write_text("# restored universe\n", encoding="utf-8")
        ensure_universe_registered(base, universe_id="U-Mine", universe_path=udir)
        grant_universe_access(
            base, universe_id="u-mine", actor_id=owner.user_id,
            permission="admin", granted_by=owner.user_id,
        )

        assert owned_universe_id(base, "U-Mine") == ""
        assert helpers._owned_universe_dir_name(base, "U-Mine") == ""
        assert _listed_ids(base) == []
        assert udir.is_dir() and (udir / "soul.md").is_file()

    def test_the_spelling_a_resolver_returns_is_the_spelling_it_was_given(
        self, base, signed_in,
    ):
        """No re-spelling, in either direction. Returning the row's spelling
        opens a path that does not exist on a case-sensitive filesystem;
        returning the directory's spelling denies the owner's write AND makes
        ``universe_is_private`` find no rows, so the other spelling reads as a
        PUBLIC universe."""
        from tinyassets.daemon_server import (
            universe_access_permission,
            universe_is_private,
        )

        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)

        resolved = helpers._owned_universe_dir_name(base, "u-mine")
        assert resolved == "u-mine"
        # The value a resolver hands back is usable as BOTH, which is the point.
        assert (base / resolved).is_dir()
        assert universe_is_private(base, universe_id=resolved) is True
        assert universe_access_permission(
            base, universe_id=resolved, actor_id=owner.user_id,
        ) == "admin"

    def test_a_pointer_to_a_row_with_no_directory_resolves_to_nothing(
        self, base, signed_in,
    ):
        owner = signed_in("workos|founder")
        grant_universe_access(
            base, universe_id="u-gone", actor_id=owner.user_id,
            permission="admin", granted_by=owner.user_id,
        )

        assert helpers._owned_universe_dir_name(base, "u-gone") == ""

    def test_a_traversal_pointer_is_refused_even_when_a_row_names_it(
        self, base, signed_in,
    ):
        """A pointer is caller-influenced -- a marker file, an env var -- so
        ``../outside`` must not be resolvable as a universe.

        Two things had to be got right for this to test the PATH guard rather
        than something else:

        * the row is GRANTED, because ``grant_universe_access`` does not validate
          the id -- without a row the ownership check refuses the traversal on
          its own and the guard is never reached;
        * the name must not begin with ``.``, because ``../outside`` is caught by
          `owned_universe_id`'s dot guard first. ``sub/../../outside`` resolves to
          the same place and reaches the path guard, which is the one thing
          standing between a caller and a directory outside the data root.
        """
        owner = signed_in("workos|founder")
        (base.parent / "outside").mkdir(exist_ok=True)
        escapes = "sub/../../outside"
        grant_universe_access(
            base, universe_id=escapes, actor_id=owner.user_id,
            permission="admin", granted_by=owner.user_id,
        )

        assert escapes in owned_universe_ids(base)  # the row is really there
        assert (base / escapes).resolve() == (base.parent / "outside").resolve()
        assert helpers._owned_universe_dir_name(base, escapes) == ""
        # ...and the shapes the other guards catch, for completeness.
        assert helpers._owned_universe_dir_name(base, "../outside") == ""
        assert helpers._owned_universe_dir_name(base, ".") == ""
        assert helpers._owned_universe_dir_name(base, "") == ""

    def test_a_dotted_name_is_never_a_universe(self, base, signed_in):
        """Whatever the ACL says. ``.deleting/`` is account deletion's staging
        directory, and a row naming it must not make it readable."""
        owner = signed_in("workos|founder")
        (base / ".deleting").mkdir()
        grant_universe_access(
            base, universe_id=".deleting", actor_id=owner.user_id,
            permission="admin", granted_by=owner.user_id,
        )

        assert owned_universe_id(base, ".deleting") == ""
        assert helpers._owned_universe_dir_name(base, ".deleting") == ""
        assert ".deleting" not in _listed_ids(base)

    def test_an_owned_id_with_no_directory_is_not_a_readable_universe(
        self, base, signed_in,
    ):
        """An ownership row is necessary, not sufficient: the directory has to
        be there too."""
        owner = signed_in("workos|founder")
        grant_universe_access(
            base, universe_id="u-vanished", actor_id=owner.user_id,
            permission="admin", granted_by=owner.user_id,
        )

        assert _listed_ids(base) == []
        assert "not found" in json.loads(
            us._action_inspect_universe(universe_id="u-vanished")
        ).get("error", "")

    def test_an_unreadable_ownership_store_says_so_rather_than_not_found(
        self, base, signed_in, monkeypatch,
    ):
        """Returning "" on a failed read refused the request as "not found",
        which tells a caller an existing universe does not exist -- a lie, from
        a transient SQLite lock. Fail closed AND loudly."""
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)

        import tinyassets.daemon_server as ds

        def _boom(*_a, **_k):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(ds, "owned_universe_id", _boom)

        result = json.loads(us._action_inspect_universe(universe_id="u-mine"))
        assert "not found" not in result.get("error", "")
        assert "unavailable" in result.get("error", "").lower()

        switched = json.loads(us._action_switch_universe(universe_id="u-mine"))
        assert "unavailable" in switched.get("error", "").lower()

    def test_an_unreadable_ownership_store_publishes_nothing(
        self, base, signed_in, monkeypatch,
    ):
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)

        import tinyassets.daemon_server as ds

        monkeypatch.setattr(
            ds, "owned_universe_ids",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("locked")),
        )

        assert us._available_universe_ids() == []


# --------------------------------------------------------------------------- #
# 5. Indexed is not owned.
# --------------------------------------------------------------------------- #


class TestTheIndexIsNotTheDefinition:
    def test_the_path_index_stays_unfiltered_and_shows_nobody_anything(
        self, base, signed_in,
    ):
        """A self-hoster restoring a directory from a backup needs it indexed
        before anything can grant on it -- so the index takes everything, and
        the readers still refuse it."""
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        _bare_directory(base, ARCHIVE)

        sync_universes_from_filesystem(base)

        from tinyassets.storage import _connect

        with _connect(base) as conn:
            indexed = {
                str(row[0])
                for row in conn.execute("SELECT universe_id FROM universes")
            }
        assert ARCHIVE in indexed

        assert owned_universe_id(base, ARCHIVE) == ""
        assert _listed_ids(base) == ["u-mine"]
        assert "not found" in json.loads(
            us._action_inspect_universe(universe_id=ARCHIVE)
        ).get("error", "")

    def test_an_unowned_directory_is_not_deleted(self, base, signed_in):
        """Invisible and unreadable, never removed. The cut is a separate lane
        with a dry-run inventory first."""
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        archive = _bare_directory(base, ARCHIVE)

        _boot_backfill(base)
        _listed_ids(base)
        us._action_inspect_universe(universe_id=ARCHIVE)

        assert archive.is_dir()
        assert (archive / "soul.md").is_file()


# --------------------------------------------------------------------------- #
# 6. The half a first pass missed: readers of CONTENT and METADATA by id.
#
# Filtering enumeration hides a directory. It does NOT retract the
# `visibility_level=public` row the old boot backfill already wrote, and
# production's seven maintenance directories are in exactly that state
# (`docs/host-actions.md`). So the graveyard went from browsable to
# unlisted-but-readable, which is the same leak wearing a hat.
#
# `visibility_permits` is where the check belongs: the ONE gate every by-id
# content/metadata reader already passes through.
# --------------------------------------------------------------------------- #


class TestAnUnownedUniverseGrantsNothing:
    def _already_declared_public(self, base: Path, name: str) -> Path:
        """A directory in the state production's archives are ALREADY in: on
        disk, `visibility_level=public`, and nobody owns it."""
        from tinyassets.daemon_server import ensure_universe_registered

        d = _bare_directory(base, name)
        ensure_universe_registered(base, universe_id=name, universe_path=d)
        vis.set_universe_visibility(name, "public", source="owner")
        return d

    @pytest.mark.parametrize(
        "capability", ["discover_existence", "read_metadata", "read_content"],
    )
    def test_no_capability_survives_on_an_unowned_universe(
        self, base, signed_in, capability,
    ):
        signed_in("workos|stranger")
        self._already_declared_public(base, ARCHIVE)

        assert vis.visibility_permits(ARCHIVE, capability) is False

    def test_every_capability_survives_on_an_owned_one(self, base, signed_in):
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id, level="public")

        for capability in ("discover_existence", "read_metadata", "read_content"):
            assert vis.visibility_permits("u-mine", capability) is True, capability

    def test_read_page_cannot_reach_an_unowned_universes_wiki(
        self, base, signed_in, tmp_path, monkeypatch,
    ):
        """The reported P0. `read_page` forwards straight to the wiki gate, which
        asked only about visibility -- so an archive already declared `public`
        answered with its page body to any authenticated caller."""
        import tinyassets.api.wiki as wiki_mod

        monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(tmp_path / "wiki"))
        signed_in("workos|stranger")
        self._already_declared_public(base, ARCHIVE)

        out = json.loads(
            wiki_mod.wiki(action="read", universe_id=ARCHIVE, page="index")
        )

        assert "error" in out, out
        assert "content" not in out

    def test_read_page_still_works_on_an_owned_universe(
        self, base, signed_in, tmp_path, monkeypatch,
    ):
        """The other half of the same guard: a real universe's pages stay as
        readable as before."""
        import tinyassets.api.wiki as wiki_mod

        monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(tmp_path / "wiki"))
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id, level="public")

        out = json.loads(
            wiki_mod.wiki(action="read", universe_id="u-mine", page="index")
        )

        assert out.get("error") not in {
            "universe_access_denied", "authentication_required",
        }, out

    def test_explicit_id_get_status_cannot_describe_an_unowned_universe(
        self, base, signed_in,
    ):
        """`get_status` gated on `udir.is_dir()` plus visibility, so it described
        an archive's phase, word count and activity dates by id."""
        from tinyassets.universe_server import get_status

        signed_in("workos|stranger")
        self._already_declared_public(base, ARCHIVE)

        out = json.loads(get_status(command_center_id=ARCHIVE))

        assert "word_count" not in out, out
        daemon = out.get("daemon")
        assert not (isinstance(daemon, dict) and "phase" in daemon), out

    def test_the_platform_work_probe_ignores_an_unowned_universe(
        self, base, signed_in,
    ):
        """A boolean leaks no ids, which is why this was first judged out of
        scope -- wrongly. A stale `work_targets.json` in an archive made the
        platform report work, and the activity canary then skips its
        healthy-idleness handling and alarms on a merely quiet platform."""
        import tinyassets.api.status as status_mod

        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        archive = _bare_directory(base, ARCHIVE)
        (archive / "work_targets.json").write_text(
            json.dumps([{"lifecycle": "active"}]), encoding="utf-8",
        )

        assert status_mod._platform_has_work() is False

        (base / "u-mine" / "work_targets.json").write_text(
            json.dumps([{"lifecycle": "active"}]), encoding="utf-8",
        )
        assert status_mod._platform_has_work() is True

    def test_an_unreadable_ownership_store_grants_no_capability(
        self, base, signed_in, monkeypatch,
    ):
        """A capability predicate has only a boolean to return, so an unreadable
        authority must answer no. A permissive default there is how a gate stops
        being one."""
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id, level="public")

        import tinyassets.daemon_server as ds

        monkeypatch.setattr(
            ds, "owned_universe_id",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("locked")),
        )

        assert vis.visibility_permits("u-mine", "read_content") is False


# --------------------------------------------------------------------------- #
# 7. The creation window. Requiring an owner to READ makes the old ordering a
#    functional hole, not merely untidy.
# --------------------------------------------------------------------------- #


class TestTheOwnerIsClaimedFirst:
    def test_the_grant_exists_before_the_directory_does(
        self, base, signed_in, monkeypatch,
    ):
        """Creation used to mkdir and seed, then grant ~90 lines later. Inside
        that window the directory was owned by nobody -- and a universe nobody
        owns now grants no capability, so the creator's own seeding reads would
        have been refused."""
        from tinyassets.daemon_server import owned_universe_ids

        # (name, owned-at-mkdir) for every directory made directly under the
        # data root. Creation also makes daemon-owned platform roots there
        # (``.universe-sidecars``, ``.agent-sessions``), which no ownership row
        # names by design; the property is about the universe's own directory,
        # so the check below keys on the id creation returned.
        seen: list[tuple[str, bool]] = []
        real_mkdir = Path.mkdir

        def _spy(self, *a, **k):
            if self.parent == base:
                seen.append((self.name, self.name in owned_universe_ids(base)))
            return real_mkdir(self, *a, **k)

        owner = signed_in("workos|founder")
        monkeypatch.setattr(Path, "mkdir", _spy)
        out = json.loads(us._action_create_universe(text="a seed."))
        monkeypatch.undo()

        uid = out.get("universe_id")
        assert uid, out
        mine = [owned for name, owned in seen if name == uid]
        assert mine and all(mine), (
            "the universe directory was created before any ownership row named it"
        )
        assert owned_universe_id(base, uid) == uid
        assert out.get("founder_id") == owner.user_id

    def test_a_created_universe_is_immediately_readable_by_its_owner(
        self, base, signed_in,
    ):
        """End to end: the thing the window would have broken."""
        signed_in("workos|founder")
        out = json.loads(us._action_create_universe(text="a seed."))
        uid = out["universe_id"]

        assert vis.visibility_permits(uid, "read_metadata") is True
        assert json.loads(
            us._action_inspect_universe(universe_id=uid)
        )["universe_id"] == uid

    def test_an_unauthenticated_create_leaves_nothing_behind(self, base, nobody):
        """The refusal comes back as an error envelope, not an exception --
        ``PermissionError`` is a subclass of ``OSError``, so it takes the
        rollback's OSError branch. Asserting the envelope rather than a raise,
        because the envelope is what a caller actually receives.

        Refusing before the mkdir is what makes "nothing behind" literal: no
        directory to clean up and no grant to revoke, rather than a rollback
        that has to succeed."""
        before = sorted(p.name for p in base.iterdir())

        out = json.loads(us._action_create_universe(universe_id="u-orphan", text="x"))

        assert "must belong to someone" in out.get("error", ""), out
        assert sorted(p.name for p in base.iterdir()) == before
        assert owned_universe_ids(base) == set()

    @pytest.mark.parametrize("published", [False, True])
    def test_failed_create_retains_only_a_published_root(self, base, signed_in, monkeypatch,
                                                        published):
        """Admission owns the root once published; retry must reuse that inode."""
        from tinyassets import role_center_admission

        signed_in("workos|founder")

        def fail(*_a, **_k):
            raise OSError("disk full")

        with monkeypatch.context() as fault:
            if published:
                fault.setattr(us, "_normalize_escaped_text", fail)
            else:
                fault.setattr(role_center_admission, "admit_center", fail)
            out = json.loads(us._action_create_universe(universe_id="u-doomed", text="x"))
        assert "error" in out, out
        home = base / "u-doomed"
        assert owned_universe_id(base, "u-doomed") == ("u-doomed" if published else "")
        assert home.exists() is published
        if published:
            inode = home.stat().st_ino
            assert not (home / "soul.md").exists()
            retried = json.loads(us._action_create_universe(universe_id="u-doomed", text="x"))
            assert retried["status"] == "created"
            assert home.stat().st_ino == inode
            assert (home / "soul.md").is_file()


# --------------------------------------------------------------------------- #
# 8. Each enumeration filter tested on its own.
#
# With ownership required by the shared access gate, the enumeration-side filters
# in the listing and the `available` list are a SECOND line rather than the only
# one -- a whole-surface test cannot tell them apart from the gate. These pin
# them directly, so loosening either goes red on its own.
# --------------------------------------------------------------------------- #


class TestTheEnumerationFiltersStandAlone:
    def test_the_listing_predicate_requires_membership(self, base, signed_in):
        signed_in("workos|founder")
        (base / "u-mine").mkdir()
        (base / ARCHIVE).mkdir()
        (base / ".hidden").mkdir()
        (base / "a-file.txt").write_text("x", encoding="utf-8")

        owned = {"u-mine"}
        assert us._is_listable_universe_dir(base / "u-mine", owned) is True
        assert us._is_listable_universe_dir(base / ARCHIVE, owned) is False
        assert us._is_listable_universe_dir(base / ".hidden", {".hidden"}) is False
        assert us._is_listable_universe_dir(base / "a-file.txt", {"a-file.txt"}) is False

    def test_the_available_list_filters_by_ownership_not_only_by_the_gate(
        self, base, signed_in, monkeypatch,
    ):
        """The visibility gate is forced permissive, so the ownership filter is
        the only thing that can withhold the archive."""
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)
        _bare_directory(base, ARCHIVE)

        monkeypatch.setattr(vis, "visibility_permits", lambda *_a, **_k: True)

        assert us._available_universe_ids() == ["u-mine"]

    def test_the_listing_says_the_store_is_down_rather_than_showing_nothing(
        self, base, signed_in, monkeypatch,
    ):
        """Silently listing zero universes on a broken store tells a founder
        their universes are gone. An empty list is a fact about the data; this is
        a fact about the daemon, and they read identically unless it says so."""
        owner = signed_in("workos|founder")
        _owned_universe(base, "u-mine", owner.user_id)

        import tinyassets.daemon_server as ds

        monkeypatch.setattr(
            ds, "owned_universe_ids",
            lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("database is locked")),
        )

        out = json.loads(us._action_list_universes())
        assert out["universes"] == []
        assert "unavailable" in out.get("note", "").lower(), out
        assert "locked" in out.get("note", ""), out
