"""Open-provider routing building blocks (compute-agnostic task 3.1c).

Covers:
- the set_engine `open_provider` mode writes preferred_writer = the definition's
  resolved executor name (no credential), and validates the definition exists.

The preference reordering over a platform fallback chain (``_apply_open_preference``)
was deleted under Hard Rule 15: the router serves only the provider an owner's
authority names, so there is no chain to reorder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinyassets.providers.base import BaseProvider, ModelConfig, ProviderResponse


class _Fake(BaseProvider):
    def __init__(self, name: str) -> None:
        self.name = name
        self.family = "api:openai_chat"

    async def complete(self, prompt: str, system: str, config: ModelConfig,
                       *, universe_dir: Path | None = None) -> ProviderResponse:
        return ProviderResponse(text="x", provider=self.name, model="m",
                                family=self.family, latency_ms=0.0)


# --------------------------------------------------------------------------- #
# set_engine open_provider mode.
# --------------------------------------------------------------------------- #


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    (root / "u-x").mkdir(parents=True)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


def test_set_engine_open_provider_points_writer_at_definition(base: Path) -> None:
    from tinyassets.api.universe import _set_engine_open_provider
    from tinyassets.providers.definition import register_definition

    d = register_definition(
        universe_id="u-x", owner_user_id="founder", access_method="subscription_cli",
        protocol="cli:codex", model="gpt-5-codex", ref="codex",
    )
    result = json.loads(
        _set_engine_open_provider("u-x", base / "u-x", {"definition_id": d.id}, "")
    )
    assert result["status"] == "engine_set"
    assert result["engine_source"] == "open_provider"
    assert result["preferred_writer"] == "codex"  # resolved executor name


def test_set_engine_open_provider_api_key_name(base: Path) -> None:
    from tinyassets.api.universe import _set_engine_open_provider
    from tinyassets.providers.definition import register_definition

    d = register_definition(
        universe_id="u-x", owner_user_id="founder", access_method="api_key_http",
        protocol="openai_chat", model="moonshotai/kimi-k2", ref="http_grant_" + "a" * 32,
    )
    result = json.loads(
        _set_engine_open_provider("u-x", base / "u-x", {"definition_id": d.id}, "")
    )
    assert result["preferred_writer"] == f"api_key_http:{d.id}"
    assert "secret" not in json.dumps(result).lower()


def test_set_engine_open_provider_missing_definition(base: Path) -> None:
    from tinyassets.api.universe import _set_engine_open_provider

    r1 = json.loads(_set_engine_open_provider("u-x", base / "u-x", {}, ""))
    assert "error" in r1  # no definition_id
    r2 = json.loads(
        _set_engine_open_provider("u-x", base / "u-x",
                                  {"definition_id": "provdef_nope"}, "")
    )
    assert "error" in r2  # unknown definition


def test_register_hook_registers_universe_open_providers(base: Path) -> None:
    from tinyassets.providers import call as call_mod
    from tinyassets.providers.definition import register_definition
    from tinyassets.providers.router import ProviderRouter

    register_definition(
        universe_id="u-x", owner_user_id="founder", access_method="subscription_cli",
        protocol="cli:codex", model="gpt-5", ref="codex",
    )
    router = ProviderRouter()
    call_mod.set_provider_router(router)
    try:
        ctx = type("Ctx", (), {"universe_dir": base / "u-x"})()
        call_mod.register_open_providers_for(ctx)
        assert "codex" in router.available_providers
    finally:
        call_mod.set_provider_router(None)
