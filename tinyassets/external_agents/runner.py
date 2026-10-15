"""Generic, bounded external-agent package operations."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from dataclasses import replace
from datetime import datetime, timezone

from tinyassets.credential_refresh import RefreshRejected
from tinyassets.exceptions import (
    ExternalAgentUnavailable,
    ProviderAuthenticationError,
    ProviderError,
    ProviderTimeoutError,
)
from tinyassets.external_agents import receipts, registry
from tinyassets.external_agents.home import (
    Custody,
    RefreshBusyError,
    compare_and_swap,
    exclusive_refresh,
    materialize,
)
from tinyassets.providers.base import ProviderResponse
from tinyassets.providers.native_discovery import NativeDiscoverySnapshot
from tinyassets.providers.native_jsonrpc_discovery import (
    NativeControlProtocol,
    NativeJsonRpcProtocol,
    read_native_catalogue,
)

logger = logging.getLogger(__name__)


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


async def run_catalogue(agent: str, launch, custody: Custody) -> NativeDiscoverySnapshot:
    """One receipted package operation under exact owner custody."""
    try:
        async with exclusive_refresh(custody):
            try:
                result = await _run_catalogue_locked(agent, launch, custody)
            except (RefreshRejected, ProviderAuthenticationError):
                _record_auth_rejection(custody)
                raise ProviderAuthenticationError(
                    f"{agent} needs you to sign in again"
                ) from None
            _clear_auth_rejection(custody)
            return result
    except RefreshBusyError:
        raise ProviderError('external-agent credential refresh busy') from None


async def _run_catalogue_locked(agent, launch, custody):
    package = registry.resolve(agent)
    if package.row["auth"]["service"] != custody.reference.service:
        raise PermissionError("external-agent package service differs from custody")
    if not package.executable.is_file():
        raise ProviderError("external-agent unavailable: not_installed")
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
        if callable(launch):
            launch = launch(receipt_id)
        original = materialize(custody, package.row["auth"])
        operation = package.row["catalogue"]
        argv = [str(package.executable), *operation["argv"]]
        home = "/agent-home/" + package.row["auth"]["home_dir"]
        env = {package.row["auth"]["home_env"]: home}
        proc = await launch.spawn(argv, env, home="/agent-home",
                                  workdir="/agent-home/jobs/" + secrets.token_hex(16),
                                  egress=universe.name, auth_files=original,
                                  auth=package.row["auth"])
        catalogue = await read_native_catalogue(
            argv, protocol=_protocol(operation), env=env, cwd=None,
            universe_dir=universe, timeout=receipts.REFRESH_TIMEOUT_SECONDS,
            process=proc,
        )
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
                if hasattr(launch, "acknowledge"):
                    await launch.acknowledge(proc)
                if getattr(returned, "exit_code", 0) != 0:
                    raise RuntimeError("external-agent process failed")
            except BaseException as exc:
                if error is None:
                    error = exc
        outcome = "succeeded" if error is None else (
            "cancelled" if isinstance(error, asyncio.CancelledError) else
            "timed_out" if isinstance(error, TimeoutError) else "failed")
        reason = ("timeout" if isinstance(error, TimeoutError) else
                  "cancelled" if isinstance(error, asyncio.CancelledError) else
                  "custody" if isinstance(error, PermissionError) else
                  "failed" if error else "")
        receipts.finish(universe.parent, receipt_id, outcome=outcome,
                        model_count=len(result.catalogue.models) if result else 0,
                        reason=reason)
    if error is not None:
        if not isinstance(error, Exception):
            raise error
        if isinstance(error, (ProviderAuthenticationError, RefreshRejected)):
            raise error
        raise ProviderError("external-agent catalogue failed: " + reason) from None
    return result


async def _engine_tools(stack, config):
    """The same owner-pinned tool route used by the other served executors."""
    from tinyassets.served_tools import granted_tools, model_tools

    actor = (config.engine_mcp_actor_id or "").strip()
    center = (config.engine_mcp_graph_id or "").strip()
    enabled = model_tools(config)
    if not (config.engine_mcp_enabled and actor and center and enabled):
        return None
    from tinyassets.engine_steering import session_of, turn_of
    from tinyassets.engine_tool_client import open_engine_tools

    return await stack.enter_async_context(open_engine_tools(
        actor_id=actor, graph_id=center, enabled_tools=enabled,
        capability_grant=granted_tools(config),
        timeout=config.stream_timeout_profile().absolute_cap_s,
        session_key=session_of(config), turn=turn_of(),
        context_tokens=getattr(config.selected_model, "context_tokens", None),
    ))


def _job_failure_reason(error, turn, outcome) -> str:
    """Return only a declared, bounded failure category."""
    if error is None:
        return ""
    if isinstance(error, asyncio.CancelledError):
        return "job_failed:cancelled"
    if isinstance(error, (TimeoutError, ProviderTimeoutError)):
        return "job_failed:timeout"
    if isinstance(error, PermissionError):
        return "job_failed:custody"
    if isinstance(error, ProviderAuthenticationError):
        return "auth_invalid"
    if isinstance(error, ProviderError):
        if "did not complete with text and usage" in str(error):
            return "job_failed:incomplete"
        if "refused" in str(error):
            detail = str(error).removeprefix("external agent refused ")
            if detail != str(error) and detail.count(":") == 1:
                method, category = detail.split(":")
                if (turn is not None and method == turn.phase
                        and method in {turn.job["start"]["method"],
                                       turn.job["resume"]["method"],
                                       turn.job["input"]["method"]}
                        and category in {"model_unavailable", "permission_denied",
                                         "rate_limited", "invalid_request", "unknown"}):
                    return f"job_failed:refused:{method}:{category}"
            return "job_failed:refused"
        if "exited" in str(error):
            return "job_failed:exited"
        return "job_failed:protocol"
    return "job_failed:failed"


async def run_job(agent: str, launch, custody: Custody, *, prompt: str,
                  system: str, config) -> ProviderResponse:
    """Execute one native turn and settle its custody and job receipt."""
    try:
        async with exclusive_refresh(custody):
            try:
                result = await _run_job_locked(agent, launch, custody, prompt=prompt,
                                               system=system, config=config)
            except (RefreshRejected, ProviderAuthenticationError):
                _record_auth_rejection(custody)
                raise ProviderAuthenticationError(
                    f"{agent} needs you to sign in again"
                ) from None
            _clear_auth_rejection(custody)
            return result
    except RefreshBusyError:
        raise ExternalAgentUnavailable(agent, config.native_model_id or '',
                                       'job_failed:refresh_busy') from None


def _record_auth_rejection(custody: Custody) -> None:
    from tinyassets.credential_vault import record_refresh_rejected

    universe = custody.universe_dir.resolve(strict=True)
    record_refresh_rejected(universe.parent, universe_id=universe.name,
                            service=custody.reference.service)


def _clear_auth_rejection(custody: Custody) -> None:
    from tinyassets.credential_vault import clear_refresh_rejected

    universe = custody.universe_dir.resolve(strict=True)
    clear_refresh_rejected(universe.parent, universe_id=universe.name,
                           service=custody.reference.service)


async def _run_job_locked(agent, launch, custody, *, prompt, system, config):
    from tinyassets import agent_sessions
    from tinyassets.agent_definition import agent_definition
    from tinyassets.external_agents.jsonrpc_job import JsonRpcJob, method_params, tools_digest
    from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence

    package = registry.resolve(agent)
    model = config.native_model_id or ""
    if not package.executable.is_file():
        raise ExternalAgentUnavailable(agent, model, "not_installed")
    universe = custody.universe_dir.resolve(strict=True)
    if (custody.reference.service != package.row["auth"]["service"]
            or not config.invocation_owner_user_id
            or config.invocation_owner_user_id != custody.owner_user_id):
        raise ExternalAgentUnavailable(agent, model, "job_failed:custody")
    receipt_id = receipts.admit_job(
        universe.parent, owner=custody.owner_user_id, universe_id=universe.name,
        agent=agent, package_digest=package.digest,
        custody_digest=custody.reference.reference_digest, requested_model=model,
    )
    proc = None
    turn = None
    original = {}
    outcome = None
    error = None
    started = time.monotonic()
    try:
        original = materialize(custody, package.row["auth"])
        job = package.row["job"]
        if job["transport"] != "jsonrpc":
            raise ValueError("external-agent job transport is unsupported")
        async with contextlib.AsyncExitStack() as stack:
            tools = await _engine_tools(stack, config)
            definition = agent_definition(tools.tools if tools else (), system)
            ref = config.agent_session
            session_model = model + ":" + tools_digest(definition)
            with agent_sessions.exclusive(ref) as locked:
                record = (agent_sessions.resumable(
                    ref, adapter=agent, model=session_model, prompt=prompt)
                    if locked else None)
                resume = str(record["handle"]) if record else ""
                input_text = ref.resume_prompt if record else prompt
                workdir = "/agent-home/jobs/" + secrets.token_hex(16)
                argv = [str(package.executable), *job["argv"]]
                home = "/agent-home/" + package.row["auth"]["home_dir"]
                proc = await launch.spawn(
                    argv, {package.row["auth"]["home_env"]: home},
                    home="/agent-home", workdir=workdir, egress=universe.name,
                    auth_files=original, auth=package.row["auth"],
                    catalogue_rewrite=job.get("catalogue_rewrite"),
                    requested_model=model or None,
                )
                turn = JsonRpcJob(
                    proc, job=job, agent=agent, tools=tools,
                    profile=config.stream_timeout_profile(), start=time.monotonic(),
                    turn_wait=900.0, tool_wait=900.0,
                )
                outcome = await turn.run(
                    definition=definition, model=model or None,
                    input_text=input_text, effort=config.reasoning_effort or None,
                    workdir=getattr(proc, "workdir", workdir), resume=resume,
                )
                if outcome.auth_rejected:
                    raise ProviderAuthenticationError(
                        "external agent needs a fresh sign-in")
                if (outcome.status != job["events"]["done"]["success"]
                        or not outcome.messages or not outcome.usage_seen):
                    raise ProviderError("external-agent turn did not complete with text and usage")
                if not custody.current():
                    raise PermissionError("external-agent custody changed during job")
                if locked and ref is not None:
                    agent_sessions.save(ref, adapter=agent, model=session_model,
                                        handle=outcome.thread_id, system=system)
    except BaseException as exc:
        error = exc
        if (isinstance(exc, (asyncio.CancelledError, ProviderTimeoutError))
                and turn is not None and turn.outcome.thread_id):
            cancel = package.row["job"]["cancel"]
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(turn.notify(
                    cancel["method"], method_params(cancel, {
                        "session": turn.outcome.thread_id,
                    }))), timeout=cancel["grace_s"])
    finally:
        if proc is not None:
            try:
                returned = await launch.close(proc)
                changed = {name: data for name, data in returned.items()
                           if name in original and data != original[name]}
                if changed and not compare_and_swap(custody, package.row["auth"], changed):
                    raise PermissionError("external-agent custody changed before writeback")
                if hasattr(launch, "acknowledge"):
                    await launch.acknowledge(proc)
                if getattr(returned, "exit_code", 0) != 0:
                    raise RuntimeError("external-agent process failed")
            except BaseException as exc:
                if error is None:
                    error = exc
        reason = _job_failure_reason(error, turn, outcome)
        if (model and reason.endswith(":model_unavailable")
                and turn is not None and turn.phase in {
                    job["start"]["method"], job["input"]["method"]}):
            try:
                from tinyassets.storage.refused_models import record_refused_model

                record_refused_model(
                    universe.parent, owner_user_id=custody.owner_user_id,
                    connection_id=agent, model_id=model,
                    failure_class="provider_refused", detail=reason,
                )
                from tinyassets.providers.shortlist_refresh import SHORTLIST_CACHE

                SHORTLIST_CACHE.schedule(base=universe.parent, owner=custody.owner_user_id,
                                         universe_id=universe.name, provider=agent)
            except Exception:
                logger.warning("external-agent refusal refresh failed", exc_info=True)
        evidence = outcome or (turn.outcome if turn is not None else None)
        receipts.finish_job(
            universe.parent, receipt_id,
            outcome="succeeded" if error is None else (
                "cancelled" if isinstance(error, asyncio.CancelledError) else
                "timed_out" if isinstance(error, (TimeoutError, ProviderTimeoutError))
                else "failed"),
            reason=reason,
            resolved_model=(evidence.configured_model or model) if evidence else model,
            reported_model=evidence.reported_model if evidence else "",
            native_session=evidence.thread_id if evidence else "",
            input_tokens=evidence.input_tokens if evidence and evidence.usage_seen else None,
            output_tokens=evidence.output_tokens if evidence and evidence.usage_seen else None,
        )
    if error is not None:
        if not isinstance(error, Exception):
            raise error
        if isinstance(error, (ExternalAgentUnavailable, ProviderAuthenticationError,
                              RefreshRejected)):
            raise error
        raise ExternalAgentUnavailable(agent, model, reason) from None
    return ProviderResponse(
        text=outcome.messages[-1].strip(), provider=agent,
        model=model or "provider-default", requested_model=model,
        configured_model=outcome.configured_model or model,
        reported_model=outcome.reported_model, family=package.row["family"],
        latency_ms=(time.monotonic() - started) * 1000,
        input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens,
        cost_microunits=0,
        native_evidence=NativeCompletionEvidence(agent, False, True, "unknown"),
    )
