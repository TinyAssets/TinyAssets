"""Old-layout extraction for the one-shot migration's pre-traffic rollback.

This unit restores into a new, private staging directory only. Deployment
control owns durable eligibility, stopped-service installation and startup (C4).
It is never a platform or owner-volume recovery path.
"""

import json
import os
import subprocess
import tarfile
from pathlib import PurePosixPath

from tinyassets.box_layout_migration import _verified_archive
from tinyassets.cutover_sqlite import _repair_staged_databases


def restore_old_layout(archive, destination):
    _verified_archive(archive, fresh=False)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        root_metadata = source.getmember("_data")
        symlinks = {member.name.rstrip("/") for member in members if member.issym()}
        if not members:
            raise ValueError("empty old-layout backup")
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or not path.parts
                or path.parts[0] != "_data"
                or ".." in path.parts
                or any(str(parent) in symlinks for parent in path.parents)
            ):
                raise ValueError("unsafe old-layout backup path")
            if member.islnk():
                target = PurePosixPath(member.linkname)
                if (
                    target.is_absolute()
                    or not target.parts
                    or target.parts[0] != "_data"
                    or ".." in target.parts
                    or str(target) in symlinks
                ):
                    raise ValueError("unsafe old-layout backup hardlink")
            elif not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError("unsupported old-layout backup member")
    destination.mkdir(mode=0o700)
    subprocess.run(
        [
            "tar",
            "-xzf",
            str(archive),
            "-C",
            str(destination),
            "--strip-components=1",
            "--numeric-owner",
            "--acls",
            "--xattrs",
        ],
        check=True,
    )
    marker = destination / ".layout.json"
    layout = json.loads(marker.read_text())
    if layout.get("box_layout", layout.get("split")) != "owner-split":
        raise ValueError("rollback requires the pre-cutover old layout")
    _repair_staged_databases(destination)
    os.chown(destination, root_metadata.uid, root_metadata.gid)
    destination.chmod(root_metadata.mode)
    return destination
