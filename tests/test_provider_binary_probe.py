"""BUG-025 — provider binary probe at registration time.

Guards:
- ClaudeProvider.is_available() returns False when 'claude' binary is absent.
- CodexProvider.is_available() returns False when 'codex' binary is absent.
- ClaudeProvider.is_available() returns True when binary is present.
- CodexProvider.is_available() returns True when binary is present.
- _provider_stub skips ClaudeProvider registration when binary absent.
- _provider_stub skips CodexProvider registration when binary absent.
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
    def test_claude_unavailable_when_binary_absent(self, monkeypatch):
        import tinyassets.providers.claude_provider as _cp
        monkeypatch.setattr(_cp.shutil, "which", lambda name: None)
        from tinyassets.providers.claude_provider import ClaudeProvider
        assert ClaudeProvider.is_available() is False

    def test_claude_available_when_binary_present(self, monkeypatch):
        import tinyassets.providers.claude_provider as _cp
        monkeypatch.setattr(_cp.shutil, "which", lambda name: "/usr/local/bin/claude")
        from tinyassets.providers.claude_provider import ClaudeProvider
        assert ClaudeProvider.is_available() is True

    def test_codex_unavailable_when_binary_absent(self, monkeypatch):
        import tinyassets.providers.codex_provider as _cdp
        monkeypatch.setattr(_cdp.shutil, "which", lambda name: None)
        from tinyassets.providers.codex_provider import CodexProvider
        assert CodexProvider.is_available() is False

    def test_codex_available_when_binary_present(self, monkeypatch):
        import tinyassets.providers.codex_provider as _cdp
        monkeypatch.setattr(_cdp.shutil, "which", lambda name: "/usr/local/bin/codex")
        from tinyassets.providers.codex_provider import CodexProvider
        assert CodexProvider.is_available() is True

    def test_base_provider_defaults_to_true(self):
        from tinyassets.providers.base import BaseProvider

        class _Dummy(BaseProvider):
            name = "dummy"
            family = "test"

            async def complete(self, prompt, system, config, *, universe_dir=None):
                raise NotImplementedError

        assert _Dummy.is_available() is True


class TestStubSkipsWhenBinaryAbsent:
    def test_claude_not_registered_when_binary_absent(
        self, monkeypatch, reset_stub
    ):
        import tinyassets.providers.claude_provider as _cp
        monkeypatch.setattr(_cp.ClaudeProvider, "is_available", classmethod(lambda cls: False))

        stub = _reload_stub()

        assert stub._router is not None
        assert "claude-code" not in stub._router.available_providers

    def test_codex_not_registered_when_binary_absent(
        self, monkeypatch, reset_stub
    ):
        import tinyassets.providers.codex_provider as _cdp
        monkeypatch.setattr(_cdp.CodexProvider, "is_available", classmethod(lambda cls: False))

        stub = _reload_stub()

        assert stub._router is not None
        assert "codex" not in stub._router.available_providers

    def test_claude_registered_when_binary_present(
        self, monkeypatch, reset_stub
    ):
        import tinyassets.providers.claude_provider as _cp
        monkeypatch.setattr(_cp.ClaudeProvider, "is_available", classmethod(lambda cls: True))

        stub = _reload_stub()

        assert stub._router is not None
        assert "claude-code" in stub._router.available_providers
