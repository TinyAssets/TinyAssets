"""Admit the existing local box double for internal owner-reader tests."""
import os

import pytest


def owner_read(operation, root):
    if os.name != "posix":
        pytest.skip("the admitted local owner box requires POSIX; run in the Linux oracle")
    from tinyassets.control_plane.binding import file_binding, use_binding

    binding = file_binding(root)
    assert binding is not None
    with use_binding(binding):
        return operation(root)
