#!/usr/bin/env python3
"""Rehearse the final owner cutover; reports contain counts and fixed labels only."""

import re
import sys
import tarfile
from pathlib import Path

ARCHIVE_NAME = re.compile(r"tinyassets-data-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.tar\.gz\Z")


def validate_archive(path: Path) -> int:
    """Apply backup-restore.sh's member rules before mounting or extracting."""
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("--archive requires an absolute readable non-symlink regular file")
    if not ARCHIVE_NAME.fullmatch(path.name):
        raise ValueError("--archive requires a tinyassets-data-YYYY-MM-DDTHH-MM-SSZ.tar.gz file")
    total = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise ValueError("archive is empty")
            symlinks = {member.name.rstrip("/") for member in members if member.issym()}
            for member in members:
                name = member.name.rstrip("/") if member.isdir() else member.name
                parts = name.split("/")
                if (
                    name.startswith("/")
                    or not parts
                    or parts[0] != "_data"
                    or any(part in ("", ".", "..") for part in parts)
                ):
                    raise ValueError(f"unsafe archive path: {name!r}")
                if len(parts) == 1 and not member.isdir():
                    raise ValueError(f"archive root is not a directory: {name!r}")
                if any("/".join(parts[:index]) in symlinks for index in range(1, len(parts))):
                    raise ValueError(f"archive descends through a symlink: {name!r}")
                if member.islnk():
                    target = member.linkname.split("/")
                    if (
                        member.linkname.startswith("/")
                        or not target
                        or target[0] != "_data"
                        or ".." in target
                        or member.linkname in symlinks
                    ):
                        raise ValueError(f"unsafe archive hardlink: {name!r}")
                elif not (member.isfile() or member.isdir() or member.issym()):
                    raise ValueError(f"unsupported archive member type: {name!r}")
                total += member.size if member.isfile() else 0
    except (OSError, tarfile.TarError) as exc:
        raise ValueError(f"archive unreadable: {type(exc).__name__}") from exc
    return total


def restore_replay_probe(mode: str) -> int:
    """Synthetic operations in the disposable venue, through real owner tools."""
    import asyncio
    import json
    import os
    import runpy
    import sqlite3
    import subprocess

    from tinyassets import owner_runtime
    from tinyassets.agent_loop.owner_tools import OwnerTools
    from tinyassets.boxes.boxd import OwnerBoxd
    from tinyassets.boxes.provider import BoxError
    from tinyassets.router_credentials import DeletedOwners

    data, store, backup = Path("/data"), Path("/box-store"), Path("/backup/replay")
    backup.mkdir(exist_ok=True)
    with sqlite3.connect(data / ".broker/state/owner-identities.db") as db:
        owners = db.execute("SELECT principal,machine FROM box_records WHERE machine<>399999"
                            ).fetchall()
    proof = backup / "proof.json"
    epochs = json.loads(proof.read_text()) if mode == "verify" else {}
    for owner, uid in owners:
        tree = store / str(uid)
        core = OwnerBoxd(owner_id=owner, owner_root=tree, state_dir=tree / ".owner-boxd")
        try:
            handle = core.bind(owner)
            owner_runtime._runtime = owner_runtime.OwnerRuntime(owner, uid, tree, os.getpid(), core)
            tools = OwnerTools(tree)
            if mode == "snapshot":
                core.write(handle, "rehearsal-before", "/cc/.replay-proof", b"snapshot",
                           max_bytes=100)
                epochs[owner] = handle.epoch
                asyncio.run(tools.bash("rehearsal-bash", "echo once >> .bash-proof"))
            elif mode == "write":
                core.write(handle, "rehearsal-after", "/cc/.replay-proof", b"after", max_bytes=100)
            elif mode == "verify":
                if handle.epoch <= epochs[owner]:
                    raise RuntimeError("owner epoch did not advance")
                if (tree / ".replay-proof").read_bytes() != b"snapshot":
                    raise RuntimeError("owner snapshot was not restored")
                core.write(handle, f"{handle.epoch}:rehearsal-later", "/cc/.replay-proof",
                           b"later work", max_bytes=100)
                for op_id, content in (("rehearsal-before", b"snapshot"),
                                       ("rehearsal-after", b"after")):
                    try:
                        core.write(handle, op_id, "/cc/.replay-proof", content, max_bytes=100)
                    except BoxError as exc:
                        if "not re-run" not in str(exc):
                            raise
                    else:
                        raise RuntimeError("restored operation was admitted again")
                if (tree / ".replay-proof").read_bytes() != b"later work":
                    raise RuntimeError("replayed operation changed later work")
                try:
                    asyncio.run(tools.bash("rehearsal-bash", "echo once >> .bash-proof"))
                except BoxError as exc:
                    if "not re-run" not in str(exc):
                        raise
                else:
                    raise RuntimeError("restored bash operation was admitted again")
                if (tree / ".bash-proof").read_text() != "once\n":
                    raise RuntimeError("replayed bash changed restored work")
            else:
                raise ValueError("unknown replay proof phase")
        finally:
            owner_runtime._runtime = None
            core.close()
    if mode == "snapshot":
        # A separate disposable owner preserves the per-owner replay proof
        # while exercising complete post-snapshot deletion in the same restore.
        with sqlite3.connect(data / ".broker/state/owner-identities.db") as db:
            db.execute("INSERT INTO owner_identities VALUES ('restore-erased',399999)")
            db.execute("INSERT INTO box_records VALUES ('restore-erased',399999,399999,1048576)")
        erased_tree = store / "399999"
        erased_tree.mkdir(mode=0o700)
        (erased_tree / "private").write_bytes(b"synthetic erased content")
        os.chown(erased_tree, 399999, 399999)
        import hashlib

        from tinyassets.api_keys import ApiKeyStore
        from tinyassets.broker.ops import new_op_id
        from tinyassets.onboarding import owner_sessions
        from tinyassets.router_credentials import HookOwners

        credential = ApiKeyStore(data).mint(owners[0][0], name="synthetic", scopes=[],
                                            authority=None)["key"]
        HookOwners(data).publish(hashlib.sha256(b"rehearsal-hook").hexdigest(), owners[0][0])
        with owner_sessions.store() as db:
            db.execute("INSERT INTO owner_sessions VALUES (?,?,?)",
                       (owner_sessions.hashed("rehearsal-session"),
                        json.dumps({"user_id": owners[0][0]}), 4000000000))
        (backup / "credentials.json").write_text(json.dumps({
            "key": credential, "op": new_op_id()}))
        proof.write_text(json.dumps(epochs))
        stamp = "2026-10-17T01-00-00Z"
        shared = backup / f"tinyassets-data-{stamp}.tar.gz"
        boxes = backup / f"tinyassets-box-store-{stamp}.tar.gz"
        subprocess.run(["tar", "-czf", str(shared), "--transform=s,^data,_data,",
                        "-C", "/", "--numeric-owner", "--acls", "--xattrs", "data"], check=True)
        subprocess.run(["tar", "-czf", str(boxes), "--transform=s,^,owners/,",
                        "-C", str(store), "--numeric-owner", "--acls", "--xattrs",
                        *(str(uid) for _, uid in owners), "399999"], check=True)
        runpy.run_path("/support/deploy/backup-pair.py")["create"](
            shared, boxes, backup / f"tinyassets-pair-{stamp}.json")
    elif mode == "write":
        from tinyassets.boxes.destroy import tree as destroy_tree

        DeletedOwners(data).add("restore-erased", {"unfinished_phases": []})
        with sqlite3.connect(data / ".broker/state/owner-identities.db") as db:
            db.execute("INSERT INTO owner_box_revocations VALUES ('restore-erased',1)")
        destroy_tree(store, 399999, erase=True)
        from tinyassets.router_traffic import open_traffic

        open_traffic(data)
    elif mode == "verify":
        if (store / "399999").exists() or not DeletedOwners(data).contains("restore-erased"):
            raise RuntimeError("restored erased owner returned")
        from tinyassets.api_keys import ApiKeyStore
        from tinyassets.broker.ops import OpStore
        from tinyassets.onboarding import owner_sessions
        from tinyassets.router_credentials import HookOwners

        credentials = json.loads((backup / "credentials.json").read_text())
        if (ApiKeyStore(data).resolve(credentials["key"]) is not None
                or HookOwners(data).resolve("rehearsal-hook") is not None
                or owner_sessions.lookup("rehearsal-session") is not None):
            raise RuntimeError("stale credential still verifies")
        if OpStore(data / ".broker/ops.db").admit(
                "rehearsal-outbound", credentials["op"], "digest").kind != "expired":
            raise RuntimeError("pre-restore outbound operation admitted")
    return len(owners)


def archive_rehearsal(image, archive, *, store_bytes):
    from scripts.archive_rehearsal import archive_rehearsal as rehearse

    return rehearse(image, archive, store_bytes=store_bytes)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.archive_rehearsal import main

    main()
