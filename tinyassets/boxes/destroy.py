"""Remove runtime socket exports after stopping the owner sandbox.

Owner content is erased exclusively through OwnerVolumes.delete, never here.
"""

import shutil
from pathlib import Path

from tinyassets.boxes.gvisor_runtime import GvisorRuntime


def socket_tree(store: Path, uid: int) -> None:
    GvisorRuntime.box_id(uid)
    try:
        shutil.rmtree(store / str(uid))
    except FileNotFoundError:
        pass
