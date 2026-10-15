"""Storage gates: fitted workspace reservations, commons pages, uploads, branches.

account-storage-quota D6/D7. One focused test per behaviour. The free quota is
shrunk through its real env override so tests move kilobytes, not gibibytes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets import storage_accounting as sa
from tinyassets.daemon_server import grant_universe_ownership, initialize_author_server

A = "workos|alice"
B = "workos|bob"
KIB = 1024
MIB = 1024 * 1024


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_WIKI_PATH", str(root / "wiki"))
    monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", str(100 * KIB / 1024**3))
    initialize_author_server(root)
    for owner, uid in ((A, "u-a"), (B, "u-b")):
        grant_universe_ownership(root, universe_id=uid, owner_id=owner)
        (root / uid).mkdir()
    return root


def _fill(base: Path, uid: str, size: int) -> None:
    (base / uid / "filler.bin").write_bytes(b"x" * size)


# --------------------------------------------------------------------------- #
# Workspace reservation sized to what fits (D6)
# --------------------------------------------------------------------------- #


class TestFittedWorkspace:
    def test_an_empty_small_account_gets_a_bound_that_fits(self, base):
        """The old fixed 4 GiB reservation refused every permanent workspace on
        a small account, however empty. Now the bound is what fits."""
        _, bound = sa.reserve_fitted(
            base, account_id=A, scope_id="u-a", store="workspaces",
            cap=4 * 1024 * MIB, minimum=16 * KIB,
        )
        assert bound == 100 * KIB  # the whole (tiny test) quota, not 4 GiB

    def test_below_the_minimum_it_is_refused_before_anything_moves(self, base):
        _fill(base, "u-a", 90 * KIB)
        with pytest.raises(sa.StorageRefused) as refused:
            sa.reserve_fitted(
                base, account_id=A, scope_id="u-a", store="workspaces",
                cap=4 * 1024 * MIB, minimum=16 * KIB,
            )
        assert refused.value.record["failure_class"] == sa.FAILURE_QUOTA

    def test_the_replaced_generation_is_credited(self, base):
        """Re-checking out a repo that already fills the account must fit:
        the old generation's discard is owed at publication."""
        _fill(base, "u-a", 90 * KIB)
        _, bound = sa.reserve_fitted(
            base, account_id=A, scope_id="u-a", store="workspaces",
            cap=4 * 1024 * MIB, credit=80 * KIB, minimum=16 * KIB,
        )
        assert bound == 10 * KIB + 80 * KIB

    def test_a_generation_that_outgrew_its_bound_is_refused(self, tmp_path, monkeypatch):
        from tinyassets.boxes.provider import BOX_ROOT
        from tinyassets.control_plane import binding as binding_module
        from tinyassets.effectors import workspace as wse

        # The generation is measured in its owner's admitted box: the lease
        # names the guest path of a checkout inside the bound command center.
        data_root = tmp_path / "data"
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data_root))
        center = data_root / "u-gen"
        lease_dir = center / "gen"
        lease_dir.mkdir(parents=True)
        (lease_dir / "provisioned.bin").write_bytes(b"p" * (50 * KIB))

        class _Lease:
            path = f"{BOX_ROOT}/{center.name}/gen"

        with binding_module.use_binding(binding_module.file_binding(center)):
            with pytest.raises(wse._Refused) as refused:
                wse._require_generation_fits(_Lease(), 40 * KIB)
            assert refused.value.kind == "storage_quota_exceeded"
            assert wse._require_generation_fits(_Lease(), 60 * KIB) == 50 * KIB

    def test_the_effector_fits_the_bound_to_the_owning_account(self, base, monkeypatch):
        """A real-sized free quota (1 GiB here): an empty account's permanent
        workspace gets a bound under the quota -- not the fixed 4 GiB that used
        to be refused outright."""
        from tinyassets.effectors import workspace as wse
        from tinyassets.runs import runs_db_path

        monkeypatch.setenv("TINYASSETS_FREE_STORAGE_GIB", "1")
        bound_reservation, bound = wse._fit_permanent(
            "universe", base / "u-a", "u-a", "github.com__o__r", runs_db_path(base / "u-a"),
        )
        assert sa.MIN_WORKSPACE_BYTES <= bound < 1024 * MIB
        sa.release(bound_reservation)
        assert wse._fit_permanent(
            "scratch", base / "u-a", "u-a", "k", runs_db_path(base / "u-a"),
        ) == (None, wse._DEFAULT_MAX_CHECKOUT_BYTES)


# --------------------------------------------------------------------------- #
# Commons page writes (D7)
# --------------------------------------------------------------------------- #


class TestCommonsPages:
    def test_a_commons_write_past_the_writers_quota_is_refused_and_not_written(
        self, base, signed_in,
    ):
        from tinyassets.api import wiki as api_wiki

        signed_in(A)
        _fill(base, "u-a", 95 * KIB)
        with pytest.raises(sa.StorageRefused):
            api_wiki._wiki_write(
                category="notes", filename="big.md", content="# x\n" + "x" * (10 * KIB),
            )
        assert not list((base / "wiki").rglob("big.md"))
        assert not list((base / ".wiki").rglob("big.md"))

    def test_the_wiki_tool_returns_the_refusal_record(self, base, signed_in):
        from tinyassets.api import wiki as api_wiki

        signed_in(A)
        _fill(base, "u-a", 95 * KIB)
        out = json.loads(api_wiki.wiki(
            action="write", category="notes", filename="big.md",
            content="# x\n" + "x" * (10 * KIB),
        ))
        assert out.get("failure_class") == sa.FAILURE_QUOTA, out

    def test_a_collaborator_refused_in_the_owners_universe_learns_no_numbers(
        self, base, signed_in,
    ):
        """gpt-6-astra PR #4167: the refusal is against the OWNER's pool, so a
        collaborator gets a generic notice -- never the owner's usage, quota or
        private universe ids. The owner, refused the same way, sees everything."""
        from tinyassets.api import wiki as api_wiki

        _fill(base, "u-a", 95 * KIB)
        page = base / "u-a" / "wiki" / "p.md"
        page.parent.mkdir(parents=True)

        signed_in(B)
        with pytest.raises(sa.StorageRefused) as refused:
            api_wiki._charge_commons_write("y" * (20 * KIB), page)
        seen_by_b = sa.visible_record(refused.value)
        assert "largest" not in seen_by_b and "used_bytes" not in seen_by_b
        assert "u-a" not in json.dumps(seen_by_b)
        assert seen_by_b["failure_class"] == sa.FAILURE_QUOTA

        signed_in(A)
        assert sa.visible_record(refused.value)["largest"][0]["scope_id"] == "u-a"

    def test_a_page_in_a_universe_charges_that_universes_owner(self, base, signed_in):
        """A collaborator writing into A's universe spends A's storage."""
        from tinyassets.api import wiki as api_wiki

        signed_in(B)
        page = base / "u-a" / "wiki" / "p.md"
        page.parent.mkdir(parents=True)
        api_wiki._charge_commons_write("y" * (20 * KIB), page)

        assert sa.usage(base, A).used_bytes >= 20 * KIB
        assert sa.usage(base, B).used_bytes < 20 * KIB


# --------------------------------------------------------------------------- #
# Uploads (D7; cross-owner delivery against the RECEIVER, founder Q4)
# --------------------------------------------------------------------------- #


@pytest.fixture
def intake(base, monkeypatch):
    from tinyassets.authoring import service
    from tinyassets.authoring.store import AuthoringStore

    store = AuthoringStore(base)
    store.initialize()
    session = service.start_session(
        actor_id=A, artifact_kind="node", sketch="files", store=store,
    )["session_id"]
    handle = store.put_file_handle(
        session_id=session, owner_id=A, input_name="f", filename="f.bin",
        media_type="application/octet-stream", content=b"z" * (30 * KIB),
        lifetime_seconds=3600,
    )
    monkeypatch.setenv("TINYASSETS_RUN_FILE_CUSTODY_MAX_BYTES", str(64 * MIB))
    return [{"session_id": session, "handle_id": handle["handle_id"]}]


class TestUploads:
    def test_under_the_quota_the_capture_lands(self, base, intake):
        from tinyassets.run_file_capture import capture_authoring_files

        refs = capture_authoring_files(
            base, owner_id=A, universe_id="u-a", label="ok", sources=intake,
        )
        assert refs and refs[0]["size_bytes"] == 30 * KIB

    def test_past_the_quota_nothing_is_copied_or_allocated(self, base, intake):
        from tinyassets import runs
        from tinyassets.run_file_capture import capture_authoring_files

        _fill(base, "u-a", 80 * KIB)
        with pytest.raises(sa.StorageRefused):
            capture_authoring_files(
                base, owner_id=A, universe_id="u-a", label="big", sources=intake,
            )
        with runs._connect(base) as conn:
            ready = conn.execute(
                "SELECT COUNT(*) FROM run_file_objects WHERE state='ready'"
            ).fetchone()[0]
        assert ready == 0
        assert not list((base / ".run-file-custody").glob("*.body"))

    def test_a_full_receiver_tells_the_sender_only_that(self, base, monkeypatch):
        from tinyassets import run_file_crossowner as xo

        def _refused(*_a, **_k):
            raise sa.StorageRefused({"error": "B is using 99 KiB of 100 KiB",
                                     "failure_class": sa.FAILURE_QUOTA})

        monkeypatch.setattr(xo, "_capture_files", _refused)
        with pytest.raises(xo.store.FileCustodyRefused) as refused:
            xo._capture_into_receiver(base, owner_id=B, universe_id="u-b")
        assert str(refused.value) == "recipient_storage_full"
        assert "KiB" not in str(refused.value)


def test_a_targeted_ui_change_past_the_quota_is_refused_before_it_lands(base):
    """`change_app_ui_entry` (added on main beside `save_app_ui`) writes the same
    UI library, so it is gated the same way -- not a way around the quota."""
    from tinyassets.app_ui_store import change_app_ui_entry, get_app_ui

    _fill(base, "u-a", 95 * KIB)
    with pytest.raises(sa.StorageRefused):
        change_app_ui_entry(
            base, owner_user_id=A, universe_id="u-a", operation="add_ui",
            payload={"ui_id": "big", "html": "x" * (10 * KIB)},
        )
    assert get_app_ui(base, owner_user_id=A, universe_id="u-a")["revision"] == 0


# --------------------------------------------------------------------------- #
# Branch writes (D7)
# --------------------------------------------------------------------------- #


class TestBranches:
    def test_a_branch_save_past_the_authors_quota_is_refused(self, base):
        from tinyassets.daemon_server import get_branch_definition, save_branch_definition

        _fill(base, "u-a", 95 * KIB)
        with pytest.raises(sa.StorageRefused):
            save_branch_definition(base, branch_def={
                "branch_def_id": "bd-big", "name": "n", "author": A,
                "description": "d" * (10 * KIB),
            })
        with pytest.raises(KeyError):
            get_branch_definition(base, branch_def_id="bd-big")

    def test_a_version_publish_past_the_publishers_quota_is_refused(self, base):
        from tinyassets.branch_versions import publish_branch_version

        _fill(base, "u-a", 95 * KIB)
        with pytest.raises(sa.StorageRefused):
            publish_branch_version(
                base, {"branch_def_id": "bd", "name": "n"},
                publisher=A, notes="n" * (10 * KIB),
            )

    def test_another_authors_save_is_unaffected(self, base):
        from tinyassets.daemon_server import save_branch_definition

        _fill(base, "u-a", 95 * KIB)
        saved = save_branch_definition(base, branch_def={
            "branch_def_id": "bd-b", "name": "n", "author": B, "description": "d" * KIB,
        })
        assert saved["author"] == B
