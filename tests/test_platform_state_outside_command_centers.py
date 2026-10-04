"""Consent is decided by a file the command center cannot write.

Before 2026-10-03 the effector-consent database lived INSIDE the command-center
folder, which the command center's own processes can write. Its schema is
``CREATE TABLE IF NOT EXISTS``, so a database that already existed was adopted
rather than refused, and ``effectors/authenticated_external_call.py`` reads the
result. Whichever party created the file first decided what the owner had
consented to.

Two in-place fixes failed before this one: ``?nofollow=1`` (SQLite ignores it)
and per-database provenance records (#4330, three P1 defects). Both tried to
tell a forged file from a real one *after* putting it somewhere the command
center can write.

Nothing is carried forward by the move -- a row with no provenance is
indistinguishable from the forgery being fixed -- so every consent is granted
again through the ordinary ask at first use.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from tinyassets import storage_layout as layout
from tinyassets.providers.provider_jail import UNIVERSE_SIDECARS_DIR
from tinyassets.storage import platform_state_move as psm
from tinyassets.storage.effector_consents import (
    consents_db_path,
    grant_consent,
    initialize_consents_db,
    is_consent_active,
    legacy_consents_db_path,
)

SINK = "authenticated_call"
DEST = "api.example.com"


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    layout.release_for_tests()
    return tmp_path


def _forge(command_center: Path, sink: str = SINK, dest: str = DEST) -> Path:
    """What a command center's own processes could do: create the database
    first, with a row granting itself something."""
    command_center.mkdir(parents=True, exist_ok=True)
    path = legacy_consents_db_path(command_center)
    con = sqlite3.connect(path)
    try:
        con.executescript(
            "CREATE TABLE IF NOT EXISTS effector_consents ("
            " sink TEXT NOT NULL, destination TEXT NOT NULL,"
            " granted_at REAL NOT NULL, granted_by TEXT NOT NULL,"
            " revoked_at REAL, PRIMARY KEY (sink, destination));")
        con.execute(
            "INSERT INTO effector_consents VALUES (?,?,?,?,NULL)",
            (sink, dest, 1.0, "the-command-center-itself"))
        con.commit()
    finally:
        con.close()
    return path


# ---------------------------------------------------------------------------
# The hole itself
# ---------------------------------------------------------------------------


def test_a_forged_in_folder_database_grants_nothing(data: Path):
    """The test that would have caught the original defect."""
    cc = data / "u-alpha"
    forged = _forge(cc)
    assert forged.exists()

    # The gate reads the sidecar, which this command center cannot write.
    assert is_consent_active(cc, sink=SINK, destination=DEST) is False
    assert consents_db_path(cc).parent.parent.name == UNIVERSE_SIDECARS_DIR


def test_the_database_the_gate_reads_is_not_inside_the_command_center(data: Path):
    cc = data / "u-alpha"
    initialize_consents_db(cc)

    path = consents_db_path(cc)
    assert path.exists()
    # Not under the command center at all, by path containment rather than by
    # spelling: this is the property, not the filename.
    assert cc.resolve() not in path.resolve().parents
    assert path.resolve().is_relative_to((data / UNIVERSE_SIDECARS_DIR).resolve())


def test_a_real_grant_still_works(data: Path):
    """The control: moving it must not break consent."""
    cc = data / "u-alpha"
    initialize_consents_db(cc)
    grant_consent(cc, sink=SINK, destination=DEST, granted_by="owner")

    assert is_consent_active(cc, sink=SINK, destination=DEST) is True


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


def _marker(base: Path) -> dict:
    return json.loads((base / layout.MARKER).read_text(encoding="utf-8"))


def test_the_move_carries_no_row_and_sets_the_old_file_aside(data: Path):
    cc = data / "u-alpha"
    _forge(cc)
    (data / layout.MARKER).write_text(
        json.dumps({"layout": 1, "state": "stable"}), encoding="utf-8")

    layout.check(data)

    # Nothing carried: the forged grant did not survive.
    assert is_consent_active(cc, sink=SINK, destination=DEST) is False
    # The old file is kept, renamed, so an operator can still read it.
    assert not legacy_consents_db_path(cc).exists()
    assert (cc / (".effector_consents.db" + psm.SUPERSEDED_SUFFIX)).exists()
    assert _marker(data)[psm.MOVES][psm.CONSENTS] == psm.DONE


def test_the_move_is_idempotent(data: Path):
    cc = data / "u-alpha"
    _forge(cc)
    (data / layout.MARKER).write_text(
        json.dumps({"layout": 1, "state": "stable"}), encoding="utf-8")

    layout.check(data)
    first = sorted(p.name for p in cc.iterdir())
    layout.release_for_tests()
    layout.check(data)

    assert sorted(p.name for p in cc.iterdir()) == first


def test_both_copies_present_is_a_refusal_naming_the_command_center(data: Path):
    """Either an interrupted run or a planted file. Guessing is how a forged
    file gets blessed, so it refuses and changes nothing."""
    cc = data / "u-alpha"
    _forge(cc)
    initialize_consents_db(cc)  # now both exist
    (data / layout.MARKER).write_text(
        json.dumps({"layout": 1, "state": "stable"}), encoding="utf-8")

    with pytest.raises(psm.MoveRefused, match="u-alpha"):
        layout.check(data)

    # Both files untouched, and the marker still says the move is unfinished.
    assert legacy_consents_db_path(cc).exists()
    assert consents_db_path(cc).exists()
    assert _marker(data)[psm.MOVES][psm.CONSENTS] == psm.MIGRATING


def test_an_interrupted_move_resumes(data: Path):
    cc = data / "u-alpha"
    _forge(cc)
    (data / layout.MARKER).write_text(
        json.dumps({"layout": 1, "state": "stable",
                    psm.MOVES: {psm.CONSENTS: psm.MIGRATING}}), encoding="utf-8")

    layout.check(data)

    assert _marker(data)[psm.MOVES][psm.CONSENTS] == psm.DONE
    assert not legacy_consents_db_path(cc).exists()


def test_crash_after_target_creation_resumes_without_legacy_grants(data, monkeypatch):
    from tinyassets.storage import effector_consents

    cc = data / "u-alpha"
    legacy = _forge(cc)
    initialize = effector_consents.initialize_consents_db

    def crash(home):
        initialize(home)
        raise RuntimeError("crash after target creation")

    with monkeypatch.context() as patch:
        patch.setattr(effector_consents, "initialize_consents_db", crash)
        with pytest.raises(RuntimeError, match="crash after target"):
            layout.check(data)
    assert legacy.exists() and consents_db_path(cc).exists()
    layout.check(data)
    assert not legacy.exists()
    assert legacy.with_name(legacy.name + psm.SUPERSEDED_SUFFIX).exists()
    assert not is_consent_active(cc, sink=SINK, destination=DEST)
    assert _marker(data)[psm.MOVES][psm.CONSENTS] == psm.DONE


def test_migration_preserves_reset_for_a_home_without_consents(data):
    from tinyassets.scoped_reset import _walk_home_without_following

    cc = data / "u-alpha"
    cc.mkdir()
    assert not _walk_home_without_following(cc)
    layout.check(data)
    assert not consents_db_path(cc).exists()
    assert not _walk_home_without_following(cc)


def test_crash_after_legacy_rename_resumes(data, monkeypatch):
    cc = data / "u-alpha"
    legacy = _forge(cc)
    replace = os.replace

    def crash(source, destination):
        replace(source, destination)
        if source == legacy:
            raise RuntimeError("crash after legacy rename")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", crash)
        with pytest.raises(RuntimeError, match="crash after legacy"):
            layout.check(data)
    layout.check(data)
    assert not legacy.exists()
    assert not is_consent_active(cc, sink=SINK, destination=DEST)
    assert _marker(data)[psm.MOVES][psm.CONSENTS] == psm.DONE


def test_a_dot_entry_at_the_root_is_not_a_command_center(data: Path):
    """The sidecar folder itself must not be walked as a command center."""
    (data / UNIVERSE_SIDECARS_DIR).mkdir()
    (data / ".runtime").mkdir()
    (data / "u-alpha").mkdir()

    assert [p.name for p in psm.command_centers(data)] == ["u-alpha"]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlink on this host")
def test_a_link_at_the_old_name_is_set_aside_not_followed(data: Path):
    """A link there is evidence. Reading or deleting through it is exactly what
    this change refuses to do, so it is renamed like any other old file."""
    cc = data / "u-alpha"
    cc.mkdir()
    other = data / "u-bravo"
    other.mkdir()
    victim = other / "someone-elses.db"
    victim.write_text("not a database\n", encoding="utf-8")
    try:
        os.symlink(victim, legacy_consents_db_path(cc))
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")
    (data / layout.MARKER).write_text(
        json.dumps({"layout": 1, "state": "stable"}), encoding="utf-8")

    layout.check(data)

    assert victim.read_text(encoding="utf-8") == "not a database\n"
    assert (cc / (".effector_consents.db" + psm.SUPERSEDED_SUFFIX)).is_symlink()
    assert is_consent_active(cc, sink=SINK, destination=DEST) is False


# ---------------------------------------------------------------------------
# The readers that must follow the data
# ---------------------------------------------------------------------------


def test_staging_a_home_for_deletion_takes_its_sidecar_with_it(data: Path):
    """Moving a store OUT of the home puts it where the deletion sweep does not
    walk. A deleted account's consent records left on disk is retained user
    data, so the sidecar is staged into the same directory the home is.
    """
    from tinyassets.account_deletion import _stage_home

    cc = data / "u-alpha"
    cc.mkdir()
    (cc / "note.md").write_text("mine\n", encoding="utf-8")
    initialize_consents_db(cc)
    sidecar = consents_db_path(cc).parent
    assert sidecar.is_dir()

    staged = _stage_home(data, "u-alpha")

    assert staged is not None
    assert not cc.exists()
    assert not sidecar.exists(), "the sidecar survived staging"
    # Both are inside the one staged directory, so one rmtree removes both.
    assert (staged / "home" / "note.md").exists()
    assert (staged / "sidecar" / ".effector_consents.db").exists()


def test_a_sidecar_with_no_home_left_is_still_staged(data: Path):
    """The home may already be gone while its sidecar lingers; that is still
    something to remove, so the early return considers both."""
    from tinyassets.account_deletion import _stage_home

    cc = data / "u-alpha"
    cc.mkdir()
    initialize_consents_db(cc)
    sidecar = consents_db_path(cc).parent
    import shutil

    shutil.rmtree(cc)

    staged = _stage_home(data, "u-alpha")

    assert staged is not None
    assert not sidecar.exists()


def test_nothing_to_remove_is_still_nothing(data: Path):
    from tinyassets.account_deletion import _stage_home

    assert _stage_home(data, "u-never-existed") is None


def test_a_scoped_reset_still_refuses_rather_than_silently_skipping(data: Path):
    """Before the move, the walk found the consents database inside the home and
    raised a "no scoped-reset adapter" blocker. After it, the walk sees nothing
    there -- so without following the store, a reset would proceed and leave the
    consents untouched. A loud refusal became a silent incompleteness, which is
    the worse of the two.
    """
    from tinyassets.scoped_reset import _walk_home_without_following

    cc = data / "u-alpha"
    cc.mkdir()
    initialize_consents_db(cc)

    blockers = _walk_home_without_following(cc)

    assert any("effector_consents" in b for b in blockers), blockers
    assert any("no scoped-reset adapter" in b for b in blockers), blockers


# ---------------------------------------------------------------------------
# The enumeration: what stops the next store being placed inside
# ---------------------------------------------------------------------------

#: State a command center's own code must read through its jail, so it stays
#: inside the folder on purpose. Anything else resolving inside is the defect
#: this test exists to catch.
ALLOWED_INSIDE = {
    ".runtime",  # per-launch credential snapshots the provider jail binds
}


def test_no_platform_path_helper_resolves_inside_a_command_center(data: Path):
    """Derived from the helpers themselves, not a list of filenames.

    A hand-written list of names cannot guard against the omission that
    produced the bug -- that lesson came from the package allowlist, where a
    six-name list stayed green over the very folder it was meant to catch. So
    this calls the real helpers and checks where they land.
    """
    cc = data / "u-alpha"
    cc.mkdir()
    helpers = {
        "effector_consents.consents_db_path": consents_db_path(cc),
    }

    inside = {}
    for name, path in helpers.items():
        resolved = Path(path).resolve()
        if cc.resolve() in resolved.parents or resolved.parent == cc.resolve():
            first = resolved.relative_to(cc.resolve()).parts[0]
            if first not in ALLOWED_INSIDE:
                inside[name] = str(path)
    assert not inside, (
        "platform state that decides authority resolves inside a "
        "command-center folder, which the command center can write:\n"
        + "\n".join(f"  {n}: {p}" for n, p in sorted(inside.items())))
