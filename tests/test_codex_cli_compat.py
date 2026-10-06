"""Codex CLI compatibility must preserve confinement and terminal evidence."""
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts.check_drop_first_exec import load_modes
from tests.support.owned_spawn import install_fake_owned_spawn
from tests.test_codex_app_server import served  # noqa: F401 - the shared fixture
from tinyassets.exceptions import ProviderError
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




# --- the turn's own reason, redacted (served turns: codex app-server) ----------


def _failed(error, *, notices=(), stderr=b""):
    from tests.support.fake_codex_app_server import ScriptedAppServer, finished

    steps = [(0, {"method": "error", "params": {"error": {"message": text}, "willRetry": False}})
             for text in notices]
    end = finished(reply="", status="failed")
    end[-1][1]["params"]["turn"]["error"] = error
    return ScriptedAppServer([*steps, *end], stderr=stderr)


@pytest.mark.asyncio
async def test_terminal_json_reason_wins_over_tracing_and_transient_errors(served):  # noqa: F811
    run, *_ = served
    server = _failed({"message": "first line\nmodel unavailable"},
                     notices=["transient reconnect"], stderr=b"catalogue " + b"x" * 5000)
    with pytest.raises(ProviderError) as failure:
        await run(server=server)
    assert str(failure.value).endswith("first line model unavailable")
    assert "transient" not in str(failure.value) and "catalogue" not in str(failure.value)


@pytest.mark.parametrize("error", [None, 42, {}, {"message": ""}])
@pytest.mark.asyncio
async def test_unusable_json_falls_back_to_scrubbed_stderr(served, error):  # noqa: F811
    run, *_ = served
    with pytest.raises(ProviderError) as failure:
        await run(server=_failed(error, stderr=b"model unavailable sk-secretsensitive123"))
    assert str(failure.value).endswith("model unavailable [redacted]")


@pytest.mark.asyncio
async def test_last_error_event_is_kept_without_a_turn_failed_event(served):  # noqa: F811
    """A failed turn that names no reason keeps the last ``error`` Codex sent;
    with none at all the scrubbed stderr is the reason."""
    run, *_ = served
    with pytest.raises(ProviderError) as kept:
        await run(server=_failed(None, notices=["first", "last"], stderr=b"noise"))
    assert str(kept.value).endswith(": last")
    with pytest.raises(ProviderError) as plain:
        await run(server=_failed(None, stderr=b"plain stderr"))
    assert str(plain.value).endswith(": plain stderr")


@pytest.mark.asyncio
async def test_entire_json_message_is_scrubbed_before_clipping(served):  # noqa: F811
    run, *_ = served
    secret = "sk-" + "Sensitive" * 25
    message = "start " + "x" * 105 + secret + "y" * 400 + " terminal cause"
    with pytest.raises(ProviderError) as failure:
        await run(server=_failed({"message": message}))
    result = str(failure.value).split(": ", 1)[1]
    assert len(result) <= 240
    assert "Sensitive" not in result and "sk-" not in result
    assert result.startswith("start ") and result.endswith("terminal cause")


@pytest.mark.asyncio
@pytest.mark.parametrize("returncode", [1, 2])
async def test_real_provider_nonzero_paths_keep_json_reason_and_confinement(
    served, returncode,  # noqa: F811
):
    from tinyassets.providers.codex_app_server import SERVED_LAUNCH_ARGS

    run, launch, *_ = served
    server = _failed({"message": "model rejected sk-secretsensitive123"},
                     stderr=b"tracing catalogue " + b"x" * 5000)
    server.exit_code = returncode
    with pytest.raises(ProviderError) as failure:
        await run(server=server)
    assert "model rejected [redacted]" in str(failure.value)
    assert "secretsensitive" not in str(failure.value)
    # The adapter hands the shared spawn point codex's own argv plus its view of
    # the universe; the jail wraps it there (provider_jail).
    assert launch.call_args.kwargs["universe_view"] is not None
    # Codex runs nothing itself, so it declares no nested sandbox and gets the
    # jail's full deny profile.
    assert not launch.call_args.kwargs.get("nested_sandbox")
    inner = launch.call_args.args
    pairs = list(zip(inner, inner[1:]))
    assert list(inner[1:1 + len(SERVED_LAUNCH_ARGS)]) == list(SERVED_LAUNCH_ARGS)
    assert "--full-auto" not in inner and "exec" not in inner
    assert "--dangerously-bypass-approvals-and-sandbox" not in inner
    for name in ("shell_tool", "apps", "plugins", "remote_plugin"):
        assert ("--disable", name) in pairs
    assert ("-c", 'projects."/workspace".trust_level="untrusted"') in pairs
    assert not any("mcp_servers" in arg for arg in inner)


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [None, "", " \t", "future-model-2030", " future-model-2030 "])
async def test_served_model_selection_is_native_unless_explicit(
    served, monkeypatch, override,  # noqa: F811
):
    from tinyassets.providers.codex_app_server import SERVED_LAUNCH_ARGS

    if override is None:
        monkeypatch.delenv("TINYASSETS_CODEX_MODEL", raising=False)
    else:
        monkeypatch.setenv("TINYASSETS_CODEX_MODEL", override)
    run, launch, *_ = served
    result, server = await run(cfg=ModelConfig(sandbox_workspace=True))
    assert launch.call_args.kwargs["universe_view"] is not None
    params = server.requests("thread/start")[0]["params"]
    expected = (override or "").strip()
    if expected:
        assert params["model"] == expected
        assert result.model == expected
    else:
        assert "model" not in params
        assert result.model == "provider-default"
    # The model is a thread parameter, never a launch flag.
    inner = launch.call_args.args
    assert "-m" not in inner and "--model" not in inner
    assert result.text == "done" and result.input_tokens == 5 and result.output_tokens == 3
    assert list(inner[1:1 + len(SERVED_LAUNCH_ARGS)]) == list(SERVED_LAUNCH_ARGS)
    assert "--dangerously-bypass-approvals-and-sandbox" not in inner
    assert ("--sandbox", "workspace-write") not in zip(inner, inner[1:])
    for name in ("shell_tool", "apps", "plugins", "remote_plugin"):
        assert ("--disable", name) in zip(inner, inner[1:])
    assert launch.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", ["owner-picked-model", None])
async def test_recorded_stream_carries_the_request_never_a_reported_model(
    served, requested,  # noqa: F811
):
    """The web app showed "Model not reported" beside an owner-picked model.

    Replays a real codex-cli 0.160.0 turn: nothing in it says what answered,
    so the receipt stays `unknown` -- but the requested id travels as a
    REQUEST the app can name.
    """
    from tests.support.codex_app_server_recording import recorded_turn
    from tests.support.fake_codex_app_server import ScriptedAppServer
    from tinyassets.providers.execution_receipt import WriterExecutionReceipt

    run, _launch, _state, config, _ = served
    script, _call = recorded_turn()
    result, server = await run(server=ScriptedAppServer(script),
                               cfg=config(native_model_id=requested))
    assert result.text == "notes.md says hello." and result.input_tokens == 240
    assert result.reported_model == ""
    receipt = WriterExecutionReceipt()
    receipt.observe(result)
    params = server.requests("thread/start")[0]["params"]
    if requested:
        assert params["model"] == requested
        assert result.requested_model == requested
        assert receipt.projection() == {
            "provider": "codex", "model": "", "model_status": "unknown",
            "requested_model": requested, "configured_model": requested,
        }
    else:
        # The source's own default is named as configuration, not requested.
        assert "model" not in params
        assert result.requested_model == ""
        assert receipt.projection() == {
            "provider": "codex", "model": "", "model_status": "unknown",
            "configured_model": "cli-selected",
        }
