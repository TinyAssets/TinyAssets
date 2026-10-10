"""A jail binds exactly two sidecar paths, and nothing else under that folder.

The validator used to admit any bind source resolving under
``<data>/.universe-sidecars/<cc>/``, because the egress proxy socket lives
there and a jail has to reach it. A directory prefix is not a capability: a
provider could rename a directory the tool jail was about to bind read-write
and leave a link to the sidecar folder in its place. The link resolved inside
the allowed prefix, so the view was accepted and the command center got a
WRITABLE handle on daemon-owned state -- including the consent database that
decides what it is allowed to do.

The allowance is now the exact paths ``_network`` constructed for that launch,
passed to ``jail_argv`` as ``platform_sources``. These tests drive the real
validator.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tinyassets.providers.provider_jail import (
    UNIVERSE_SIDECARS_DIR,
    JailMount,
    ProviderConfinementError,
    UniverseView,
    jail_argv,
)


def _sidecar_dir(cc: Path) -> Path:
    return cc.parent / UNIVERSE_SIDECARS_DIR / cc.name


def _argv(cc: Path, mounts, platform_sources=frozenset()):
    return jail_argv(
        ["/bin/true"],
        UniverseView(universe_dir=cc, mounts=tuple(mounts), chdir="/u", setenv=()),
        bwrap_path="/usr/bin/bwrap",
        platform_sources=platform_sources,
    )


@pytest.fixture
def command_center(tmp_path: Path) -> Path:
    cc = tmp_path / "data" / "u-alpha"
    cc.mkdir(parents=True)
    _sidecar_dir(cc).mkdir(parents=True)
    return cc


def test_the_constructed_socket_is_bindable(command_center: Path):
    """The control: the one thing the allowance exists for still works."""
    socket = _sidecar_dir(command_center) / "egress.sock"
    socket.write_bytes(b"")

    argv = _argv(
        command_center,
        [JailMount("bind", "/run/egress.sock", socket)],
        platform_sources=frozenset({socket.resolve()}),
    )

    assert str(socket.resolve()) in argv


def test_another_file_in_the_sidecar_folder_is_refused(command_center: Path):
    """Same folder, not the constructed socket. Previously accepted on prefix."""
    other = _sidecar_dir(command_center) / ".effector_consents.db"
    other.write_bytes(b"")
    socket = _sidecar_dir(command_center) / "egress.sock"
    socket.write_bytes(b"")

    with pytest.raises(ProviderConfinementError, match="may not bind platform sidecar state"):
        _argv(
            command_center,
            [JailMount("bind", "/u/notes", other)],
            platform_sources=frozenset({socket.resolve()}),
        )


def test_the_sidecar_folder_itself_is_refused(command_center: Path):
    with pytest.raises(ProviderConfinementError, match="may not bind platform sidecar state"):
        _argv(command_center, [JailMount("bind", "/u/notes", _sidecar_dir(command_center))])


def test_with_no_constructed_sockets_nothing_in_the_sidecar_is_bindable(
    command_center: Path,
):
    """A launch with no egress mounts admits no sidecar source at all --
    ``platform_sources`` defaults to empty rather than to the folder."""
    socket = _sidecar_dir(command_center) / "egress.sock"
    socket.write_bytes(b"")

    with pytest.raises(ProviderConfinementError, match="may not bind platform sidecar state"):
        _argv(command_center, [JailMount("bind", "/run/egress.sock", socket)])


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlink on this host")
def test_a_swapped_link_to_the_sidecar_folder_is_refused(command_center: Path):
    """The attack, as a test.

    A provider renames the directory the tool jail is about to bind read-write
    and puts a link to the sidecar folder there. The validator resolves the
    source -- which is what makes the link land inside the sidecar -- and must
    refuse it, because the resolved path is not one of the constructed sockets.
    """
    notes = command_center / "notes"
    notes.mkdir()
    socket = _sidecar_dir(command_center) / "egress.sock"
    socket.write_bytes(b"")

    # The tool jail builds a read-write mount for a real directory...
    mount = JailMount("bind", "/u/notes", notes)
    # ...and before the argv is built, the name is swapped for a link out.
    notes.rmdir()
    try:
        os.symlink(_sidecar_dir(command_center), notes, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")

    with pytest.raises(ProviderConfinementError, match="may not bind platform sidecar state"):
        _argv(command_center, [mount], platform_sources=frozenset({socket.resolve()}))


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlink on this host")
def test_a_link_to_the_constructed_socket_is_still_only_that_socket(
    command_center: Path,
):
    """A link that resolves TO an allowed source is allowed -- the rule is about
    the resolved identity, not the spelling. This pins that the exact-set check
    compares resolved paths, so an equivalent spelling is not a bypass and not
    a false refusal either."""
    socket = _sidecar_dir(command_center) / "egress.sock"
    socket.write_bytes(b"")
    alias = command_center / "egress-alias.sock"
    try:
        os.symlink(socket, alias)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create a symlink")

    argv = _argv(
        command_center,
        [JailMount("bind", "/run/egress.sock", alias)],
        platform_sources=frozenset({socket.resolve()}),
    )

    assert str(socket.resolve()) in argv
