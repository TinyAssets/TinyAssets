"""Root-owned deployment metadata; never opens an owner's tree."""
from __future__ import annotations

import ast
import json
import os
import re
import threading
from pathlib import Path

from tinyassets.boxes.provider import BoxError


class Images:
    def __init__(self, directory: Path, state: Path, initial: Path):
        self.directory, self.state = directory.resolve(strict=True), state
        self.lock = threading.RLock()
        self.current = initial.name
        if state.exists():
            document = json.loads(state.read_text())
            recorded = document.get("current")
            # A pruned harness name must not stick. initial is the resolved
            # target of harness-current.
            if isinstance(recorded, str) and (self.directory / recorded).is_dir():
                self.current = recorded
        self.path(self.current)

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

    def save(self):
        self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.state.with_suffix(".new")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"current": self.current}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.state)
        fd = os.open(self.state.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def select(self, image):
        with self.lock:
            if not self.compatible(image):
                raise BoxError("cutover boundary: restore with deploy/backup-restore.sh")
            self.current = image
            self.save()
