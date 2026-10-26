"""Root-owned deployment metadata; never opens an owner's tree."""
from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import threading
from pathlib import Path

from tinyassets.boxes.provider import BoxError

#: Shared with deploy/deploy_fail_safe.sh and scripts/daemon_image_retention.py.
HARNESS_LOCK = ".harness.lock"


@contextlib.contextmanager
def harness_lease(directory: Path):
    """Hold a harness from image selection until the sandbox bundle names it.

    Pruning takes this lock exclusively from its bundle scan through deletion, so
    a launch that chose an image is either visible in a bundle or not yet started.
    """
    import fcntl

    fd = os.open(Path(directory) / HARNESS_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        yield
    finally:
        os.close(fd)


class Images:
    def __init__(self, directory: Path, state: Path, initial: Path):
        self.directory, self.state = directory.resolve(strict=True), state
        self.lock = threading.RLock()
        # initial is the resolved harness-current target at process start.
        # The recorded name can still be on disk then; prune deletes it later.
        self._initial = initial.name
        self._current = self._initial
        if state.exists():
            document = json.loads(state.read_text())
            recorded = document.get("current")
            if isinstance(recorded, str) and (self.directory / recorded).is_dir():
                self._current = recorded
        self.path(self.current)

    @property
    def current(self) -> str:
        if (not (self.directory / self._current).is_dir()
                and (self.directory / self._initial).is_dir()):
            self._current = self._initial
            self._write(self._current)
        return self._current

    @current.setter
    def current(self, image: str) -> None:
        self._current = image

    def path(self, image: str) -> Path:
        if not isinstance(image, str) or not re.fullmatch(r"harness-[0-9a-f]{64}", image):
            raise BoxError("invalid immutable harness image")
        path = self.directory / image
        if path.is_symlink() or not path.is_dir():
            raise BoxError("immutable harness image unavailable")
        if (path / ".box-image-id").read_text().strip() != "sha256:" + image[8:]:
            raise BoxError("harness image identity mismatch")
        return path

    def compatible(self, image: str) -> bool:
        # Do not execute code from a rollback candidate. Missing/old declarations
        # are across the cutover boundary even before the first owner write.
        path = self.path(image)
        try:
            tree = ast.parse((path / "app/tinyassets/storage_layout.py").read_text())
        except (OSError, SyntaxError):
            return False
        return any(isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "ACTIVE_BOX_LAYOUT"
                           for t in node.targets)
                   and isinstance(node.value, ast.Constant)
                   and node.value.value == "owner-box" for node in tree.body)

    def _write(self, image: str) -> None:
        self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.state.with_suffix(".new")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"current": image}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.state)
        fd = os.open(self.state.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def save(self):
        self._write(self._current)

    def select(self, image):
        with self.lock:
            if not self.compatible(image):
                raise BoxError("cutover boundary: restore with deploy/backup-restore.sh")
            self.current = image
            self.save()
