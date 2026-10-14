from __future__ import annotations

import multiprocessing
import queue
import threading

import pytest


def _enter_exclusive_in_child(universe: str, events) -> None:
    from tinyassets.provider_assignment import ProviderAssignmentAdmission

    events.put("attempting")
    with ProviderAssignmentAdmission().exclusive(universe):
        events.put("entered")


def test_shared_launch_fence_blocks_assignment_writer_until_release(tmp_path):
    from tinyassets.provider_assignment import ProviderAssignmentAdmission

    admission = ProviderAssignmentAdmission()
    universe = tmp_path / "u-fenced"
    writer_started = threading.Event()
    writer_entered = threading.Event()

    def _writer() -> None:
        writer_started.set()
        with admission.exclusive(universe):
            writer_entered.set()

    with admission.shared(universe):
        thread = threading.Thread(target=_writer)
        thread.start()
        assert writer_started.wait(timeout=1)
        assert not writer_entered.wait(timeout=0.05)

    thread.join(timeout=1)
    assert not thread.is_alive()
    assert writer_entered.is_set()


def test_assignment_admission_refuses_reentrant_and_reverse_order(tmp_path):
    from tinyassets.provider_assignment import ProviderAssignmentAdmission

    admission = ProviderAssignmentAdmission()
    universe = tmp_path / "u-fenced"
    with admission.shared(universe):
        with pytest.raises(RuntimeError, match="not reentrant"):
            with admission.shared(universe):
                pass
        with pytest.raises(RuntimeError, match="not reentrant"):
            with admission.exclusive(universe):
                pass


def test_shared_launch_fence_blocks_writer_in_another_process(tmp_path):
    from tinyassets.provider_assignment import ProviderAssignmentAdmission

    universe = tmp_path / "u-fenced"
    universe.mkdir()
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    process = context.Process(
        target=_enter_exclusive_in_child,
        args=(str(universe), events),
    )
    admission = ProviderAssignmentAdmission()
    with admission.shared(universe):
        process.start()
        assert events.get(timeout=5) == "attempting"
        with pytest.raises(queue.Empty):
            events.get(timeout=0.25)
    assert events.get(timeout=5) == "entered"
    process.join(timeout=5)
    assert process.exitcode == 0


def test_windows_file_lock_retry_is_bounded_and_fails_closed():
    from tinyassets.provider_assignment import _acquire_windows_file_lock

    attempts = 0

    class _Handle:
        @staticmethod
        def seek(_offset):
            return 0

        @staticmethod
        def fileno():
            return 17

    def _always_contended(_fileno, _mode, _count):
        nonlocal attempts
        attempts += 1
        raise OSError("contended")

    with pytest.raises(TimeoutError, match="admission lock"):
        _acquire_windows_file_lock(
            _Handle(),
            locking=_always_contended,
            sleep=lambda _seconds: None,
            max_attempts=3,
        )

    assert attempts == 3


@pytest.mark.parametrize("component", [
    "namespace", "center", "leaf", "hardlink", "writable", "base_writable", "fifo",
])
def test_platform_lock_rejects_mutable_aliases_without_touching_owner_files(tmp_path, component):
    from tinyassets.provider_assignment import ProviderAssignmentAdmission

    admission = ProviderAssignmentAdmission()
    universe = tmp_path / "cc"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "target"
    target.write_bytes(b"keep exact bytes")
    namespace = tmp_path / ".agent-sessions"
    center = namespace / "cc"
    if component == "namespace":
        namespace.symlink_to(outside, target_is_directory=True)
    else:
        namespace.mkdir()
        if component == "center":
            center.symlink_to(outside, target_is_directory=True)
        else:
            center.mkdir()
            leaf = center / ".provider-assignment-admission.lock"
            if component == "leaf":
                leaf.symlink_to(target)
            elif component == "hardlink":
                leaf.hardlink_to(target)
            elif component == "writable":
                center.chmod(0o777)
            elif component == "base_writable":
                tmp_path.chmod(0o777)
            else:
                import os
                os.mkfifo(leaf)
    with pytest.raises((OSError, RuntimeError)):
        with admission.shared(universe):
            pytest.fail("mutable platform path was admitted")
    assert target.read_bytes() == b"keep exact bytes"
    assert not universe.exists()
