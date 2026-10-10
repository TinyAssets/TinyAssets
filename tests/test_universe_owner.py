"""One owning account per universe -- the resolver storage and seats both charge.

account-storage-quota D2 (founder decisions 2026-09-30): the owner is written by
the creation transaction, backfilled ONLY from `founder_home`, never inferred from
`universe_acl`, and the tier is the subscription on the account's home universe.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import tinyassets.api.universe as us
import tinyassets.daemon_server as ds
from tinyassets import universe_owner as uo
from tinyassets.daemon_server import (
    grant_universe_access,
    grant_universe_ownership,
    initialize_author_server,
    set_founder_home,
)
from tinyassets.storage.subscription_state import TIER_FREE, TIER_PAID

A = "workos|alice"
B = "workos|bob"


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.delenv("UNIVERSE_SERVER_DEFAULT_UNIVERSE", raising=False)
    return root


def _owner_rows(base: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(ds.db_path(base)) as conn:
        return conn.execute(
            "SELECT universe_id, owner_id, source FROM universe_owner ORDER BY universe_id"
        ).fetchall()


def _set_paid(universe_dir: Path) -> None:
    from tinyassets.storage import subscription_state as ss

    conn = ss._connect(universe_dir)
    try:
        conn.executescript(ss._SCHEMA)
        conn.execute(
            "INSERT OR REPLACE INTO subscription_meta (key, value) VALUES ('tier', ?)",
            (TIER_PAID,),
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Creation writes the owner, atomically with the grant
# --------------------------------------------------------------------------- #


class TestCreationRecordsTheOwner:
    def test_a_created_universe_is_owned_by_its_creator(self, base, signed_in):
        signed_in(A)
        uid = json.loads(us._action_create_universe(text="a seed."))["universe_id"]

        assert uo.owner_of(base, uid) == A
        assert _owner_rows(base) == [(uid, A, uo.SOURCE_CREATION)]
        assert uo.owned_universes(base, A) == [uid]

    def test_a_second_universe_joins_the_same_account(self, base, signed_in):
        signed_in(A)
        first = json.loads(us._action_create_universe(text="one."))["universe_id"]
        second = json.loads(us._action_create_universe(text="two."))["universe_id"]

        assert uo.owned_universes(base, A) == sorted([first, second])

    @pytest.mark.parametrize("published", [False, True])
    def test_failed_create_retains_ownership_only_after_root_publication(
        self, base, signed_in, monkeypatch, published,
    ):
        from tinyassets import role_center_admission

        signed_in(A)

        def _boom(*_a, **_k):
            raise OSError("disk full")

        with monkeypatch.context() as fault:
            if published:
                fault.setattr(us, "_normalize_escaped_text", _boom)
            else:
                fault.setattr(role_center_admission, "admit_center", _boom)
            out = json.loads(us._action_create_universe(universe_id="u-doomed", text="x"))

        assert "error" in out, out
        assert uo.owner_of(base, "u-doomed") == (A if published else None)
        assert uo.owned_universes(base, A) == (["u-doomed"] if published else [])
        assert (base / "u-doomed").exists() is published
        if published:
            inode = (base / "u-doomed").stat().st_ino
            retried = json.loads(us._action_create_universe(universe_id="u-doomed", text="x"))
            assert retried["status"] == "created"
            assert (base / "u-doomed").stat().st_ino == inode

    def test_ownership_never_moves_and_the_grant_rolls_back_with_it(self, base):
        grant_universe_ownership(base, universe_id="u-x", owner_id=A)

        with pytest.raises(uo.OwnershipConflict):
            grant_universe_ownership(base, universe_id="u-x", owner_id=B)

        assert uo.owner_of(base, "u-x") == A
        # B's admin grant was in the same transaction, so it is not there either.
        assert ds.universe_access_permission(base, universe_id="u-x", actor_id=B) != "admin"

    def test_a_repeated_grant_for_the_same_owner_is_idempotent(self, base):
        grant_universe_ownership(base, universe_id="u-x", owner_id=A)
        grant_universe_ownership(base, universe_id="u-x", owner_id=A)

        assert _owner_rows(base) == [("u-x", A, uo.SOURCE_CREATION)]

    def test_a_shared_universe_charges_only_its_owner(self, base):
        grant_universe_ownership(base, universe_id="u-x", owner_id=A)
        grant_universe_access(
            base, universe_id="u-x", actor_id=B, permission="admin", granted_by=A,
        )

        assert uo.owner_of(base, "u-x") == A
        assert uo.owned_universes(base, B) == []


# --------------------------------------------------------------------------- #
# Backfill: founder_home only, once
# --------------------------------------------------------------------------- #


def _legacy_db_without_owner_table(base: Path) -> None:
    """A pre-existing install: the author-server DB exists, the table does not."""
    initialize_author_server(base)
    with sqlite3.connect(ds.db_path(base)) as conn:
        conn.execute("DROP TABLE universe_owner")
    ds._AUTHOR_SERVER_INITIALIZED.discard(str(ds.db_path(base)))


class TestBackfill:
    def test_a_home_binding_is_backfilled(self, base):
        initialize_author_server(base)
        set_founder_home(base, founder_sub=A, universe_id="u-home-a")
        _legacy_db_without_owner_table(base)

        initialize_author_server(base)

        assert _owner_rows(base) == [("u-home-a", A, uo.SOURCE_FOUNDER_HOME)]

    def test_an_admin_grant_alone_is_never_inferred_to_be_an_owner(self, base):
        """The founder's rule: never infer identity from adjacent tables -- not
        even the self-granted admin row creation used to write."""
        initialize_author_server(base)
        (base / "u-legacy").mkdir()
        grant_universe_access(
            base, universe_id="u-legacy", actor_id=A, permission="admin", granted_by=A,
        )
        _legacy_db_without_owner_table(base)

        initialize_author_server(base)

        assert uo.owner_of(base, "u-legacy") is None
        assert uo.unattributed_universes(base) == ["u-legacy"]

    def test_a_universe_two_homes_point_at_stays_unattributed(self, base):
        initialize_author_server(base)
        set_founder_home(base, founder_sub=A, universe_id="u-shared")
        set_founder_home(base, founder_sub=B, universe_id="u-shared")
        _legacy_db_without_owner_table(base)

        initialize_author_server(base)

        assert uo.owner_of(base, "u-shared") is None

    def test_the_backfill_runs_once_so_a_later_rebind_owns_nothing(self, base):
        """A home rebound after the table exists must not re-own anything: only
        creation records owners from then on."""
        initialize_author_server(base)
        set_founder_home(base, founder_sub=A, universe_id="u-later")
        ds._AUTHOR_SERVER_INITIALIZED.discard(str(ds.db_path(base)))

        initialize_author_server(base)

        assert uo.owner_of(base, "u-later") is None


# --------------------------------------------------------------------------- #
# Tier: the home universe's subscription, free on anything else
# --------------------------------------------------------------------------- #


class TestTierOf:
    def test_no_home_is_free(self, base):
        assert uo.account_type_of(base, A) == TIER_FREE

    def test_the_home_subscription_is_the_account_tier(self, base):
        initialize_author_server(base)
        home = base / "u-home"
        home.mkdir()
        set_founder_home(base, founder_sub=A, universe_id="u-home")
        _set_paid(home)

        assert uo.account_type_of(base, A) == TIER_PAID
        assert uo.account_type_of(base, B) == TIER_FREE

    def test_a_paid_non_home_universe_does_not_make_the_account_paid(self, base):
        initialize_author_server(base)
        (base / "u-home").mkdir()
        other = base / "u-other"
        other.mkdir()
        set_founder_home(base, founder_sub=A, universe_id="u-home")
        _set_paid(other)

        assert uo.account_type_of(base, A) == TIER_FREE

    def test_a_traversal_home_is_free_not_read(self, base):
        initialize_author_server(base)
        set_founder_home(base, founder_sub=A, universe_id="../outside")

        assert uo.account_type_of(base, A) == TIER_FREE

    def test_an_unnamed_principal_is_free(self, base):
        assert uo.account_type_of(base, "") == TIER_FREE


# --------------------------------------------------------------------------- #
# Account deletion takes the person's owner rows
# --------------------------------------------------------------------------- #


class TestAccountDeletion:
    def _delete_rows(self, base: Path, principal: str, home: str) -> None:
        from tinyassets.account_deletion import _delete_root_rows

        conn = sqlite3.connect(ds.db_path(base))
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                _delete_root_rows(conn, principal=principal, home=home, counts={})
        finally:
            conn.close()

    def test_the_home_owner_row_goes_with_the_home(self, base):
        grant_universe_ownership(base, universe_id="u-home", owner_id=A)

        self._delete_rows(base, A, "u-home")

        assert uo.owner_of(base, "u-home") is None

    def test_a_surviving_universe_stays_charged_to_an_opaque_owner(self, base):
        """A retained universe must not become unattributed (never refused) --
        and must not keep the deleted person's identifier either."""
        from tinyassets.account_deletion import _fingerprint

        grant_universe_ownership(base, universe_id="u-home", owner_id=A)
        grant_universe_ownership(base, universe_id="u-shared", owner_id=A)
        grant_universe_access(
            base, universe_id="u-shared", actor_id=B, permission="write", granted_by=A,
        )

        self._delete_rows(base, A, "u-home")

        owner = uo.owner_of(base, "u-shared")
        assert owner == f"deleted:{_fingerprint(A)}"
        assert A not in owner
        assert uo.owned_universes(base, A) == []
        assert uo.account_type_of(base, owner) == TIER_FREE

    def test_a_deleted_owner_does_not_block_the_survivors_own_deletion(self, base):
        """A owned B's home; A deleted their account. B must still be able to
        delete theirs -- the redacted owner names nobody who could resolve it."""
        from tinyassets.account_deletion import deletion_blockers, write_tombstone

        grant_universe_ownership(base, universe_id="u-home-a", owner_id=A)
        grant_universe_ownership(base, universe_id="u-home-b", owner_id=A)
        set_founder_home(base, founder_sub=B, universe_id="u-home-b")
        write_tombstone(base, A)  # the real flow writes it before any row work
        self._delete_rows(base, A, "u-home-a")

        conn = sqlite3.connect(ds.db_path(base))
        conn.row_factory = sqlite3.Row
        try:
            blockers = deletion_blockers(conn, principal=B, home="u-home-b")
        finally:
            conn.close()

        assert not [b for b in blockers if "universe_owner" in b], blockers

    def test_a_live_foreign_owner_still_blocks(self, base):
        """The exemption is for redacted owners only: a LIVE owner's universe is
        not the home-holder's to delete."""
        from tinyassets.account_deletion import deletion_blockers

        grant_universe_ownership(base, universe_id="u-home-b", owner_id=A)
        set_founder_home(base, founder_sub=B, universe_id="u-home-b")

        conn = sqlite3.connect(ds.db_path(base))
        conn.row_factory = sqlite3.Row
        try:
            blockers = deletion_blockers(conn, principal=B, home="u-home-b")
        finally:
            conn.close()

        assert any("universe_owner" in b for b in blockers), blockers

    @pytest.mark.parametrize("live", ["deleted:live-b", "deleted:0123456789abcdef"])
    def test_a_live_principal_named_like_a_redaction_still_blocks(self, base, live):
        """Local auth accepts any subject, including one spelled like a
        redaction (gpt-6-astra round 3). Only a TOMBSTONED fingerprint is
        exempt; a live owner by any name blocks."""
        from tinyassets.account_deletion import deletion_blockers

        grant_universe_ownership(base, universe_id="u-home-b", owner_id=live)
        set_founder_home(base, founder_sub=B, universe_id="u-home-b")

        conn = sqlite3.connect(ds.db_path(base))
        conn.row_factory = sqlite3.Row
        try:
            blockers = deletion_blockers(conn, principal=B, home="u-home-b")
        finally:
            conn.close()

        assert any("universe_owner" in b for b in blockers), blockers

    def test_the_exemption_is_only_for_the_owner_table(self, base):
        """Another table's `deleted:`-looking created_by is still foreign."""
        from tinyassets.account_deletion import _redacted_owner_exemption

        tables = {"universe_owner", "deleted_principals", "consumer_bindings"}
        assert _redacted_owner_exemption("consumer_bindings", "created_by", tables) == ""
        assert _redacted_owner_exemption("universe_owner", "owner_id", tables)


def test_an_interrupted_migration_leaves_nothing_and_retries(base, monkeypatch):
    """The table IS the marker that the backfill ran, so they commit together:
    a crash inside the backfill must not leave a table that skips it forever."""
    initialize_author_server(base)
    set_founder_home(base, founder_sub=A, universe_id="u-home-a")
    _legacy_db_without_owner_table(base)

    def _crash(_conn):
        raise RuntimeError("killed mid-backfill")

    monkeypatch.setattr(uo, "backfill_from_founder_home", _crash)
    with pytest.raises(RuntimeError):
        initialize_author_server(base)
    with sqlite3.connect(ds.db_path(base)) as conn:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'universe_owner'"
        ).fetchone() is None
    monkeypatch.undo()
    ds._AUTHOR_SERVER_INITIALIZED.discard(str(ds.db_path(base)))

    initialize_author_server(base)

    assert uo.owner_of(base, "u-home-a") == A
