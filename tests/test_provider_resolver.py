"""provider_for_definition — resolve a ProviderDefinition to its executor.

Covers: api_key_http -> ApiKeyHttpProvider; subscription_cli -> the vendor CLI
adapter (codex/claude-code) with its stable identity preserved; unknown ref /
access_method rejected; no cross-method fallback.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.definition import ProviderDefinition, register_definition
from tinyassets.providers.provider_resolver import (
    provider_for_definition,
    register_universe_open_providers,
)


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


def _def(access_method: str, protocol: str, ref: str) -> ProviderDefinition:
    return ProviderDefinition(
        id="provdef_" + "d" * 32,
        universe_id="u",
        owner_user_id="o",
        access_method=access_method,
        protocol=protocol,
        model="m",
        ref=ref,
        visibility="private",
        created_at="2026-01-01T00:00:00+00:00",
    )


def test_api_key_http_resolves_to_api_provider() -> None:
    prov = provider_for_definition(_def("api_key_http", "openai_chat", "http_grant_x"))
    assert isinstance(prov, ApiKeyHttpProvider)
    assert prov.family == "api:openai_chat"


def test_subscription_cli_codex_preserves_identity() -> None:
    prov = provider_for_definition(_def("subscription_cli", "cli:codex", "codex"))
    assert prov.name == "codex"
    assert prov.family == "openai"


def test_subscription_cli_claude_preserves_identity() -> None:
    prov = provider_for_definition(
        _def("subscription_cli", "cli:claude-code", "claude-code")
    )
    assert prov.name == "claude-code"
    assert prov.family == "anthropic"


def test_unknown_cli_ref_rejected() -> None:
    with pytest.raises(ValueError):
        provider_for_definition(_def("subscription_cli", "cli:codex", "kimi-cli"))


def test_unknown_access_method_rejected() -> None:
    # Bypass registration validation by constructing the dataclass directly.
    bad = ProviderDefinition(
        id="provdef_x", universe_id="u", owner_user_id="o",
        access_method="sdk_direct", protocol="openai_chat", model="m",
        ref="x", visibility="private", created_at="2026-01-01T00:00:00+00:00",
    )
    with pytest.raises(ValueError):
        provider_for_definition(bad)


def test_register_universe_open_providers_makes_them_routable(base: Path) -> None:
    from tests.test_compute_connection import _seed_grant
    from tinyassets.providers.router import ProviderRouter

    _seed_grant(base, owner="founder", universe="u-x")
    register_definition(
        universe_id="u-x", owner_user_id="founder", access_method="api_key_http",
        protocol="openai_chat", model="moonshotai/kimi-k2", ref="http_grant_" + "a" * 32,
    )
    register_definition(
        universe_id="u-x", owner_user_id="founder", access_method="subscription_cli",
        protocol="cli:codex", model="gpt-5-codex", ref="codex",
    )

    router = ProviderRouter()
    names = register_universe_open_providers(router, "u-x")

    assert len(names) == 2
    assert set(names) <= set(router.available_providers)
    assert any(n.startswith("api_key_http:") for n in names)
    assert "codex" in names
    # The EXISTING chain logic now routes to them — all registered, none excluded.
    effective, excluded = router.effective_chain(names)
    assert effective == names
    assert excluded == []


def test_register_universe_open_providers_empty_when_none(base: Path) -> None:
    from tinyassets.providers.router import ProviderRouter

    (base / "u-empty").mkdir()
    router = ProviderRouter()
    assert register_universe_open_providers(router, "u-empty") == []
