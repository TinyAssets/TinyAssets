"""U2 two-pass deletion of a migrated owner tree (design D10, D85).

Pass one is U1's authenticated owner-delete cell (``role_owner_delete``); it
removes the owner identity's entries and leaves daemon entries. Pass two runs
here, as the daemon, with no capability: it removes UID1001 entries and the
empty structure pass one left, and refuses anything else with its path.

The two-pass route applies only to a center whose forward role migration is
verified. A legacy single-UID volume (no owner migration, or a completed
reverse) returns ``None`` and the caller keeps its existing traversal. Any
other layout state refuses: neither route is safe on a half-migrated volume.

The intent and token persist before pass one, so a crash or failure resumes
with the same token. The startup reverse migration refuses while any intent
exists (``deploy/role_volume_migration.py``). Before ``finish`` the center is
retired in the admission log (DA6), which is what lets the next startup accept
the smaller principal set.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Also named by deploy/role_volume_migration.py, which cannot import the app.
INTENT_DIR = ".role-owner-delete"
DAEMON_UID = 1001
OWNER_ID_FIRST, OWNER_ID_LAST = 300001, 399999
_MAX_DEPTH = 256
_NAME = re.compile(r"[A-Za-z0-9_-]+")
_DIRECTORY = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
              | getattr(os, "O_CLOEXEC", 0))


class OwnerTreeDeletionRefused(RuntimeError):
    """The two-pass deletion cannot proceed safely; nothing more was removed."""


def _read_layout(root_fd: int) -> dict[str, Any]:
    try:
        fd = os.open(".layout.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=root_fd)
    except FileNotFoundError:
        return {}  # no layout marker: never migrated
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OwnerTreeDeletionRefused("layout marker is not a regular file")
        with os.fdopen(os.dup(fd), "rb") as handle:
            return json.loads(handle.read(1 << 20))
    finally:
        os.close(fd)


def _layout_split(root_fd: int) -> bool:
    """True after a verified forward migration, False on a legacy layout."""
    layout = _read_layout(root_fd)
    roles = layout.get("roles") or {}
    owners = roles.get("owners")
    if owners is None:
        return False
    if layout.get("state") != "stable" or roles.get("state") != "stable":
        raise OwnerTreeDeletionRefused("role migration is not verified")
    if owners == {"state": "stable", "direction": "reverse"}:
        return False
    if (owners != {"state": "stable", "direction": "forward"}
            or roles.get("metadata") != {"state": "stable", "direction": "forward"}):
        raise OwnerTreeDeletionRefused("role migration is not verified")
    return True


def _center_name(center: str) -> str:
    if type(center) is not str or not _NAME.fullmatch(center):
        raise OwnerTreeDeletionRefused("invalid command center name")
    return center


# --------------------------------------------------------------------------- #
# durable intent
# --------------------------------------------------------------------------- #


def _intent_dir(root_fd: int, *, create: bool) -> int | None:
    try:
        info = os.stat(INTENT_DIR, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not create:
            return None
        os.mkdir(INTENT_DIR, 0o700, dir_fd=root_fd)
        os.fsync(root_fd)
        info = os.stat(INTENT_DIR, dir_fd=root_fd, follow_symlinks=False)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise OwnerTreeDeletionRefused("deletion intent directory is not daemon-private")
    fd = os.open(INTENT_DIR, _DIRECTORY, dir_fd=root_fd)
    if not os.path.samestat(os.fstat(fd), info):
        os.close(fd)
        raise OwnerTreeDeletionRefused("deletion intent directory changed")
    return fd


def _load_intent(root_fd: int, center: str) -> dict[str, Any] | None:
    intents = _intent_dir(root_fd, create=False)
    if intents is None:
        return None
    try:
        try:
            fd = os.open(center + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=intents)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid()):
                raise OwnerTreeDeletionRefused("deletion intent is not daemon-private")
            with os.fdopen(os.dup(fd), "rb") as handle:
                intent = json.loads(handle.read(4096))
        finally:
            os.close(fd)
    finally:
        os.close(intents)
    if (not isinstance(intent, dict) or set(intent) != {"center", "principal", "token", "machine"}
            or intent["center"] != center
            or not re.fullmatch("[a-f0-9]{32}", str(intent["token"]))
            or type(intent["machine"]) is not int
            or not OWNER_ID_FIRST <= intent["machine"] <= OWNER_ID_LAST):
        raise OwnerTreeDeletionRefused("invalid deletion intent")
    return intent


def _write_intent(root_fd: int, intent: dict[str, Any]) -> None:
    intents = _intent_dir(root_fd, create=True)
    temporary = f".{intent['center']}.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                     | os.O_CLOEXEC, 0o600, dir_fd=intents)
        try:
            os.write(fd, json.dumps(intent, sort_keys=True).encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, intent["center"] + ".json", src_dir_fd=intents, dst_dir_fd=intents)
        os.fsync(intents)
    finally:
        os.close(intents)


def _clear_intent(root_fd: int, center: str) -> None:
    intents = _intent_dir(root_fd, create=False)
    if intents is None:
        return
    try:
        os.unlink(center + ".json", dir_fd=intents)
        os.fsync(intents)
    finally:
        os.close(intents)


def pending(root: str | Path) -> list[str]:
    """Centers with a durable, unfinished two-pass deletion."""
    root_fd = os.open(os.fspath(root), _DIRECTORY)
    try:
        intents = _intent_dir(root_fd, create=False)
        if intents is None:
            return []
        try:
            return sorted(name[:-5] for name in os.listdir(intents)
                          if name.endswith(".json") and not name.startswith("."))
        finally:
            os.close(intents)
    finally:
        os.close(root_fd)


# --------------------------------------------------------------------------- #
# pass two
# --------------------------------------------------------------------------- #


def daemon_pass(root: str | Path, center: str, *, machine: int) -> dict[str, int]:
    """Remove daemon entries and the empty structure pass one left behind.

    Capability-free: UID1001 entries are unlinked; a directory is removed only
    when empty and owned by UID1001 or by this center's own owner identity. An
    owner non-directory is unlinked only beneath a UID1001 parent, the one
    place pass one cannot write (the migrated center root is 1001:<owner>
    2750). Any other entry, including one pass one should have removed,
    refuses with its relative path and is never touched. Not atomic: a refusal leaves prior
    removals and the intent in place for resume.
    """
    center = _center_name(center)
    removed = 0
    root_fd = os.open(os.fspath(root), _DIRECTORY)
    try:
        device = os.fstat(root_fd).st_dev

        def check(info: os.stat_result, relative: str, *, directory: bool,
                  daemon_parent: bool) -> None:
            if info.st_dev != device:
                raise OwnerTreeDeletionRefused("deletion crosses filesystem: " + relative)
            allowed = {DAEMON_UID, machine} if directory or daemon_parent else {DAEMON_UID}
            if info.st_uid not in allowed:
                raise OwnerTreeDeletionRefused("foreign or undeleted entry: " + relative)

        def walk(parent: int, name: str, relative: str, depth: int) -> None:
            nonlocal removed
            if depth > _MAX_DEPTH:
                raise OwnerTreeDeletionRefused("deletion depth bound: " + relative[:1024])
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            directory = stat.S_ISDIR(info.st_mode)
            check(info, relative, directory=directory,
                  daemon_parent=os.fstat(parent).st_uid == DAEMON_UID and depth > 0)
            try:
                if directory:
                    fd = os.open(name, _DIRECTORY, dir_fd=parent)
                    try:
                        if not os.path.samestat(os.fstat(fd), info):
                            raise OwnerTreeDeletionRefused("entry replaced: " + relative)
                        for child in sorted(os.listdir(fd)):
                            walk(fd, child, relative + "/" + child, depth + 1)
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    os.rmdir(name, dir_fd=parent)
                else:
                    # Unlinking a symlink or hardlink name never touches its target.
                    os.unlink(name, dir_fd=parent)
            except OSError as exc:
                raise OwnerTreeDeletionRefused(
                    "owner tree deletion failed: " + relative[:1024]) from exc
            removed += 1

        try:
            info = os.stat(center, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return {"removed": 0}
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (DAEMON_UID, machine):
            raise OwnerTreeDeletionRefused("center root is not this owner's migrated root")
        walk(root_fd, center, center, 0)
        os.fsync(root_fd)
        return {"removed": removed}
    finally:
        os.close(root_fd)


# --------------------------------------------------------------------------- #
# the two-pass driver
# --------------------------------------------------------------------------- #


def delete_center(root: str | Path, center: str, *, principal: str) -> dict[str, Any] | None:
    """Delete one migrated center with two capability-free passes, resumably.

    Returns ``None`` when the volume is a legacy single-UID layout and no
    two-pass deletion is pending: the caller's existing traversal applies.
    The caller holds the authenticated principal's identity context.
    """
    from tinyassets import role_owner_delete
    from tinyassets.auth.middleware import current_identity
    from tinyassets.owner_launcher_client import OwnerLaunchRefused

    center = _center_name(center)
    if os.name != "posix":
        return None  # the role split exists only on Linux
    root = Path(root)
    root_fd = os.open(os.fspath(root), _DIRECTORY)
    try:
        intent = _load_intent(root_fd, center)
        if intent is not None and intent["principal"] != principal:
            raise OwnerTreeDeletionRefused("a different principal's deletion is pending")
        try:
            info = os.stat(center, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if intent is None and not _layout_split(root_fd):
            return None
        if current_identity().user_id != principal:
            raise OwnerTreeDeletionRefused("deletion principal is not the admitted identity")
        if intent is None and info is None:
            return _retire_missing(root, center)
        if intent is None:
            from tinyassets.broker.owner_identities import owner_identity

            machine = owner_identity(root, principal=principal).gid
            if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid)
                    != (DAEMON_UID, machine)):
                raise OwnerTreeDeletionRefused("center is not this owner's migrated root")
            intent = {"center": center, "principal": principal,
                      "token": secrets.token_hex(16), "machine": machine}
            _write_intent(root_fd, intent)
    finally:
        os.close(root_fd)

    token, machine = intent["token"], intent["machine"]
    receipt: dict[str, Any] = {"center": center, "resumed": info is None}
    if info is not None:
        # Pass one again on every resume: an exact-token retry is admitted and
        # reinstalls the fence a launcher restart dropped, so finish matches.
        receipt["owner_pass"] = role_owner_delete.begin(root / center, token=token)
        receipt["daemon_pass"] = daemon_pass(root, center, machine=machine)
    # DA6: retire before finish, on the normal path and the tree-gone resume;
    # the fence outlives the binding until finish releases it.
    receipt["retired"] = role_owner_delete.retire(root / center, token=token)
    try:
        role_owner_delete.finish(root / center, token=token)
        receipt["fence"] = "released"
    except OwnerLaunchRefused:
        if info is not None:
            raise
        # Both passes completed before an interruption; the tree is gone and a
        # launcher restart already dropped the fence. Nothing is left to release.
        logger.warning("owner deletion %s: fence already released", center)
        receipt["fence"] = "absent"
    root_fd = os.open(os.fspath(root), _DIRECTORY)
    try:
        _clear_intent(root_fd, center)
    finally:
        os.close(root_fd)
    return receipt


def _retire_missing(root: Path, center: str) -> dict[str, Any] | None:
    """F1 (b): an admitted center held on ``missing`` lost its tree with no deletion.

    No pass can run and no fence exists, so only its ``retire`` row remains;
    the mapper verifies it as an unbound no-op. A tree-less center the log never
    admitted has nothing to retire, and the caller's existing traversal applies.
    """
    from tinyassets import role_owner_delete
    from tinyassets.broker.owner_identities import CenterUnadmitted

    try:
        generation = role_owner_delete.retire(root / center, token=secrets.token_hex(16))
    except CenterUnadmitted:
        return None
    logger.warning("owner deletion %s: retired a missing center", center)
    return {"center": center, "resumed": True, "missing": True, "retired": generation,
            "fence": "absent"}


def abort_center(root: str | Path, center: str, *, principal: str) -> dict[str, Any]:
    """Explicit recovery: release a failed deletion's fence, record partial loss.

    Cancels the pending daemon pass by clearing the intent. Never rollback and
    never a deletion success; the center keeps whatever both passes left.
    """
    from tinyassets import role_owner_delete

    center = _center_name(center)
    root = Path(root)
    root_fd = os.open(os.fspath(root), _DIRECTORY)
    try:
        intent = _load_intent(root_fd, center)
        if intent is None or intent["principal"] != principal:
            raise OwnerTreeDeletionRefused("no matching deletion is pending")
        role_owner_delete.abort(root / center, token=intent["token"])
        _clear_intent(root_fd, center)
    finally:
        os.close(root_fd)
    logger.error("owner deletion %s aborted with partial deletion", center)
    return {"center": center, "aborted": True, "partial": True}
