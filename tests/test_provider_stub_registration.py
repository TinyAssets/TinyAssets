"""The provider call module boots its fallback router with no credentials."""
from __future__ import annotations

import importlib
import sys

import pytest

from tests.support.provider_import import isolated_provider_import


def _reload_stub():
    """Force a fresh import of the stub so module-level registration reruns."""
    mod_name = "tinyassets.providers.call"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


@pytest.fixture
def reset_stub():
    with isolated_provider_import():
        yield


class TestFallbackRouterBoot:
    def test_stub_importable_even_when_all_providers_fail(
        self, monkeypatch, reset_stub
    ):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        stub = _reload_stub()

        assert stub is not None
        assert hasattr(stub, "call_provider")
