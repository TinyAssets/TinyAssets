"""Explicit owner-bound memory lifetime for C7 sessions and their children.

Enter after authenticated C6 admission, before graph execution. Await all children
before exit. Context inheritance carries this exact binding, never serialized
state or a host path. No process-global memory object can cross owner sessions.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.records import SourceRecords, source_census
from tinyassets.okf.sql_index import reviewed_rows

_MEMORY = ContextVar("canonical_owner_memory", default=None)
SOURCES = ("daemon", "project", "episodic", "knowledge", "promises", "versions", "temporal")


class OwnerMemory:
    def __init__(self, *, owner, center, namespace):
        root = SourceRecords.bind(owner=owner, center=center, source="project")
        self.sources = {name: SourceRecords(root.bundle, name) for name in SOURCES}
        before = root.bundle.snapshot()
        for source in self.sources.values():
            reviewed_rows(source, before)
        for name in ("episodic", "knowledge", "promises", "versions"):
            for definition in source_census(before, name)["tables"].values():
                if namespace not in definition["scope"].get("universe_id", []):
                    raise InvalidBundle("Session namespace has no reviewed canonical binding")
        self._closed = False
        self.namespace = namespace
        self._objects = {}
        root.bundle.access_check = self._check_open

    def source(self, name):
        self._check_open()
        return self.sources[name]

    def get(self, name):
        self._check_open()
        if name not in self._objects:
            from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
            from tinyassets.memory.manager import MemoryManager
            from tinyassets.memory.promises import SeriesPromiseTracker
            from tinyassets.memory.temporal import TemporalFactStore
            from tinyassets.memory.versioning import OutputVersionStore

            factories = {
                "knowledge_graph": lambda: KnowledgeGraph(self.source("knowledge")),
                "memory_manager": lambda: MemoryManager(self.namespace, self.source("episodic")),
                "version_store": lambda: OutputVersionStore(
                    self.source("versions"), self.namespace
                ),
                "promise_tracker": lambda: SeriesPromiseTracker(
                    self.source("promises"), self.namespace
                ),
                "temporal_store": lambda: TemporalFactStore(self.source("temporal")),
            }
            self._objects[name] = factories[name]()
        return self._objects[name]

    def _check_open(self):
        if self._closed:
            raise InvalidBundle("Owner memory session has ended")
        if _MEMORY.get() is not self:
            raise InvalidBundle("Memory source is outside its current owner session")

    def close(self):
        self._closed = True
        for item in self._objects.values():
            item.close()


def current_memory():
    value = _MEMORY.get()
    if value is None:
        raise InvalidBundle("Memory requires an authenticated owner session binding")
    return value


@contextmanager
def owner_memory_scope(*, owner, center, namespace):
    memory = OwnerMemory(owner=owner, center=center, namespace=namespace)
    token = _MEMORY.set(memory)
    try:
        yield memory
    finally:
        _MEMORY.reset(token)
        memory.close()
