"""Importing the model registry never registers host-specific executors."""

import importlib
import sys

from tests.support.provider_import import isolated_provider_import


def test_registry_imports_without_host_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with isolated_provider_import():
        sys.modules.pop("tinyassets.providers.call", None)
        registry = importlib.import_module("tinyassets.providers.call")
        assert set(registry.get_provider_router().available_providers) <= {
            "codex", "claude-code",
        }
