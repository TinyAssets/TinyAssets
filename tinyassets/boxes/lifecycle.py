"""Center lifecycle executes only inside the owner's volume (C11)."""

from pathlib import Path

from tinyassets.owner_centers import Centers
from tinyassets.owner_runtime import require


def admit_center(data_root: str | Path, *, principal: str, center: str) -> None:
    runtime = require(Path(data_root) / center, principal)
    Centers(runtime.root).create(center)


def retire_center(center: str) -> None:
    from tinyassets.owner_runtime import current

    runtime = current()
    if runtime is None:
        raise PermissionError("center retirement requires the owner's box")
    Centers(runtime.root).retire(center)
