"""Admit the existing local box double for internal owner-reader tests."""


def owner_read(operation, root):
    from tinyassets.control_plane.binding import file_binding, use_binding

    binding = file_binding(root)
    assert binding is not None
    with use_binding(binding):
        return operation(root)
