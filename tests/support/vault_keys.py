"""Explicit synthetic root shared by test processes; never used by application code."""
from pathlib import Path

from tinyassets.broker import key_service


def install(root):
    root = Path(root).resolve()
    with key_service._lock:
        if root not in key_service._services:
            key_service._services[root] = key_service.KeyService(
                root, key_service.memory_test_root(root))
    return key_service._services[root]
