"""Select the committed owner storage layout for admission and deletion."""

from pathlib import Path


def admit_center(data_root: str | Path, *, principal: str, center: str) -> int:
    """Register a center from its already-admitted owner box."""
    from tinyassets.owner_channel import request

    return request("", {"op": "ADMIT_CENTER", "center": center})["generation"]
