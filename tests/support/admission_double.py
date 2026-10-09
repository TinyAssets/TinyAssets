"""Center admission and two-pass deletion doubles for non-oracle tests.

Production creates every center through ``admit_center`` and deletes every
owner tree through the owner-delete cell plus the daemon pass; both need the
bounded launcher, which only the PID1 bootstrap installs. Tests without the
``role_split`` marker get same-uid doubles here: a plain directory for
admission (including a write's implicit center root) and a no-follow removal
for deletion. Tests carrying the marker
exercise the real functions (on the root oracle where they need it).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


def _remove(path: Path) -> int:
    from tinyassets.workspace_fs import _remove_beneath, open_dir_nofollow

    try:
        parent = open_dir_nofollow(path.parent)
    except FileNotFoundError:
        return 0
    try:
        try:
            os.lstat(path.name, dir_fd=parent)
        except FileNotFoundError:
            return 0
        _remove_beneath(parent, path.name)
        return 1
    finally:
        os.close(parent)


@pytest.fixture(autouse=True)
def _admission_and_deletion_double(request, monkeypatch):
    if request.node.get_closest_marker("role_split") is not None:
        yield
        return
    from tinyassets import (
        role_center_admission,
        role_owner_delete,
        role_owner_tree_deletion,
        universe_files,
    )

    def admit_center(data_root, *, principal, center):
        if not re.fullmatch("[A-Za-z0-9_-]{1,128}", center):
            raise role_center_admission.AdmissionRefused("invalid command center name")
        (Path(data_root) / center).mkdir(exist_ok=True)
        return 1

    def delete_center(root, center, *, principal):
        removed = _remove(Path(root) / center)
        return {"center": center, "resumed": not removed, "double": True}

    def remove_subtree(path, *, principal):
        return {"removed": _remove(Path(path)), "double": True}

    def ensure_center_dir(udir):
        # A test that writes into a center it never created stands in for the
        # admission that would have published it.
        Path(udir).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(role_center_admission, "admit_center", admit_center)
    monkeypatch.setattr(role_center_admission, "ensure_center_dir", ensure_center_dir)
    monkeypatch.setattr(universe_files, "_refuse_unadmitted_root", lambda *_args: None)
    monkeypatch.setattr(role_owner_tree_deletion, "delete_center", delete_center)
    monkeypatch.setattr(role_owner_tree_deletion, "pending", lambda root: [])
    monkeypatch.setattr(role_owner_delete, "remove_subtree", remove_subtree)
    yield
