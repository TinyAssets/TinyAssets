"""Generic, bounded external-agent package operations."""
from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import replace
from datetime import datetime, timezone

from tinyassets.exceptions import ProviderError
from tinyassets.external_agents import receipts, registry
from tinyassets.external_agents.home import Custody, compare_and_swap, materialize
from tinyassets.providers.native_catalogue import NativeCatalogue
from tinyassets.providers.native_discovery import NativeDiscoverySnapshot
from tinyassets.providers.native_jsonrpc_discovery import (
    NativeControlProtocol,
    NativeJsonRpcProtocol,
    parse_model_page,
)

_MAX_BYTES = 4 * 1024 * 1024
_MAX_PAGES = 64
_MAX_MODELS = 4096


def _key(pointer: str) -> str:
    if pointer.count("/") != 1:
        raise ValueError("nested external-agent catalogue pointer is unsupported")
    return pointer[1:]


def _protocol(operation: dict):
    row = operation["row"]
    listed = operation["list"]
    common = dict(
        list_method=listed["method"], items_key=_key(listed["items"]),
        model_key=_key(row["model"]), default_key=_key(row["default"]),
        modalities_key=_key(row["modalities"]), hidden_key=_key(row["hidden"]),
        cursor_key=_key(listed["cursor"]) if "cursor" in listed else None,
        cursor_param=listed.get("cursor_param"),
        list_params_json=json.dumps(listed["params"]),
        effort_levels_key=_key(row["effort_levels"]) if "effort_levels" in row else None,
        effort_level_key=_key(row["effort_level"]) if "effort_level" in row else None,
    )
    if operation["transport"] == "jsonrpc":
        init = operation.get("initialize") or {}
        return NativeJsonRpcProtocol(
            **common,
            initialize_method=init.get("method"),
            initialize_params_json=json.dumps(init.get("params", {})),
            initialized_notification=init.get("notification"),
        )
    return NativeControlProtocol(**common)


async def _read(proc, protocol, operation):
    consumed = 0

    async def send(message):
        proc.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        await proc.stdin.drain()

    async def response(token):
        nonlocal consumed
        while True:
            line = await proc.stdout.readline()
            consumed += len(line)
            if not line or consumed > _MAX_BYTES:
                raise ValueError("external-agent catalogue incomplete or oversized")
            message = json.loads(line)
            if type(message) is not dict:
                raise ValueError("invalid external-agent catalogue envelope")
            result = protocol.decode_response(message, token)
            if result is not None:
                return result

    if protocol.initialize_method is not None:
        token = protocol.request_token(0)
        await send(protocol.encode_request(
            protocol.initialize_method, token,
            operation["initialize"]["params"],
        ))
        await response(token)
    if protocol.initialized_notification is not None:
        await send(protocol.encode_notification(protocol.initialized_notification))
    models, defaults, cursors = [], [], set()
    cursor = None
    for index in range(1, _MAX_PAGES + 1):
        params = dict(operation["list"]["params"])
        if cursor is not None:
            params[operation["list"]["cursor_param"]] = cursor
        token = protocol.request_token(index)
        await send(protocol.encode_request(protocol.list_method, token, params))
        page, recommended, cursor = parse_model_page(await response(token), protocol)
        models.extend(page)
        defaults.extend(recommended)
        if len(models) > _MAX_MODELS or len(set(defaults)) > 1:
            raise ValueError("external-agent catalogue limit or conflicting defaults")
        if cursor is None:
            return NativeCatalogue(tuple(models), defaults[0] if defaults else None,
                                   datetime.now(timezone.utc))
        if cursor in cursors:
            raise ValueError("external-agent catalogue cursor repeated")
        cursors.add(cursor)
    raise ValueError("external-agent catalogue page limit exceeded")


async def run_catalogue(agent: str, launch, custody: Custody) -> NativeDiscoverySnapshot:
    """One receipted package operation under exact owner custody."""
    package = registry.resolve(agent)
    if package.row["auth"]["service"] != custody.reference.service:
        raise PermissionError("external-agent package service differs from custody")
    universe = custody.universe_dir.resolve(strict=True)
    receipt_id = receipts.admit(
        universe.parent, owner=custody.owner_user_id, universe_id=universe.name,
        agent=agent, package_digest=package.digest,
        custody_digest=custody.reference.reference_digest,
    )
    proc, original, result, error = None, {}, None, None
    receipts.start(universe.parent, receipt_id)
    observed = datetime.now(timezone.utc)
    try:
        original = materialize(custody, package.row["auth"])
        operation = package.row["catalogue"]
        argv = [str(package.executable), *operation["argv"]]
        home = "/agent-home/" + package.row["auth"]["home_dir"]
        env = {package.row["auth"]["home_env"]: home}
        proc = await launch.spawn(argv, env, home="/agent-home",
                                  workdir="/agent-home/jobs/" + secrets.token_hex(16),
                                  egress=universe.name, auth_files=original,
                                  auth=package.row["auth"])
        async with asyncio.timeout(receipts.DEADLINE_SECONDS):
            catalogue = await _read(proc, _protocol(operation), operation)
        completed = datetime.now(timezone.utc)
        if not custody.current():
            raise PermissionError("external-agent custody changed during catalogue")
        result = NativeDiscoverySnapshot(agent, custody.owner_user_id, universe,
                                         custody.reference, observed, completed, catalogue)
    except BaseException as exc:
        error = exc
    finally:
        if proc is not None:
            try:
                returned = await launch.close(proc)
                changed = {name: data for name, data in returned.items()
                           if name in original and data != original[name]}
                if changed:
                    if not compare_and_swap(custody, package.row["auth"], changed):
                        raise PermissionError("external-agent custody changed before writeback")
                    carried = custody.reload()
                    if (carried is None or
                            carried.reference_digest != custody.reference.reference_digest):
                        raise RuntimeError("external-agent custody carry was lost")
                    if result is not None:
                        result = replace(result, custody=carried)
            except BaseException as exc:
                if error is None:
                    error = exc
        outcome = "succeeded" if error is None else (
            "cancelled" if isinstance(error, asyncio.CancelledError) else
            "timed_out" if isinstance(error, TimeoutError) else "failed")
        receipts.finish(universe.parent, receipt_id, outcome=outcome,
                        model_count=len(result.catalogue.models) if result else 0,
                        reason=type(error).__name__ if error else "")
    if error is not None:
        if not isinstance(error, Exception):
            raise error
        raise ProviderError("external-agent catalogue failed: " + type(error).__name__) from None
    return result
