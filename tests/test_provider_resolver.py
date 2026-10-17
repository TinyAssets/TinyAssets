"""Owner definitions resolve to the generic broker-backed HTTP executor."""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.definition import ProviderDefinition
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








def test_unknown_access_method_rejected() -> None:
    # Bypass registration validation by constructing the dataclass directly.
    bad = ProviderDefinition(
        id="provdef_x", universe_id="u", owner_user_id="o",
        access_method="sdk_direct", protocol="openai_chat", model="m",
        ref="x", visibility="private", created_at="2026-01-01T00:00:00+00:00",
    )
    with pytest.raises(ValueError):
        provider_for_definition(bad)




def test_register_universe_open_providers_empty_when_none(base: Path) -> None:
    from tinyassets.providers.router import ProviderRouter

    (base / "u-empty").mkdir()
    router = ProviderRouter()
    assert register_universe_open_providers(router, "u-empty") == []
