"""The host serves nothing before the one-shot owner-box commit."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from deploy import boxhostd

pytestmark = pytest.mark.skipif(os.name != "posix", reason="boxhostd is Linux-only")


def _marker(root: Path, *, box_layout: str | None = None, state: str = "stable") -> None:
    document = {"layout": 2, "state": state, "split": "owner-split"}
    if box_layout is not None:
        document["box_layout"] = box_layout
    path = root / ".layout.json"
    path.write_text(json.dumps(document))
    os.chown(path, 1001, 1001)


def test_live_service_requires_committed_marker(tmp_path):
    if os.geteuid() != 0:
        pytest.skip(
            "requires linux_oracle.py --as-root; owner=c6-owner-box runs-in=linux_oracle.py"
        )
    assert boxhostd.owner_box_committed(tmp_path) is False
    _marker(tmp_path)
    assert boxhostd.owner_box_committed(tmp_path) is False
    _marker(tmp_path, box_layout="owner-box")
    assert boxhostd.owner_box_committed(tmp_path) is True
    _marker(tmp_path, box_layout="owner-box", state="migrating")
    with pytest.raises(RuntimeError, match="not stable"):
        boxhostd.owner_box_committed(tmp_path)
