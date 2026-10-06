"""D10 data-loss and owner/center isolation matrix on real files and SQLite."""

import pytest

from tinyassets.starter_manifest import SeedFile, SeedManifest, digest
from tinyassets.starter_seeds import seed_store


def bundle(version="1", body=b"new"):
    return SeedManifest(
        "starter-agent-v1",
        version,
        (
            SeedFile("AGENTS.md", body, (digest(b"old"),), True),
            SeedFile("starter/hooks.md", b"hooks"),
            SeedFile("skills/test/SKILL.md", b"skill"),
        ),
    )


def store(root, owner="owner", center="center"):
    return seed_store(root, owner_id=owner, center_id=center)


@pytest.mark.parametrize(
    "initial,expected",
    [
        (b"old", b"new"),
        (b"custom", b"custom"),
        (b"", b""),
        (None, None),
    ],
)
def test_legacy_classification_and_independent_hooks(tmp_path, initial, expected):
    root = tmp_path / "center"
    root.mkdir()
    if initial is not None:
        (root / "AGENTS.md").write_bytes(initial)
    with store(root) as seeds:
        receipt = seeds.install(bundle())
        assert receipt["phase"] == "committed"
        assert (
            (root / "AGENTS.md").read_bytes() == expected
            if expected is not None
            else not (root / "AGENTS.md").exists()
        )
        assert (root / "starter/hooks.md").read_bytes() == b"hooks"
        notice = seeds.notices()[0]
        if initial == b"":
            assert "previously running defaults" in " ".join(notice["payload"]["diagnostics"])
        seeds.mark_delivered(notice["notification_key"])
        assert seeds.install(bundle())["transaction_id"] == receipt["transaction_id"]
        assert len(seeds.notices()) == 1
        assert seeds.notices()[0]["delivered"] == 1


def test_provision_delete_retry_upgrade_and_explicit_adoption(tmp_path):
    root = tmp_path / "center"
    with store(root) as seeds:
        seeds.install(bundle(), fresh=True)
        (root / "AGENTS.md").unlink()
        seeds.install(bundle())
        assert not (root / "AGENTS.md").exists()
        upgrade = seeds.install(bundle("2", b"v2"))
        assert not (root / "AGENTS.md").exists()
        seeds.adopt(upgrade["transaction_id"], "AGENTS.md", expected_hash=None, request_key="adopt")
        assert (root / "AGENTS.md").read_bytes() == b"v2"
        seeds.install(bundle("3", b"v3"))
        assert (root / "AGENTS.md").read_bytes() == b"v3"


def test_undo_stock_predecessor_is_durable_owner_choice(tmp_path):
    root = tmp_path / "center"
    root.mkdir()
    (root / "AGENTS.md").write_bytes(b"old")
    with store(root) as seeds:
        receipt = seeds.install(bundle())
        undo = seeds.undo(receipt["transaction_id"], request_key="undo")
        assert seeds.undo(receipt["transaction_id"], request_key="undo") == undo
        assert (root / "AGENTS.md").read_bytes() == b"old"
        assert not (root / "starter/hooks.md").exists()
        seeds.install(bundle("2"))
        assert (root / "AGENTS.md").read_bytes() == b"old"
        assert not (root / "starter/hooks.md").exists()


def test_undo_preserves_changed_and_deleted_files(tmp_path):
    root = tmp_path / "center"
    with store(root) as seeds:
        receipt = seeds.install(bundle(), fresh=True)
        (root / "AGENTS.md").write_bytes(b"owner edit")
        (root / "starter/hooks.md").unlink()
        seeds.undo(receipt["transaction_id"], request_key="undo")
        seeds.install(bundle("2"))
        assert (root / "AGENTS.md").read_bytes() == b"owner edit"
        assert not (root / "starter/hooks.md").exists()


@pytest.mark.parametrize("crash_after_write", [False, True])
@pytest.mark.parametrize("owner_edit", [False, True])
def test_crash_recovery_preserves_concurrent_edits(
    tmp_path, monkeypatch, crash_after_write, owner_edit
):
    import tinyassets.starter_seeds as module

    root = tmp_path / "center"
    original = module.write_universe_file

    def interrupted(*args, **kwargs):
        if crash_after_write:
            original(*args, **kwargs)
        raise RuntimeError("simulated crash")

    with store(root) as seeds:
        monkeypatch.setattr(module, "write_universe_file", interrupted)
        with pytest.raises(RuntimeError, match="simulated crash"):
            seeds.install(bundle(), fresh=True)
        tx = seeds._rows("seed_transactions")[0]["transaction_id"]
    if owner_edit:
        (root / "AGENTS.md").write_bytes(b"concurrent")
    monkeypatch.setattr(module, "write_universe_file", original)
    with store(root) as seeds:
        result = seeds.install(bundle(), fresh=True)
        assert result["transaction_id"] == tx
        assert (root / "AGENTS.md").read_bytes() == (b"concurrent" if owner_edit else b"new")
        assert len(seeds.notices()) == 1


@pytest.mark.parametrize("second_owner,second_center", [("other", "other"), ("owner", "second")])
def test_forged_references_cannot_cross_owner_or_center(tmp_path, second_owner, second_center):
    with store(tmp_path / "one") as first:
        receipt = first.install(bundle(), fresh=True)
        blob = receipt["paths"][0]["target_blob"]
        notification = first.notices()[0]["notification_key"]
    with store(tmp_path / "two", second_owner, second_center) as second:
        second.install(bundle(), fresh=True)
        for operation in (
            lambda: second.candidate(blob),
            lambda: second.receipt(receipt["transaction_id"]),
            lambda: second.undo(receipt["transaction_id"], request_key="forged"),
            lambda: second.adopt(
                receipt["transaction_id"],
                "AGENTS.md",
                expected_hash=digest(b"new"),
                request_key="forged",
            ),
            lambda: second.mark_delivered(notification),
        ):
            with pytest.raises(KeyError):
                operation()
        assert len(second.notices()) == 1
        assert (tmp_path / "two/AGENTS.md").read_bytes() == b"new"
    with pytest.raises(PermissionError):
        with store(tmp_path / "one", second_owner, second_center):
            pass


def test_newer_schema_fails_without_reset(tmp_path):
    root = tmp_path / "center"
    with store(root) as seeds:
        seeds.install(bundle(), fresh=True)
        seeds.db.execute("UPDATE seed_binding SET schema_version=99")
        seeds.db.commit()
    with pytest.raises(PermissionError):
        with store(root):
            pass
    assert (root / "AGENTS.md").read_bytes() == b"new"


def test_links_are_preserved_without_reading_target(tmp_path):
    root = tmp_path / "center"
    root.mkdir()
    outside = tmp_path / "private"
    outside.write_bytes(b"private bytes")
    (root / "AGENTS.md").symlink_to(outside)
    with store(root) as seeds:
        receipt = seeds.install(bundle())
        path = next(r for r in receipt["paths"] if r["relative_path"] == "AGENTS.md")
        assert path["prior_blob"] is None
        assert path["outcome"] == "preserved-linked"
        assert "linked" in " ".join(seeds.notices()[0]["payload"]["diagnostics"])
        assert all(b"private bytes" != bytes(r["content"]) for r in seeds._rows("seed_blobs"))
    assert outside.read_bytes() == b"private bytes"
    assert (root / "AGENTS.md").is_symlink()


def test_sidecar_link_refused(tmp_path):
    (tmp_path / "private").mkdir()
    (tmp_path / ".universe-sidecars").symlink_to(tmp_path / "private", target_is_directory=True)
    with pytest.raises(OSError, match="unsafe seed storage"):
        with store(tmp_path / "center"):
            pass
    assert not list((tmp_path / "private").iterdir())


def test_stale_adoption_and_manifest_mutation_refused(tmp_path):
    root = tmp_path / "center"
    root.mkdir()
    (root / "AGENTS.md").write_bytes(b"owner")
    with store(root) as seeds:
        receipt = seeds.install(bundle())
        with pytest.raises(ValueError, match="stale"):
            seeds.adopt(
                receipt["transaction_id"],
                "AGENTS.md",
                expected_hash=digest(b"old owner"),
                request_key="stale",
            )
        with pytest.raises(ValueError, match="version changed"):
            seeds.install(bundle(body=b"changed immutable version"))
        assert (root / "AGENTS.md").read_bytes() == b"owner"


def test_undo_retains_install_candidates_and_preserved_paths(tmp_path):
    root = tmp_path / "center"
    root.mkdir()
    (root / "AGENTS.md").write_bytes(b"custom")
    with store(root) as seeds:
        install = seeds.install(bundle())
        notice = seeds.notices()[0]
        seeds.mark_delivered(notice["notification_key"])
        undo = seeds.undo(install["transaction_id"], request_key="undo")
        after = seeds.notices()[0]
        assert after["payload"]["transaction_id"] == install["transaction_id"]
        assert after["payload"]["paths"] == notice["payload"]["paths"]
        assert after["delivered"] == 1  # No duplicate version notification.
        assert after["payload"]["choices"][0]["transaction_id"] == undo["transaction_id"]
        with pytest.raises(ValueError, match="original seed"):
            seeds.adopt(undo["transaction_id"], "starter/hooks.md",
                        expected_hash=None, request_key="wrong-target")
        seeds.adopt(install["transaction_id"], "starter/hooks.md",
                    expected_hash=None, request_key="restore-new-version")
        assert (root / "starter/hooks.md").read_bytes() == b"hooks"
        assert (root / "AGENTS.md").read_bytes() == b"custom"


def test_seed_boundary_has_bounded_wait_and_snapshot_does_not_block(tmp_path):
    from tinyassets.starter_seeds import seed_boundary, seed_snapshot

    root = tmp_path / "center"
    with store(root) as seeds:
        seeds.install(bundle(), fresh=True)
    with seed_boundary(root):
        with pytest.raises(TimeoutError, match="busy"):
            with seed_boundary(root, exclusive=True, timeout=0):
                pytest.fail("exclusive transaction entered beside a tool")
        with seed_snapshot(root, owner_id="owner", center_id="center") as snapshot:
            assert len(snapshot.notices()) == 1
            with pytest.raises(Exception, match="readonly"):
                snapshot.db.execute("DELETE FROM seed_binding")


def test_snapshot_does_not_provision_and_refuses_wrong_binding(tmp_path):
    from tinyassets.starter_seeds import seed_snapshot

    root = tmp_path / "center"
    with seed_snapshot(root, owner_id="owner", center_id="center") as snapshot:
        assert snapshot is None
    assert not root.exists()
    assert not (tmp_path / ".universe-sidecars").exists()
    with store(root) as seeds:
        seeds.install(bundle(), fresh=True)
    with pytest.raises(PermissionError):
        with seed_snapshot(root, owner_id="other", center_id="center"):
            pytest.fail("wrong owner read the seed snapshot")
