"""The data-layout guard (C4a of the command-center cutover, design D7.2).

An image must never run against data it does not understand, and a crash in a
migration must leave a marker every image refuses. Ships alone, before any
migration exists, so it is the production baseline when one runs.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tinyassets import storage_layout as layout

REPO = Path(__file__).resolve().parents[1]


def _write(base: Path, document) -> None:
    (base / layout.MARKER).write_text(
        document if isinstance(document, str) else json.dumps(document), encoding="utf-8",
    )


def test_a_fresh_data_dir_gets_the_layout_this_code_writes(tmp_path):
    assert layout.check(tmp_path) == {"layout": 1, "state": "stable"}
    assert json.loads((tmp_path / layout.MARKER).read_text(encoding="utf-8")) == {
        "layout": 1, "state": "stable",
    }
    assert layout.check(tmp_path) == {"layout": 1, "state": "stable"}  # idempotent


@pytest.mark.parametrize("document,reason", [
    ({"layout": 1, "state": "migrating"}, "did not finish"),
    ({"layout": 2, "state": "stable"}, "understands"),
    ({"state": "stable"}, "understands"),
    ("{not json", "not valid JSON"),
    ("[1]", "not a JSON object"),
])
def test_data_this_code_does_not_understand_is_refused(tmp_path, document, reason):
    _write(tmp_path, document)
    with pytest.raises(layout.LayoutRefused, match=reason):
        layout.check(tmp_path)


def test_the_check_command_exits_3_on_refusal_and_0_otherwise(tmp_path):
    assert layout.main(["check", str(tmp_path)]) == 0
    _write(tmp_path, {"layout": 1, "state": "migrating"})
    assert layout.main(["check", str(tmp_path)]) == 3


@pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX")
def test_a_running_server_holds_the_shared_lock_a_migration_needs_exclusively(tmp_path):
    import fcntl

    layout.require_layout(tmp_path)
    try:
        with open(tmp_path / layout.LOCK, "a+") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(other.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)  # readers coexist
    finally:
        layout.release_for_tests()


def test_the_server_refuses_before_admission_or_any_database(monkeypatch, tmp_path):
    from tinyassets import platform_runtime_provenance, universe_server
    from tinyassets.onboarding import session_store

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(session_store, "arm", lambda: None)
    reached = []
    monkeypatch.setattr(
        platform_runtime_provenance, "require_process_cloud_admission",
        lambda **_k: reached.append("admission"),
    )
    _write(tmp_path, {"layout": 1, "state": "migrating"})
    with pytest.raises(layout.LayoutRefused):
        universe_server.main()
    assert reached == []


# ---------------------------------------------------------------------------
# deploy/deploy_fail_safe.sh: no image-only rollback onto migrated data
# ---------------------------------------------------------------------------

_BASH = shutil.which("bash")


def _guard_function() -> str:
    script = (REPO / "deploy" / "deploy_fail_safe.sh").read_text(encoding="utf-8")
    match = re.search(r"^layout_allows_any_image\(\) \{.*?^\}\n", script, re.S | re.M)
    assert match, "deploy_fail_safe.sh lost layout_allows_any_image"
    return match.group(0)


def _bash_has_python3() -> bool:
    if _BASH is None:
        return False
    probe = subprocess.run([_BASH, "-c", "python3 -c pass"], capture_output=True, text=True)
    return probe.returncode == 0


@pytest.mark.skipif(not _bash_has_python3(), reason="needs bash with python3, as the host has")
@pytest.mark.parametrize("document,allowed", [
    (None, True),
    ({"layout": 1, "state": "stable"}, True),
    ({"layout": 1, "state": "migrating"}, False),
    ({"layout": 2, "state": "stable"}, False),
    ("garbage", False),
])
def test_the_fail_safe_refuses_an_image_rollback_onto_migrated_data(tmp_path, document, allowed):
    marker = tmp_path / layout.MARKER
    if document is not None:
        marker.write_text(document if isinstance(document, str) else json.dumps(document),
                          encoding="utf-8")
    program = _guard_function() + f'layout_allows_any_image "{marker.as_posix()}"\n'
    result = subprocess.run([_BASH, "-c", program], capture_output=True, text=True,
                            env={**os.environ})
    assert (result.returncode == 0) is allowed, result.stderr


def test_the_fail_safe_checks_the_layout_before_every_image_start():
    """Both the converge of the target image (any mode, --restore-bundle
    included) and the rollback to the previous image pass the guard first."""
    script = (REPO / "deploy" / "deploy_fail_safe.sh").read_text(encoding="utf-8")
    first = script.index('layout_allows_any_image "$LAYOUT_MARKER"')
    install = script.index("# --- 3b. the runtime bundle")
    restore = script.index('if [ "$RESTORE_BUNDLE" = "1" ]; then\n  # Undoing an install')
    assert first < install < restore
    second = script.index('layout_allows_any_image "$LAYOUT_MARKER"', first + 1)
    rollback = script.index('if ! set_image "$PREV_IMAGE"; then')
    assert install < second < rollback
    assert "refuse_on_layout" in script[second:rollback]
    refusal = script[script.index("refuse_on_layout() {"):]
    assert 'docker stop "$DAEMON_CONTAINER"' in refusal.split("}", 1)[0]


@pytest.mark.skipif(not _bash_has_python3() or sys.platform == "win32",
                    reason="needs bash, python3 and flock, as the host has")
def test_the_fail_safe_refuses_while_a_migration_holds_the_lock(tmp_path):
    import fcntl

    marker = tmp_path / layout.MARKER
    marker.write_text('{"layout": 1, "state": "stable"}', encoding="utf-8")
    program = _guard_function() + f'layout_allows_any_image "{marker.as_posix()}"\n'
    with open(tmp_path / layout.LOCK, "a+") as migration:
        fcntl.flock(migration.fileno(), fcntl.LOCK_EX)
        held = subprocess.run([_BASH, "-c", program], capture_output=True, text=True)
    free = subprocess.run([_BASH, "-c", program], capture_output=True, text=True)
    assert held.returncode != 0 and free.returncode == 0


@pytest.mark.skipif(not _bash_has_python3(), reason="needs bash with python3")
def test_the_fail_safe_treats_an_unreadable_volume_as_a_refusal(tmp_path):
    marker = tmp_path / "no-such-volume" / layout.MARKER
    program = _guard_function() + f'layout_allows_any_image "{marker.as_posix()}"\n'
    assert subprocess.run([_BASH, "-c", program]).returncode != 0


def test_the_backup_takes_the_shared_layout_lock_before_reading_the_volume():
    script = (REPO / "deploy" / "backup.sh").read_text(encoding="utf-8")
    lock = script.index('flock -s -w 600 9')
    marker = script.index('doc.get("state") == "stable"')
    first_read = script.index('for d in wiki daemon_wikis; do')
    assert lock < marker < first_read, "lock, then refuse a half-migrated volume, then read"


@pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX")
def test_a_writer_waiting_on_a_migration_sees_the_marker_it_left(tmp_path):
    """Check-then-lock would let a process pass a check, wait out a migration and
    run on its result unchecked. Lock-then-check reads what the migration left."""
    import fcntl
    import threading

    layout.check(tmp_path)  # layout 1, stable
    outcome = {}

    def start():
        try:
            outcome["doc"] = layout.require_layout(tmp_path)
        except layout.LayoutRefused as exc:
            outcome["refused"] = str(exc)

    with open(tmp_path / layout.LOCK, "a+") as migration:
        fcntl.flock(migration.fileno(), fcntl.LOCK_EX)
        waiter = threading.Thread(target=start)
        waiter.start()
        waiter.join(0.5)
        assert waiter.is_alive(), "require_layout must wait for the migration's lock"
        _write(tmp_path, {"layout": 1, "state": "migrating"})  # the crash it left behind
    waiter.join(5)
    layout.release_for_tests()
    assert "did not finish" in outcome.get("refused", ""), outcome


def test_concurrent_first_starts_write_one_marker(tmp_path):
    import threading

    docs, errors = [], []

    def start():
        try:
            docs.append(layout.check(tmp_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and len(docs) == 8
    assert not list(tmp_path.glob(".layout.json.*.tmp"))


@pytest.mark.skipif(sys.platform == "win32" or getattr(os, "geteuid", lambda: 1)() == 0,
                    reason="POSIX permissions, as a non-root service user")
def test_a_lock_file_another_user_created_can_still_be_locked(tmp_path):
    layout.check(tmp_path)
    (tmp_path / layout.LOCK).chmod(0o444)  # as root's backup.sh may leave it
    try:
        layout.require_layout(tmp_path)
    finally:
        layout.release_for_tests()
        (tmp_path / layout.LOCK).chmod(0o644)
