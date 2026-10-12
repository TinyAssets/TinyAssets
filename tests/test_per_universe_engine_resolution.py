"""Universe contexts carry scope, but never mint provider authority.

These tests pin ambient globals while interleaving explicit universe contexts.
Neither per-universe configuration nor process-global configuration may cause a
provider launch without a fresh server-issued serving carrier.
"""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest

from tinyassets import runtime_singletons as runtime
from tinyassets.config import load_universe_config
from tinyassets.credential_vault import write_credential_vault
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    UniverseContext,
    subprocess_env_for_provider,
)
from tinyassets.providers.model_call import text_sync
from tinyassets.providers.router import ProviderRouter


class _RecordingProvider(BaseProvider):
    """Fake provider that resolves auth via the REAL vault-backed env helper.

    ``complete`` calls the real ``subprocess_env_for_provider(self.name,
    universe_dir=universe_dir)`` and packs what it observed into the response so
    each call can be correlated with the universe it was routed for:

    - ``text``   = the ``universe_dir`` it saw (or ``None``)
    - ``model``  = the resolved auth env var (``CODEX_HOME`` /
      ``CLAUDE_CONFIG_DIR``) the vault produced for that universe_dir
    """

    def __init__(self, name: str, family: str, auth_env_key: str) -> None:
        self.name = name
        self.family = family
        self._auth_env_key = auth_env_key

    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir: Path | None = None,
    ) -> ProviderResponse:
        env = subprocess_env_for_provider(self.name, universe_dir=universe_dir)
        return ProviderResponse(
            text=str(universe_dir),
            provider=self.name,
            model=env.get(self._auth_env_key, ""),
            family=self.family,
            latency_ms=1.0,
        )


def _write_codex_universe(root: Path) -> tuple[Path, Path]:
    """Universe A: preferred_writer=codex + a codex vault record."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text("preferred_writer: codex\n", encoding="utf-8")
    codex_home = root / "codex_home"
    write_credential_vault(
        root,
        [
            {
                "credential_type": "llm_subscription",
                "service": "codex",
                "codex_home": str(codex_home),
            }
        ],
    )
    return root, codex_home


def _write_claude_universe(root: Path) -> tuple[Path, Path]:
    """Universe B: preferred_writer=claude-code + a claude vault record."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "preferred_writer: claude-code\n", encoding="utf-8"
    )
    claude_cfg = root / "claude_cfg"
    write_credential_vault(
        root,
        [
            {
                "credential_type": "llm_subscription",
                "service": "claude",
                "claude_config_dir": str(claude_cfg),
                "oauth_token": "tok-universe-b",
            }
        ],
    )
    return root, claude_cfg


@pytest.fixture
def _pinned_to_universe_a(tmp_path, monkeypatch):
    """Build two universes and pin ALL process globals to universe A."""
    universe_a, codex_home = _write_codex_universe(tmp_path / "universe_a")
    universe_b, claude_cfg = _write_claude_universe(tmp_path / "universe_b")

    # Neither a hard writer pin nor api-key opt-in should interfere.
    monkeypatch.delenv("TINYASSETS_PIN_WRITER", raising=False)
    monkeypatch.delenv("TINYASSETS_ALLOW_API_KEY_PROVIDERS", raising=False)

    # Pin the process globals to universe A — this is the whole point: the
    # per-call universe_context must override these, not read from them.
    monkeypatch.setenv("TINYASSETS_UNIVERSE", str(universe_a))
    saved_config = runtime.universe_config
    runtime.universe_config = load_universe_config(universe_a)
    try:
        yield {
            "a": universe_a,
            "b": universe_b,
            "codex_home": codex_home,
            "claude_cfg": claude_cfg,
        }
    finally:
        runtime.universe_config = saved_config


def test_call_sync_rejects_interleaved_config_only_universe_contexts(
    _pinned_to_universe_a,
):
    from tinyassets.exceptions import ProviderAuthorityHeldError

    fixt = _pinned_to_universe_a
    universe_a = fixt["a"]
    universe_b = fixt["b"]

    router = ProviderRouter()
    router.register(_RecordingProvider("codex", "openai", "CODEX_HOME"))
    router.register(_RecordingProvider("claude-code", "anthropic", "CLAUDE_CONFIG_DIR"))

    ctx_a = UniverseContext(
        universe_dir=universe_a, config=load_universe_config(universe_a)
    )
    ctx_b = UniverseContext(
        universe_dir=universe_b, config=load_universe_config(universe_b)
    )

    # 24 interleaved calls: even index -> A, odd index -> B.
    plan = [("a", ctx_a) if i % 2 == 0 else ("b", ctx_b) for i in range(24)]

    def _worker(item):
        label, ctx = item
        with pytest.raises(ProviderAuthorityHeldError, match="Connect your provider"):
            router.call_sync(
                role="writer",
                prompt=f"prompt-{label}",
                system="system",
                universe_context=ctx,
            )
        return label

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_worker, plan))

    assert results.count("a") == 12
    assert results.count("b") == 12


def test_text_sync_does_not_treat_forwarded_universe_config_as_authority(
    _pinned_to_universe_a,
    monkeypatch,
):
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers import call as call_module

    fixt = _pinned_to_universe_a
    ctx_b = UniverseContext(
        universe_dir=fixt["b"], config=load_universe_config(fixt["b"])
    )

    router = ProviderRouter()
    router.register(_RecordingProvider("codex", "openai", "CODEX_HOME"))
    router.register(_RecordingProvider("claude-code", "anthropic", "CLAUDE_CONFIG_DIR"))

    saved = call_module.get_provider_router()
    call_module.set_provider_router(router)
    try:
        with pytest.raises(ProviderAuthorityHeldError, match="Connect your provider"):
            text_sync(
                "prompt-b",
                "system",
                role="writer",
                universe_context=ctx_b,
            )
    finally:
        call_module.set_provider_router(saved)
