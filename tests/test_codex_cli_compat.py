"""Codex CLI compatibility must preserve confinement and terminal evidence."""
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from scripts.check_drop_first_exec import load_modes
from tests.support.owned_spawn import install_fake_owned_spawn
from tinyassets.exceptions import ProviderError
from tinyassets.providers.base import ModelConfig
from tinyassets.providers.codex_provider import _structured_failure_excerpt


def _event(kind, **fields):
    return (json.dumps({"type": kind, **fields}) + "\n").encode()


def test_terminal_json_reason_wins_over_tracing_and_transient_errors():
    output = (
        _event("error", message="transient reconnect")
        + _event("turn.failed", error={"message": "first line\nmodel unavailable"})
        + _event("error", message="teardown noise")
    )
    assert _structured_failure_excerpt(output, "catalogue " + "x" * 5000, machine=True) == (
        "first line model unavailable"
    )


@pytest.mark.parametrize("raw", [b"", b"not json\n[]\n", _event("turn.failed", error=42)])
def test_unusable_json_falls_back_to_scrubbed_stderr(raw):
    assert _structured_failure_excerpt(raw, "model unavailable", machine=True) == (
        "model unavailable"
    )


def test_last_error_event_is_kept_without_a_turn_failed_event():
    raw = _event("error", message="first") + _event("error", message="last")
    assert _structured_failure_excerpt(raw, "noise", machine=True) == "last"
    assert _structured_failure_excerpt(raw, "plain stderr", machine=False) == "plain stderr"


def test_entire_json_message_is_scrubbed_before_clipping():
    secret = "sk-" + "Sensitive" * 25
    message = "start " + "x" * 105 + secret + "y" * 400 + " terminal cause"
    result = _structured_failure_excerpt(_event("error", message=message), "", machine=True)
    assert len(result) <= 240
    assert "Sensitive" not in result and "sk-" not in result
    assert result.startswith("start ") and result.endswith("terminal cause")


@pytest.mark.asyncio
@pytest.mark.parametrize("returncode", [1, 2])
async def test_real_provider_nonzero_paths_keep_json_reason_and_confinement(
    monkeypatch, tmp_path, returncode,
):
    from tinyassets.providers import codex_provider as provider

    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    proc = AsyncMock()
    proc.returncode = returncode
    # Spawns go through the owned-process seam; a stand-in cannot answer the
    # POSIX anchor handshake, so record the argv at the seam itself.
    launch = install_fake_owned_spawn(monkeypatch, provider.__name__, return_value=proc)
    monkeypatch.setattr(provider, "_resolve_codex_cmd", lambda: (["codex"], False))
    monkeypatch.setattr(provider, "get_sandbox_status", lambda: {
        "bwrap_available": True, "bwrap_path": "fake-bwrap",
    })
    monkeypatch.setattr(provider, "subprocess_env_for_provider", lambda *a, **kw: {
        "CODEX_HOME": str(auth_dir),
    })
    monkeypatch.setattr(provider, "_codex_sandbox_mounts", lambda command: [])
    monkeypatch.setattr(provider, "_codex_home_file_mounts", lambda path: [])
    monkeypatch.setattr(provider, "_stream_codex_exec", AsyncMock(return_value=(
        _event("turn.failed", error={"message": "model rejected sk-secretsensitive123"}),
        b"tracing catalogue " + b"x" * 5000,
    )))
    with pytest.raises(ProviderError) as failure:
        await provider.CodexProvider().complete(
            "prompt", "system", ModelConfig(sandbox_workspace=True), universe_dir=tmp_path,
        )
    assert "model rejected [redacted]" in str(failure.value)
    assert "secretsensitive" not in str(failure.value)
    # The adapter hands the shared spawn point codex's own argv plus its view of
    # the universe; the jail wraps it there (provider_jail).
    assert launch.call_args.kwargs["universe_view"] is not None
    # A served turn keeps codex's own sandbox (apply_patch needs it), so it
    # declares the nested sandbox and gets the jail's permissive profile.
    assert launch.call_args.kwargs["nested_sandbox"] is True
    inner = launch.call_args.args
    pairs = list(zip(inner, inner[1:]))
    assert ("--sandbox", "workspace-write") in pairs
    assert "--full-auto" not in inner
    assert "--dangerously-bypass-approvals-and-sandbox" not in inner
    for name in ("shell_tool", "apps", "plugins", "remote_plugin"):
        assert ("--disable", name) in pairs
    assert "--ignore-user-config" in inner and "--ignore-rules" in inner


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
    assert launch.call_args.kwargs["nested_sandbox"] is False


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
async def test_served_model_selection_is_native_unless_explicit(monkeypatch, tmp_path, override):
    from tinyassets.providers import codex_provider as provider

    if override is None:
        monkeypatch.delenv("TINYASSETS_CODEX_MODEL", raising=False)
    else:
        monkeypatch.setenv("TINYASSETS_CODEX_MODEL", override)
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    proc = AsyncMock()
    proc.returncode = 0
    # Spawns go through the owned-process seam; a stand-in cannot answer the
    # POSIX anchor handshake, so record the argv at the seam itself.
    launch = install_fake_owned_spawn(monkeypatch, provider.__name__, return_value=proc)
    monkeypatch.setattr(provider, "_resolve_codex_cmd", lambda: (["codex"], False))
    monkeypatch.setattr(provider, "get_sandbox_status", lambda: {
        "bwrap_available": True, "bwrap_path": "fake-bwrap",
    })
    monkeypatch.setattr(provider, "subprocess_env_for_provider", lambda *a, **kw: {
        "CODEX_HOME": str(auth_dir),
    })
    monkeypatch.setattr(provider, "_codex_sandbox_mounts", lambda command: [])
    monkeypatch.setattr(provider, "_codex_home_file_mounts", lambda path: [])
    monkeypatch.setattr(provider, "_stream_codex_exec", AsyncMock(return_value=(
        _event("item.completed", item={"type": "agent_message", "text": "result"})
        + _event("turn.completed", usage={"input_tokens": 3, "output_tokens": 2}),
        b"",
    )))
    result = await provider.CodexProvider().complete(
        "prompt", "system", ModelConfig(sandbox_workspace=True), universe_dir=tmp_path,
    )
    assert launch.call_args.kwargs["universe_view"] is not None
    inner = launch.call_args.args
    expected = (override or "").strip()
    if expected:
        assert inner[inner.index("-m") + 1] == expected
        assert result.model == expected
    else:
        assert "-m" not in inner and "--model" not in inner
        assert result.model == "provider-default"
    assert result.text == "result" and result.input_tokens == 3 and result.output_tokens == 2
    assert "--ignore-user-config" in inner and "--ignore-rules" in inner
    assert "--dangerously-bypass-approvals-and-sandbox" not in inner
    assert ("--sandbox", "workspace-write") in zip(inner, inner[1:])
    for name in ("shell_tool", "apps", "plugins", "remote_plugin"):
        assert ("--disable", name) in zip(inner, inner[1:])
    assert launch.call_count == 1


#: The shape `codex exec --json` emitted at codex-cli 0.153.3 (captured
#: 2026-09-28 against a real subscription; ids replaced, text shortened). No
#: event names the model that answered: thread.started carries a thread id,
#: turn.completed only usage.
_RECORDED_0_153_STREAM = (
    _event("thread.started", thread_id="00000000-0000-0000-0000-000000000000")
    + _event("turn.started")
    + _event("item.completed", item={"id": "item_0", "type": "agent_message", "text": "ok"})
    + _event("turn.completed", usage={
        "input_tokens": 17071, "cached_input_tokens": 7168,
        "cache_write_input_tokens": 0, "output_tokens": 5, "reasoning_output_tokens": 0,
    })
)


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", ["owner-picked-model", None])
async def test_recorded_stream_carries_the_request_never_a_reported_model(
    monkeypatch, tmp_path, requested,
):
    """The web app showed "Model not reported" beside an owner-picked model.

    The stream cannot say what answered, so the receipt must stay `unknown` --
    but the id this call passed to -m travels as a REQUEST the app can name.
    """
    from tinyassets.providers import codex_provider as provider
    from tinyassets.providers.execution_receipt import WriterExecutionReceipt

    monkeypatch.delenv("TINYASSETS_CODEX_MODEL", raising=False)
    auth_dir = tmp_path / "auth"
    auth_dir.mkdir()
    proc = AsyncMock()
    proc.returncode = 0
    launch = install_fake_owned_spawn(monkeypatch, provider.__name__, return_value=proc)
    monkeypatch.setattr(provider, "_resolve_codex_cmd", lambda: (["codex"], False))
    monkeypatch.setattr(provider, "get_sandbox_status", lambda: {
        "bwrap_available": True, "bwrap_path": "fake-bwrap",
    })
    monkeypatch.setattr(provider, "subprocess_env_for_provider", lambda *a, **kw: {
        "CODEX_HOME": str(auth_dir),
    })
    monkeypatch.setattr(provider, "_codex_sandbox_mounts", lambda command: [])
    monkeypatch.setattr(provider, "_codex_home_file_mounts", lambda path: [])
    monkeypatch.setattr(provider, "_stream_codex_exec", AsyncMock(
        return_value=(_RECORDED_0_153_STREAM, b""),
    ))
    result = await provider.CodexProvider().complete(
        "prompt", "system",
        ModelConfig(sandbox_workspace=True, native_model_id=requested),
        universe_dir=tmp_path,
    )
    assert result.text == "ok" and result.input_tokens == 17071
    assert result.reported_model == ""
    receipt = WriterExecutionReceipt()
    receipt.observe(result)
    inner = launch.call_args.args
    if requested:
        assert inner[inner.index("-m") + 1] == requested
        assert result.requested_model == requested
        assert receipt.projection() == {
            "provider": "codex", "model": "", "model_status": "unknown",
            "requested_model": requested,
        }
    else:
        # The source's own default is a position, not a model: nothing to name.
        assert "-m" not in inner
        assert result.requested_model == ""
        assert receipt.projection() == {
            "provider": "codex", "model": "", "model_status": "unknown",
        }
