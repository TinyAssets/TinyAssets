"""Metadata discovery is bounded, opaque, no inference and no credential relay."""

import asyncio
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tinyassets.exceptions import ProviderError
from tinyassets.providers.codex_provider import CodexProvider
from tinyassets.providers.native_catalogue import NativeCatalogue, NativeModel
from tinyassets.providers.native_jsonrpc_discovery import (
    NativeJsonRpcProtocol,
    parse_model_page,
    read_native_catalogue,
)

PROTOCOL = CodexProvider.native_discovery_protocol
pytestmark = pytest.mark.usefixtures("metadata_transport_processes")


@pytest.fixture
def metadata_transport_processes(monkeypatch):
    """Exercise real protocol/family children; OS isolation has separate proofs."""
    from tinyassets.providers import owned_process, provider_jail

    def transport_launch(argv, *, view, **kwargs):
        scope = provider_jail._SCOPE.get()
        assert scope.universe_dir == view.universe_dir
        assert scope.engine_route is None
        return provider_jail.ConfinedLaunch(list(argv), universe_dir=view.universe_dir)

    monkeypatch.setattr(provider_jail, "confine_launch", transport_launch)
    monkeypatch.setattr(
        owned_process, "_open_disk_budget", lambda _: SimpleNamespace(settle=lambda: None),
    )
    monkeypatch.setattr(owned_process, "_watch_disk", lambda proc, budget: budget.settle())


def metadata_snapshot(universe):
    snapshot = universe / ".runtime" / "provider-launch-credentials" / "metadata-test"
    snapshot.mkdir(parents=True, exist_ok=True)
    return str(snapshot)


def row(model="a-future-release", **kwargs):
    return {"model": model, "inputModalities": ["text"], **kwargs}


def test_native_wire_ids_are_not_display_labels_and_unknown_fields_are_tolerated():
    models, defaults, cursor = parse_model_page({
        "data": [row(id="display-id", displayName="Some name", isDefault=True,
                     futureProviderFeature={"new": True})], "nextCursor": "opaque/page2",
    }, PROTOCOL)
    assert models == [NativeModel("a-future-release", frozenset({"text"}))]
    assert defaults == ["a-future-release"]
    assert cursor == "opaque/page2"


def test_missing_modality_is_unknown_not_invented():
    assert parse_model_page(
        {"data": [{"model": "new"}]}, PROTOCOL,
    )[0][0].input_modalities == frozenset()


@pytest.mark.parametrize("result", [
    None, {"data": {}}, {"data": [{"id": "not-the-executable-id"}]},
    {"data": [row(isDefault=1)]}, {"data": [row(hidden="true")]},
    {"data": [row(model="bad\nmodel")]}, {"data": [row(inputModalities="text")]},
    {"data": [row(inputModalities=[1])]}, {"data": [], "nextCursor": False},
])
def test_malformed_page_refuses(result):
    with pytest.raises(ValueError):
        parse_model_page(result, PROTOCOL)


@pytest.mark.parametrize("models,default,observed", [
    ((NativeModel("x", frozenset()),) * 2, None, datetime.now(timezone.utc)),
    ((), "absent", datetime.now(timezone.utc)), ((), None, datetime.now()),
])
def test_invalid_normalized_catalogue_refuses(models, default, observed):
    with pytest.raises(ValueError):
        NativeCatalogue(models, default, observed)


def peer_script(pages, *, error=None):
    # A real child process implements metadata only and rejects any inference
    # command. No files, installed provider, real credentials or network.
    return f'''
import json, sys
pages = {pages!r}
error = {error!r}
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    if method == 'initialized':
        continue
    if method == 'initialize':
        result = {{'ready': True}}
    elif method == 'model/list':
        assert request['params']['includeHidden'] is True
        assert request['id'] == 1 or request['params']['cursor'] == 'next'
        if error is not None:
            print(json.dumps({{'id': request['id'], 'error': error}}), flush=True)
            continue
        result = pages[min(request['id'] - 1, len(pages) - 1)]
    else:
        raise RuntimeError('discovery attempted inference or another operation')
    print(json.dumps({{'method': 'metadata/notification', 'params': {{}}}}), flush=True)
    print(json.dumps({{'id': request['id'], 'result': result}}), flush=True)
'''


def run_peer(tmp_path, pages, **kwargs):
    script = peer_script(pages, **kwargs)
    return asyncio.run(read_native_catalogue(
        [sys.executable, "-u", "-c", script], env=os.environ.copy(),
        cwd=metadata_snapshot(tmp_path), universe_dir=tmp_path, timeout=3,
        protocol=PROTOCOL,
    ))


def test_real_metadata_process_follows_every_page_and_preserves_hidden(tmp_path):
    result = run_peer(tmp_path, [
        {"data": [row(isDefault=True)], "nextCursor": "next"},
        {"data": [row("brand-new-id", hidden=True)], "nextCursor": None},
    ])
    assert [model.model_id for model in result.models] == ["a-future-release", "brand-new-id"]
    assert result.models[1].hidden
    assert result.default_model_id == "a-future-release"
    assert result.observed_at.tzinfo is not None


@pytest.mark.parametrize("pages", [
    [{"data": [row()], "nextCursor": "next"}],  # repeated cursor
    [{"data": [row(), row()]}],  # duplicate ID
    [{"data": [row(isDefault=True), row("second", isDefault=True)]}],
])
def test_no_partial_success_on_incomplete_or_conflicting_catalogue(tmp_path, pages):
    with pytest.raises(ProviderError, match="^native model discovery unavailable$"):
        run_peer(tmp_path, pages)


def test_upstream_errors_are_not_relayed(tmp_path):
    with pytest.raises(ProviderError) as error:
        run_peer(tmp_path, [], error={"message": "secret=never-relay-account-data"})
    assert "secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


@pytest.mark.parametrize("mode", ["timeout", "cancel", "oversized", "eof"])
def test_child_is_reaped_on_every_failure(tmp_path, mode):
    async def exercise():
        children = []
        real_spawn = asyncio.create_subprocess_exec

        async def spawn(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            children.append(process)
            return process

        code = {"timeout": "import time; time.sleep(60)",
                "cancel": "import time; time.sleep(60)",
                "oversized": "print('x' * (4 * 1024 * 1024 + 10), flush=True)",
                "eof": "pass"}[mode]
        with patch("asyncio.create_subprocess_exec", spawn):
            task = asyncio.create_task(read_native_catalogue(
                [sys.executable, "-u", "-c", code], env=os.environ.copy(),
                cwd=metadata_snapshot(tmp_path), universe_dir=tmp_path,
                protocol=PROTOCOL,
                timeout=0.2 if mode == "timeout" else 3,
            ))
            if mode == "cancel":
                # Cancel the admitted metadata process, after its shared
                # family handshake. Pre-handshake cancellation is owned by
                # the launcher's separate teardown tests.
                from tinyassets.providers import owned_process
                while not children or owned_process._get_family(children[-1]) is None:
                    await asyncio.sleep(0.01)
                task.cancel()
            expected = asyncio.CancelledError if mode == "cancel" else ProviderError
            with pytest.raises(expected):
                await task
        assert children and all(child.returncode is not None for child in children)
    asyncio.run(exercise())


def test_provider_discovery_requires_snapshot_and_never_falls_back_to_host(tmp_path):
    from tinyassets.providers.codex_provider import CodexProvider

    with pytest.raises(ProviderError, match="owned credentials"):
        asyncio.run(CodexProvider().enumerate_models(
            universe_dir=tmp_path, credential_snapshot_dir=None,
        ))


def test_native_registration_uses_owned_environment_and_direct_metadata_only(tmp_path):
    from tinyassets.providers.codex_provider import CodexProvider

    snapshot = tmp_path / "snapshot"
    expected = NativeCatalogue((), None, datetime.now(timezone.utc))
    with (
        patch("tinyassets.providers.codex_provider._resolve_codex_cmd",
              return_value=(["executor"], False)),
        patch("tinyassets.providers.base.subprocess_env_for_provider",
              return_value={"OWNED": "yes"}) as environment,
        patch("tinyassets.providers.native_jsonrpc_discovery.read_native_catalogue",
              new_callable=AsyncMock, return_value=expected) as reader,
    ):
        assert asyncio.run(CodexProvider().enumerate_models(
            universe_dir=tmp_path, credential_snapshot_dir=snapshot,
        )) == expected
    environment.assert_called_once_with("codex", universe_dir=tmp_path,
                                        credential_snapshot_dir=snapshot)
    assert reader.call_args.args == (["executor", "app-server"],)
    assert reader.call_args.kwargs["env"] == {"OWNED": "yes"}
    assert reader.call_args.kwargs["cwd"] == str(snapshot)
    assert reader.call_args.kwargs["protocol"] is PROTOCOL


def test_new_executor_can_describe_other_metadata_methods_and_fields(tmp_path):
    protocol = NativeJsonRpcProtocol(
        list_method="catalogue/get", items_key="items", model_key="identifier",
        default_key="preferred", modalities_key="inputs", hidden_key="internal",
    )
    # No handshake, different method and field names, same bounded transport.
    script = '''
import json, sys
request = json.loads(sys.stdin.readline())
assert request['method'] == 'catalogue/get'
print(json.dumps({'id': request['id'], 'result': {'items': [
    {'identifier': 'future-company/model', 'preferred': True, 'inputs': ['text']}
]}}), flush=True)
'''
    result = asyncio.run(read_native_catalogue(
        [sys.executable, "-u", "-c", script], env=os.environ.copy(),
        cwd=metadata_snapshot(tmp_path), universe_dir=tmp_path,
        protocol=protocol, timeout=3,
    ))
    assert result.models[0].model_id == "future-company/model"
    assert result.default_model_id == "future-company/model"


def test_unproven_native_adapter_reports_unknown():
    from tinyassets.providers.claude_provider import ClaudeProvider

    assert asyncio.run(ClaudeProvider().enumerate_models(
        universe_dir=None, credential_snapshot_dir=None,
    )) is None


@pytest.mark.parametrize("outcome", [
    "success", "unknown", "error", "cancel", "rotated", "foreign", "stale",
])
def test_owned_boundary_pins_custody_and_always_cleans_snapshot(tmp_path, outcome):
    from tinyassets.credential_vault import LLMCredentialCustodyReference
    from tinyassets.providers import native_discovery as module

    universe = tmp_path / "universe"
    universe.mkdir()
    custody = LLMCredentialCustodyReference("ref", "owner", "universe", "native-service",
                                            1, "digest", "record-digest")
    snapshot = SimpleNamespace(directory=universe / "sealed")
    seen = []

    async def enumerate_models(**kwargs):
        seen.append(kwargs)
        if outcome == "error":
            raise ProviderError("private provider prose")
        if outcome == "cancel":
            raise asyncio.CancelledError
        if outcome == "unknown":
            return None
        from datetime import timedelta
        observed = datetime.now(timezone.utc)
        if outcome == "stale":
            observed -= timedelta(minutes=10)
        return NativeCatalogue((NativeModel("future-model", frozenset({"text"})),),
                               "future-model", observed)

    provider = SimpleNamespace(
        name="new-native-executor", native_credential_service="native-service",
        enumerate_models=enumerate_models,
    )
    current = [custody, replace(custody, generation=2) if outcome == "rotated" else custody]
    expected = replace(custody, owner_user_id="somebody-else") if outcome == "foreign" else custody
    with (
        patch.object(module, "_custody", side_effect=current),
        patch.object(module, "snapshot_llm_subscription_credential", return_value=snapshot) as copy,
        patch.object(module, "cleanup_llm_credential_snapshot") as cleanup,
    ):
        call = module.refresh_native_catalogue(provider, universe_dir=universe,
                                               owner_user_id="owner", expected_custody=expected)
        if outcome in {"success", "unknown"}:
            result = asyncio.run(call)
            if outcome == "unknown":
                assert result is None
            else:
                assert result.provider == "new-native-executor"
                assert result.catalogue.models[0].model_id == "future-model"
        else:
            error = asyncio.CancelledError if outcome == "cancel" else ProviderError
            with pytest.raises(error):
                asyncio.run(call)
        cleanup.assert_called_once_with(None if outcome == "foreign" else snapshot)
        if outcome == "foreign":
            copy.assert_not_called()
            assert not seen
        else:
            assert seen == [{
                "universe_dir": universe, "credential_snapshot_dir": snapshot.directory,
            }]
