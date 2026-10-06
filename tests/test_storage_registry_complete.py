"""No uncounted store: every on-disk name the code creates is classified.

account-storage-quota D3. A database or directory added under the data root that
no store measures is a place to put bytes nobody counts -- the silent kind of
quota evasion. This reads the SOURCE for every dotted on-disk name joined onto a
path or declared as a database filename, and fails until each is classified:
charged by a registered store (`ROOT_ENTRIES`), counted by the universe walk
(`UNIVERSE_ENTRIES`), or not under the data root at all (`ELSEWHERE_ENTRIES`).
"""

from __future__ import annotations

import re
from pathlib import Path

from tinyassets import storage_accounting as sa

SOURCE = Path(sa.__file__).resolve().parent

_JOINED = re.compile(r'(\S+) / "(\.[A-Za-z0-9_.-]+)"')
_DB_NAME = re.compile(r'"(\.?[A-Za-z0-9_-]+\.db)"')
#: A name joined onto a HOME directory (``Path.home() / ".x"``, ``home / ".x"``,
#: ``... / "home" / ".x"``) is a process or tool home, never the data root --
#: recognized by that shape, so no tool or channel needs naming here.
_HOME_OPERAND = re.compile(r'(home\(\)|\bhome|"home")\)*$', re.IGNORECASE)


def _names_in_source() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in SOURCE.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for operand, name in _JOINED.findall(text):
            if not _HOME_OPERAND.search(operand):
                found.setdefault(name, str(path.relative_to(SOURCE)))
        for name in _DB_NAME.findall(text):
            found.setdefault(name, str(path.relative_to(SOURCE)))
    return found


def test_every_on_disk_name_is_classified():
    classified = set(sa.ROOT_ENTRIES) | sa.UNIVERSE_ENTRIES | sa.ELSEWHERE_ENTRIES
    missing = {
        name: where for name, where in _names_in_source().items()
        if name not in classified
    }
    assert not missing, (
        "on-disk names no storage store accounts for -- add each to "
        "storage_accounting.ROOT_ENTRIES (with the store that charges it, or "
        "'platform: <why>'), UNIVERSE_ENTRIES, or ELSEWHERE_ENTRIES: "
        f"{missing}"
    )


def test_every_root_entry_names_a_real_store_or_the_platform():
    for name, where in sa.ROOT_ENTRIES.items():
        if where.startswith("platform:"):
            continue
        named = {part.split(" ")[0].strip("(),") for part in where.split(",")}
        assert named & set(sa.STORES), (
            f"{name}: {where!r} names no registered store; CLASSIFICATION is required: "
            "review generated UNCLASSIFIED entries as a charging store or platform: <why>"
        )


def test_the_production_root_listing_is_classified():
    """The data root as it stood on production, 2026-09-30 (read-only probe)."""
    production = {
        ".auth.db", ".automations.db", ".connect", ".consumer_liveness",
        ".effector_consents.db", ".engine_run_admissions.db", ".executors",
        ".hosted-model-auth.db", ".langgraph_runs.db", ".outbound-proxy",
        ".owner_devices.db", ".run-execution-locks", ".run-file-custody",
        ".run-file-operation-locks", ".run_recovery.lock", ".run_recovery.lock.pid",
        ".runs.db", ".runtime", ".tinyassets.db",
        ".universe-tool-slots", ".active_universe", ".scoped-reset.barrier",
    }
    assert production <= set(sa.ROOT_ENTRIES), production - set(sa.ROOT_ENTRIES)
