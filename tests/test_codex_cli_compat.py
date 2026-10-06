"""Codex CLI compatibility must preserve confinement and terminal evidence."""
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts.check_drop_first_exec import load_modes
from tests.support.owned_spawn import install_fake_owned_spawn
from tests.test_codex_app_server import served  # noqa: F401 - the shared fixture
from tinyassets.providers.base import ModelConfig


@pytest.mark.asyncio
@pytest.mark.parametrize("confined", [True, False])
async def test_a_non_served_codex_node_drops_its_own_sandbox_only_inside_our_jail(
    monkeypatch, tmp_path, confined,
):
    """A workflow-node (non-served) codex call runs codex's shell. Inside our
    provider jail it uses --dangerously-bypass-approvals-and-sandbox so codex
    does not nest its own bubblewrap (which would need user namespaces the jail
    now denies); off the jail it keeps --sandbox workspace-write."""
    from tinyassets.providers import codex_provider as provider
    from tinyassets.providers.provider_jail import provider_launch_scope

    proc = AsyncMock()
    proc.returncode = 0
    # A non-served node reads plain stdout through proc.communicate (no event
    # stream), so give the fake process a simple reply.
    proc.communicate = AsyncMock(return_value=(b"ok", b""))
    launch = install_fake_owned_spawn(monkeypatch, provider.__name__, return_value=proc)
    monkeypatch.setattr(provider, "_resolve_codex_cmd", lambda: (["codex"], False))
    monkeypatch.setattr(provider, "get_sandbox_status", lambda: {
        "bwrap_available": True, "bwrap_path": "fake-bwrap",
    })
    monkeypatch.setattr(provider, "subprocess_env_for_provider", lambda *a, **kw: {})
    monkeypatch.setattr(provider, "_codex_sandbox_mounts", lambda command: [])

    async def drive():
        return await provider.CodexProvider().complete(
            "prompt", "system", ModelConfig(sandbox_workspace=False), universe_dir=tmp_path,
        )

    if confined:
        with provider_launch_scope(tmp_path):
            await drive()
    else:
        await drive()

    inner = launch.call_args.args
    pairs = list(zip(inner, inner[1:]))
    if confined:
        assert "--dangerously-bypass-approvals-and-sandbox" in inner
        assert ("--sandbox", "workspace-write") not in pairs
    else:
        assert ("--sandbox", "workspace-write") in pairs
        assert "--dangerously-bypass-approvals-and-sandbox" not in inner
    # A non-served node never disables the shell tool -- it is a coding turn.
    assert ("--disable", "shell_tool") not in pairs
    # It never declares a nested sandbox, so a confined one gets the jail's full
    # deny profile (no new user namespaces, no symlinks).
    assert not launch.call_args.kwargs.get("nested_sandbox")


def test_image_pin_is_catalogue_compatible_and_no_host_keepalive_remains():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text()
    version = re.search(r"ARG CODEX_CLI_VERSION=(\d+)\.(\d+)\.(\d+)", dockerfile)
    assert version and tuple(map(int, version.groups())) >= (0, 146, 0)
    assert "python /tmp/codex_cli_smoke.py" in dockerfile
    # The weekly host-login keepalive and its `codex-keepalive` helper mode were
    # retired 2026-09-24: the platform has no LLM (AGENTS.md Hard Rule 15), so
    # no codex launch outside a universe's own provider child remains.
    assert not (root / ".github/workflows/codex-auth-keepalive.yml").exists()
    assert "codex-keepalive" not in load_modes()


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [None, "", " \t", "future-model-2030", " future-model-2030 "])
async def test_served_model_selection_is_native_unless_explicit(
    served, monkeypatch, override,  # noqa: F811
):
    if override is None:
        monkeypatch.delenv("TINYASSETS_CODEX_MODEL", raising=False)
    else:
        monkeypatch.setenv("TINYASSETS_CODEX_MODEL", override)
    run, launch, *_ = served
    result, server = await run(cfg=ModelConfig(sandbox_workspace=True))
    params = server.requests("thread/start")[0]["params"]
    expected = (override or "").strip()
    if expected:
        assert params["model"] == expected
        assert result.model == expected
    else:
        assert "model" not in params
        assert result.model == "provider-default"
    # The model is a thread parameter, never a launch flag.
    assert "-m" not in launch.call_args.args and "--model" not in launch.call_args.args
    assert result.text == "done" and result.input_tokens == 5 and result.output_tokens == 3
    assert launch.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", ["owner-picked-model", None])
async def test_a_served_turn_carries_the_request_never_a_reported_model(
    served, requested,  # noqa: F811
):
    """The web app showed "Model not reported" beside an owner-picked model.

    Nothing in the protocol says what answered, so the receipt stays `unknown`
    -- but the requested id travels as a REQUEST the app can name.
    """
    from tinyassets.providers.execution_receipt import WriterExecutionReceipt

    run, _launch, _state, config, _ = served
    result, _ = await run(cfg=config(native_model_id=requested))
    assert result.reported_model == ""
    receipt = WriterExecutionReceipt()
    receipt.observe(result)
    if requested:
        assert result.requested_model == requested
        assert receipt.projection() == {
            "provider": "codex", "model": "", "model_status": "unknown",
            "requested_model": requested, "configured_model": requested,
        }
    else:
        # The source's own default is named as configuration, not requested.
        assert result.requested_model == ""
        assert receipt.projection() == {
            "provider": "codex", "model": "", "model_status": "unknown",
            "configured_model": "cli-selected",
        }
