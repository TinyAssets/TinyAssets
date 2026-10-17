"""BUG-025 — provider binary probe at registration time.

Guards:
- ClaudeProvider.is_available() returns False when 'claude' binary is absent.
- ClaudeProvider.is_available() returns True when binary is present.
- _provider_stub skips ClaudeProvider registration when binary absent.
- Shipped external agents register by package, independent of host binaries.
- BaseProvider.is_available() defaults to True (non-binary providers unaffected).
"""
from __future__ import annotations

import importlib
import sys

import pytest

from tests.support.provider_import import isolated_provider_import


def _reload_stub():
    mod_name = "tinyassets.providers.call"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


@pytest.fixture
def reset_stub():
    with isolated_provider_import():
        yield


class TestIsAvailableClassmethod:




    def test_base_provider_defaults_to_true(self):
        from tinyassets.providers.base import BaseProvider

        class _Dummy(BaseProvider):
            name = "dummy"
            family = "test"

            async def complete(self, prompt, system, config, *, universe_dir=None):
                raise NotImplementedError

        assert _Dummy.is_available() is True


