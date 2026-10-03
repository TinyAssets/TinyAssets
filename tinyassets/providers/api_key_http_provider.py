"""The ``api_key_http`` compute executor — run inference on an open, user-registered
provider over the credential-blind outbound proxy.

A :class:`~tinyassets.providers.base.BaseProvider` so the existing node-execution /
serving / router machinery consumes it unchanged (an agent is just a node — host
decision 2026-08-22). It composes:

- a :class:`~tinyassets.providers.definition.ProviderDefinition` (``api_key_http``):
  ``protocol`` selects the encoder; ``model`` is the request model; ``ref`` is the
  ``grant_id`` of the ``ConnectionLedger`` connection the owner registered (the
  connection carries the endpoint allow-list + the vault ``credential_ref``);
- the protocol encoders (``openai_chat`` / ``anthropic_messages``) to build the
  request body + path and decode the response — **never a vendor SDK on an arbitrary
  ``base_url``** (that would bypass SSRF + the credential-blind proxy);
- the outbound substrate's ``resolve_exact_scoped_proxy`` +
  ``proxy.request`` — the SAME SSRF-hardened, credential-blind broker worker that
  ``authenticated_external_call`` uses. The secret is applied inside the worker; it
  never exists in this process.

**Authorization = the connection grant alone (host decision 2026-08-22).** Running
inference on the universe's own granted compute is its core function, so — unlike an
outbound *effect* — this path does NOT require the per-destination effector consent
or the ``TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED`` effects flag. It DOES keep
every credential/network guard: the grant-identity gate (``resolve_exact_scoped_proxy``
re-checks grant.universe / owner / connection / not-revoked), the endpoint allow-list,
and SSRF + credential-blindness inside the worker. The universe-isolation gate
(grant bound to the RUNNING universe) is enforced here up front AND by the resolver.
Fail loud — never fabricate an empty completion (Hard Rule #8).
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import logging
import time
from pathlib import Path
from typing import Any

from tinyassets.exceptions import (
    ProviderAuthenticationError,
    ProviderModelRefusedError,
    ProviderOverloadedError,
    ProviderProtocolError,
    ProviderRateLimitedError,
    ProviderReplyError,
    ProviderReplyTimeoutError,
    ProviderStalledError,
    ProviderUnavailableError,
    ProviderUnreadableReplyError,
)
from tinyassets.providers.base import BaseProvider, ModelConfig, ProviderResponse
from tinyassets.providers.definition import ProviderDefinition
from tinyassets.providers.protocol_encoders import ENCODERS, ProtocolDecodeError, reported_model
from tinyassets.providers.wire_dialects import same_dialect

_LOG = logging.getLogger(__name__)

#: Seconds a STREAMED agent reply may go without new bytes before it counts as
#: stalled. A reply that keeps arriving is never cut for being slow (founder,
#: 2026-10-02): on a capped free tier an abandoned reply is one of the day's
#: requests gone. A source may declare its own ``reply_idle_s`` in its preset.
DEFAULT_REPLY_IDLE_S = 120.0

#: HTTP's own words for "not this model, not for you": forbidden, not found,
#: gone. Standard status semantics, not a vendor's error envelope.
_MODEL_REFUSAL_STATUSES = frozenset({403, 404, 410})


def _reply_budget_s(config: Any) -> float | None:
    """The turn's remaining absolute cap, as the budget to ask the broker for."""
    profile = getattr(config, "stream_timeout_profile", None)
    if not callable(profile):
        return None
    cap = profile().absolute_cap_s
    return float(cap) if type(cap) in (int, float) and cap > 0 else None


def _single_host(view: Any) -> str:
    """The connection's single allowlisted host. A compute connection targets one
    provider endpoint; an ambiguous (multi-host) or hostless connection is refused
    rather than guessed."""
    hosts = {
        ep.host for ep in getattr(view, "allowed_endpoints", ()) or () if getattr(ep, "host", "")
    }
    if len(hosts) == 1:
        return next(iter(hosts))
    raise ProviderUnavailableError("compute connection must have exactly one allowlisted host")


def _declared_path(view: Any) -> str:
    """The connection's single concrete POST path, or "" if it declares none.

    The user's own endpoint URL is what they granted, so it is what we must call.
    Before this, the grant carried the user's path (``/custom/chat``) while the
    encoder called the protocol's canonical one (``/v1/chat/completions``), and
    the broker refused the mismatch — every endpoint whose path was not the
    canonical one was registerable but could never serve.

    Read-only catalogue/account paths are not inference destinations and must
    not make a custom POST path ambiguous. A template (one containing a ``{``
    placeholder) is not concrete, and several POST paths remain ambiguous; both
    fall back to the protocol path, which the broker must still authorize.
    """
    paths = {
        str(getattr(ep, "path_template", "") or "")
        for ep in getattr(view, "allowed_endpoints", ()) or ()
        if "POST" in (getattr(ep, "methods", ()) or ())
        and str(getattr(ep, "path_template", "") or "")
    }
    if len(paths) != 1:
        return ""
    path = next(iter(paths))
    return "" if "{" in path else path


def _coerce_status(value: Any) -> int | None:
    # A well-formed proxy envelope carries an INT status. Reject floats/bools/
    # non-digit strings (Codex review): int(200.9) == 200 would let a malformed
    # envelope pass as success. bool is an int subclass, so exclude it explicitly.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _pre_generation(error):
    """Declare an ADMISSION refusal side-effect-free, as a FACT.

    Only for the statuses with which a source refuses a request *before*
    generating: 429 (its own rate limit) and the model-refusal statuses
    (access refused, no such model). The source answered with a status and
    nothing else, so no token was generated and no tool could have run, and
    saying so at the raise site is what lets `capacity_boundary` certify the
    transition to the next model.

    **A 5xx is deliberately NOT included.** It can come from a gateway after an
    upstream model already began producing output, so zero remote generation is
    unproved and the honest value is "unknown" -- which holds the node rather
    than replaying it elsewhere. Codex refutation R3, 2026-09-30: an earlier
    head labelled every 5xx side-effect-free and could not rule out a 502/504
    following generation.

    Said HERE rather than inferred at the router, because absence of the fact
    must keep meaning "unknown" for a streaming or native attempt, where a
    failure genuinely can follow partial work. It travels on the existing
    `attempt_telemetry` channel so there is one reader, not two.
    """
    error.attempt_telemetry = {"side_effect_state": "none"}
    return error


class ApiKeyHttpProvider(BaseProvider):
    """Compute over a user-registered http provider, via the credential-blind proxy."""

    agent_execution_kind = "engine_inference"
    supports_text_only = True

    def __init__(
        self, definition: ProviderDefinition, *, proxy_override: Any | None = None
    ) -> None:
        if definition.access_method != "api_key_http":
            raise ValueError("ApiKeyHttpProvider requires an api_key_http definition")
        if definition.protocol not in ENCODERS:
            raise ValueError(f"unsupported api_key_http protocol: {definition.protocol}")
        self._definition = definition
        self._proxy_override = proxy_override
        self._encode, self._decode = ENCODERS[definition.protocol]
        # Instance-level identity (BaseProvider declares class attrs; set per-instance
        # so distinct registered providers are distinguishable in telemetry/diversity).
        self.name = f"api_key_http:{definition.id}"
        self.family = f"api:{definition.protocol}"
        self.model = definition.model

    @classmethod
    def is_available(cls) -> bool:
        # Availability is per-call (does the grant resolve?), not a binary probe.
        return True

    @staticmethod
    def _capacity_detail(status: int, result: Any) -> str:
        """The source's OWN words for a refusal: its status and its body.

        A pre-generation capacity refusal used to reach the owner as the single
        word ``provider_rate_limited`` -- our class name, which says nothing the
        notice had not already said (live 2026-09-25). The body is the only
        place the source explains itself, and it belongs to the authenticated
        owner reading their own universe.

        No shape is assumed of it: parsing a vendor's error envelope would be
        vendor code (Hard Rule 3). It is treated as untrusted transport text --
        scrubbed for secrets and paths by the same gate every other attempt
        detail passes through, then bounded to the failure record's own limit.
        """
        from tinyassets.conversation_failure import DETAIL_LIMIT, clean_detail
        from tinyassets.providers.diagnostics import redacted_failure_detail

        body = result.get("body") if isinstance(result, dict) else None
        words = body if isinstance(body, str) else ""
        return clean_detail(
            redacted_failure_detail(f"HTTP {status}: {words}".strip(), limit=DETAIL_LIMIT)
        )

    def _resolve_proxy(
        self,
        *,
        db_path: Path,
        universe_id: str,
        grant_id: str,
        connection_id: str,
        owner_user_id: str,
    ) -> Any:
        if self._proxy_override is not None:
            return self._proxy_override
        from tinyassets.storage.outbound_connections import ConnectionLedger

        ledger = ConnectionLedger(db_path, verify_authenticated_principal=lambda: owner_user_id)
        return ledger.resolve_exact_scoped_proxy(
            universe_id=universe_id, grant_id=grant_id, connection_id=connection_id
        )

    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir: Path | None = None,
    ) -> ProviderResponse:
        self.require_text_only_support(config)
        # An executor Future (not a Task wrapping to_thread) survives the
        # cancel-all-Tasks phase of asyncio.run teardown. Shield alone would
        # not protect a to_thread Task from being cancelled directly there.
        context = contextvars.copy_context()
        operation = functools.partial(
            self._complete_sync,
            prompt,
            system,
            config,
            universe_dir=universe_dir,
        )
        worker = asyncio.get_running_loop().run_in_executor(None, context.run, operation)
        cancellation = None
        while True:
            try:
                response = await asyncio.shield(worker)
            except asyncio.CancelledError as exc:
                # Only this scope owns the Future; cancelling the calling task
                # never cancels the underlying synchronous request. Preserve the
                # caller's cancellation across success, failure and repeated cancels.
                cancellation = cancellation or exc
                if worker.cancelled():
                    raise cancellation
            except BaseException:
                if cancellation is not None:
                    raise cancellation from None
                raise
            else:
                if cancellation is not None:
                    raise cancellation
                return response

    def _complete_sync(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir: Path | None = None,
    ) -> ProviderResponse:
        self.require_text_only_support(config)
        if universe_dir is None:
            raise ProviderUnavailableError(
                "api_key_http compute requires a command center context (universe_dir)"
            )
        from tinyassets.storage.outbound_connections import (
            ConnectionAuthorizationError,
            ConnectionLedger,
            GrantResolutionError,
            OutboundDeadlineExceeded,
        )

        universe_dir = Path(universe_dir)
        db_path = universe_dir.parent / "outbound.db"
        universe_id = universe_dir.name
        grant_id = self._definition.ref

        # Grant read + universe-isolation gate (belt; resolver re-checks it too).
        read_ledger = ConnectionLedger(db_path)
        grant = read_ledger.get_grant(grant_id)
        if grant is None or getattr(grant, "revoked_at", None) is not None:
            raise ProviderUnavailableError(f"compute grant {grant_id} is absent or revoked")
        if getattr(grant, "universe_id", "") != universe_id:
            raise ProviderUnavailableError("compute grant is not bound to the running command "
                "center")
        connection_id = grant.connection_id
        owner_user_id = grant.owner_user_id
        view = read_ledger.get_connection_view(connection_id)
        if view is None:
            raise ProviderUnavailableError("compute connection resource is absent")
        host = _single_host(view)

        selection = getattr(config, "selected_model", None)
        agent_request = getattr(config, "agent_request", None)
        if selection is not None:
            contract = selection.contract()
            if (
                selection.provider != self.name
                or not same_dialect(contract.inference_protocol, self._definition.protocol)
            ):
                raise ProviderUnavailableError("selected model does not match the compute source")
            if config.engine_mcp_enabled and agent_request is None:
                raise ProviderUnavailableError(
                    "selected HTTP agent tool execution is not implemented yet"
                )
        if agent_request is not None:
            from tinyassets.providers.agent_inference import AgentInferenceRequest
            from tinyassets.providers.protocol_encoders import agent_codec_for

            agent_codec = agent_codec_for(self._definition.protocol)
            if (type(agent_request) is not AgentInferenceRequest or selection is None
                    or not (config.engine_mcp_enabled or agent_request.text_only)
                    or agent_codec is None):
                raise ProviderUnavailableError("HTTP agent inference requires admitted selection")
            protocol_path, body = agent_request.encode(
                prompt=prompt, system=system, selection=selection,
                temperature=config.temperature, max_tokens=config.max_tokens,
            )
        else:
            protocol_path, body = self._encode(
                prompt=prompt,
                system=system,
                model=self.model if selection is None else selection.model_id,
                temperature=getattr(config, "temperature", None),
                max_tokens=getattr(config, "max_tokens", None),
            )
            if selection is not None:
                body = contract.constrain_inference(body, selection.cost_caps)
        if getattr(config, "text_only", False):
            # Only the installed text wire is proven tool-free. In particular,
            # source-contract extensions must not reintroduce tools/plugins or
            # completed agent history after the text encoder ran. Unknown
            # extensions refuse rather than silently dropping billing controls.
            from tinyassets.exceptions import ProviderAuthorityHeldError

            if (type(body) is not dict or not {"model", "messages"} <= body.keys()
                    or body.keys() - {"model", "messages", "system", "temperature", "max_tokens"}
                    or type(body["messages"]) is not list
                    or any(type(message) is not dict
                           or message.keys() != {"role", "content"}
                           or message["role"] not in {"system", "user"}
                           or type(message["content"]) is not str
                           for message in body["messages"])
                    or ("system" in body and type(body["system"]) is not str)):
                raise ProviderAuthorityHeldError(
                    "selected HTTP request does not support enforced text-only review; "
                    "nothing was sent"
                )
        # The path the user granted wins over the protocol's canonical one: the
        # broker allowlists what they registered, so calling anything else is a
        # guaranteed refusal. The encoder still owns the BODY shape.
        path = _declared_path(view) or protocol_path
        # Protocol-static, credential-FREE headers (e.g. anthropic-version, required
        # by the Anthropic Messages API or it 400s). The api key is NOT here — the
        # broker applies it from the connection's auth_scheme (x-api-key for Claude).
        from tinyassets.providers.protocol_encoders import static_headers_for

        # An agent body already asks to stream (``AgentInferenceRequest.encode``):
        # every agent wire is chat_messages, whose servers stream on request, and
        # the decoder folds events and plain JSON alike.
        wire_request: dict[str, Any] = {"url": f"https://{host}{path}", "body": body}
        # Ask for as long as the turn itself may still run. The broker grants it
        # only because this connection is a model source, and never beyond its
        # own ceiling; a model writing a whole app needs minutes, not 30s.
        reply_budget = _reply_budget_s(config)
        if reply_budget is not None:
            wire_request["reply_budget_s"] = reply_budget
            if agent_request is not None:
                from tinyassets.providers.free_sources import source_for_host

                idle = source_for_host(host).get("reply_idle_s", DEFAULT_REPLY_IDLE_S)
                wire_request["reply_idle_s"] = float(idle)
        static_headers = static_headers_for(self._definition.protocol)
        if static_headers:
            wire_request["headers"] = static_headers

        started = time.monotonic()
        try:
            proxy = self._resolve_proxy(
                db_path=db_path,
                universe_id=universe_id,
                grant_id=grant_id,
                connection_id=connection_id,
                owner_user_id=owner_user_id,
            )
            try:
                result = proxy.request("POST", wire_request)
                # Inference latency excludes the owned worker's cleanup/join.
                latency_ms = (time.monotonic() - started) * 1000.0
            finally:
                if self._proxy_override is None:
                    try:
                        proxy.close()
                    except Exception:  # noqa: BLE001 - preserve result and secret-free diagnostics
                        _LOG.warning("HTTP inference proxy cleanup failed")
        except GrantResolutionError as exc:
            raise ProviderUnavailableError(
                f"compute grant resolution failed: {exc}"
            ) from exc
        except OutboundDeadlineExceeded:
            from tinyassets.storage.outbound_connections import INFERENCE_MAX_SECONDS

            # The budget that actually ended it: the broker grants at most its
            # own ceiling, so the turn's remaining time (live 2026-10-02:
            # "2591705s") is not the number the owner should read.
            raise ProviderReplyTimeoutError(
                "the model did not finish answering within its reply budget"
                + (f" ({int(min(reply_budget, INFERENCE_MAX_SECONDS))}s)"
                   if reply_budget is not None else "")
            ) from None
        except ConnectionAuthorizationError as exc:
            # A refresh that failed is a connection/auth failure (the class
            # maps to the connection stage), with the token endpoint's words.
            error = ProviderAuthenticationError(
                "compute connection authorization failed"
                + (f": {exc.detail}" if exc.detail else "")
            )
            error.connection_failure = exc.failure
            raise error from None

        if not isinstance(result, dict):
            if agent_request is not None:
                raise ProviderProtocolError("agent inference outcome is unknown")
            raise ProviderUnavailableError("compute proxy returned no response")
        status = _coerce_status(result.get("status"))
        if status is None:
            if agent_request is not None:
                # The proxy was called. No status does not prove no remote work;
                # hold the full reservation rather than report a free attempt.
                raise ProviderProtocolError("agent inference outcome is unknown")
            # A sanitized error envelope (no HTTP status) — the worker refused or
            # the network failed. Fail loud with the secret-free reason.
            reason = str(result.get("reason") or result.get("error") or "unknown")
            raise ProviderUnavailableError(f"compute call failed: {reason}")
        # EVERY call, not only an agent round. `agent_request` is a tool-loop
        # concern and says nothing about whether the source reported a capacity
        # window, but it used to gate this decode -- so the identical 429, from
        # the identical source, through the identical decoder, reached a workflow
        # node as a bare `ProviderRateLimitedError` with no scope, no
        # `Retry-After` and no side-effect fact. `capacity_boundary` could not
        # certify that as a safe transition, so one 429 ended the whole run
        # while a chat turn on the same source stepped to the next free model
        # (live 2026-09-30, run `c22c1cb12db74d6a`, one attempt on an account
        # holding 632 models).
        if selection is not None:
            from tinyassets.exceptions import SelectedModelCapacityError
            from tinyassets.providers.daily_quota import daily_detail, daily_quota_signal
            from tinyassets.providers.free_sources import billing_url_for_host, source_for_host

            capacity = daily_quota_signal(
                status, result.get("headers"), result.get("body"),
                daily_request_headers=source_for_host(host).get("daily_request_headers", False),
                daily_reset_timezone=source_for_host(host).get("daily_reset_timezone"),
            )
            detail = None
            if capacity is not None:
                detail = daily_detail(capacity, billing_url=billing_url_for_host(host))
            elif contract.capacity_decoder is not None:
                capacity = contract.capacity_decoder(status, result.get("headers"))
            if status == 402:
                from tinyassets.providers.model_capacity import CapacitySignal, retry_after_seconds

                if capacity is None:
                    capacity = CapacitySignal(
                        "account", "provider_credit_exhausted",
                        retry_after_seconds(result.get("headers")),
                    )
                detail = "Provider credit exhausted. Add credit: " + billing_url_for_host(host)
            if capacity is not None:
                error = SelectedModelCapacityError(
                    capacity, detail=detail or self._capacity_detail(status, result)
                )
                # Only a 4xx admission refusal proves nothing was generated; a
                # 5xx may be a gateway answering after upstream output began.
                raise _pre_generation(error) if status < 500 else error
        if status == 401:
            # Still refused after the broker's one refresh-and-retry (oauth2),
            # or a key the service no longer accepts: a sign-in problem.
            raise ProviderAuthenticationError("compute provider rejected the credential (401)")
        if status == 429:
            raise _pre_generation(
                ProviderRateLimitedError("compute provider rate limited (429)")
            )
        if 500 <= status < 600:
            raise ProviderOverloadedError(f"compute provider error (HTTP {status})")
        if status in _MODEL_REFUSAL_STATUSES:
            # Access refused, or no such model to serve: nothing was generated,
            # so this is neither a reply we failed to read nor a sick source.
            raise _pre_generation(
                ProviderModelRefusedError(self._capacity_detail(status, result))
            )
        if not (200 <= status < 300):
            raise ProviderProtocolError(
                self._capacity_detail(status, result)
                or f"compute provider returned HTTP {status}"
            )

        # A 2xx we cannot read, on an agent round, is the model's slip rather
        # than the source refusing the request: the turn may retry it.
        unreadable = (
            ProviderUnreadableReplyError if agent_request is not None else ProviderProtocolError
        )
        body_str = result.get("body")
        if agent_request is not None and result.get("stalled") is True:
            from tinyassets.providers.agent_chat_codec import partial_stream_text

            partial = partial_stream_text(body_str) if isinstance(body_str, str) else ""
            idle = wire_request.get("reply_idle_s")
            raise ProviderStalledError(
                "the model stopped sending partway through its reply"
                + (f" (nothing for {int(idle)}s" if idle else " (")
                + f", {len(partial)} characters of text received)",
                partial_text=partial,
            )
        if not isinstance(body_str, str) or not body_str:
            raise unreadable("compute response had an empty body")
        try:
            if agent_request is not None:
                from tinyassets.providers.agent_chat_codec import (
                    _object,
                    fold_chat_stream,
                    is_event_stream,
                )

                # A server may stream even when not asked to; the events fold
                # into the single response they describe, then decode as one.
                if is_event_stream(body_str):
                    parsed = fold_chat_stream(body_str)
                    body_str = json.dumps(parsed, ensure_ascii=False)
                else:
                    parsed = _object(body_str)
            else:
                parsed = json.loads(body_str)
        except (TypeError, ValueError) as exc:
            raise unreadable(f"compute response was not JSON: {exc}") from exc
        if getattr(config, "text_only", False):
            # Do not accept a verdict alongside an unexpected tool request.
            pending = [parsed]
            while pending:
                value = pending.pop()
                if isinstance(value, dict):
                    if ({"tool_calls", "function_call"} & value.keys()
                            or value.get("type") in ("tool_use", "server_tool_use")):
                        from tinyassets.exceptions import ProviderAuthorityHeldError

                        raise ProviderAuthorityHeldError("text-only review returned a tool request")
                    pending.extend(value.values())
                elif isinstance(value, list):
                    pending.extend(value)
        agent_reply = None
        cost = None
        try:
            if agent_request is not None:
                agent_reply = agent_codec.decode(
                    parsed, source_ref=selection.provider, requested_model=selection.model_id,
                    tool_names=frozenset(
                        item["function"]["name"] for item in agent_request.tools()
                    ),
                )
                if (agent_reply.stop == "truncated" and agent_reply.text is None
                        and not agent_reply.tool_requests):
                    # "Succeeded with nothing": a cold start, or a reasoning
                    # model that spent its whole output on thinking. Nothing to
                    # keep, and the next attempt usually answers.
                    raise ProviderUnreadableReplyError(
                        "the model stopped at its output limit before replying "
                        "(finish_reason length, no content)"
                    )
                text = agent_reply.text or ""
                in_tok, out_tok = agent_reply.input_tokens, agent_reply.output_tokens
            else:
                text, in_tok, out_tok = self._decode(parsed)
            if selection is not None and contract.usage_decoder is not None:
                cost = contract.usage_decoder(body_str)
        except ProtocolDecodeError as exc:
            words = getattr(exc, "source_error", None)
            if isinstance(words, str):
                # The source said generation failed; keep its own words.
                from tinyassets.providers.diagnostics import redacted_failure_detail

                # Only the agent wire codec marks one, so this is an agent round.
                raise ProviderReplyError(
                    "the model's source reported an error instead of a reply: "
                    + (redacted_failure_detail(words) or "no detail given")
                ) from exc
            raise unreadable(str(exc)) from exc

        return ProviderResponse(
            text=text,
            provider=self.name,
            model=reported_model(parsed),
            reported_model=reported_model(parsed),
            family=self.family,
            latency_ms=latency_ms,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_microunits=cost,
            agent_reply=agent_reply,
        )
