"""Definition-driven CLI inference using the same engine-owned tool protocol."""
from __future__ import annotations

import asyncio
import json
import threading
import time

from tinyassets.exceptions import (
    ProviderAuthenticationError,
    ProviderProtocolError,
    ProviderReplyTimeoutError,
)
from tinyassets.providers.base import BaseProvider, ProviderResponse
from tinyassets.providers.subscription_runtime import (
    SubscriptionError,
    SubscriptionRuntime,
    SubscriptionTimeout,
)


class SubscriptionProvider(BaseProvider):
    agent_execution_kind = "engine_inference"
    supports_text_only = True

    def __init__(self, definition):
        self.definition = definition
        self.name = "subscription_cli:" + definition.id
        self.family = definition.ref
        self.model = definition.model

    async def complete(self, prompt, system, config, *, universe_dir=None):
        from tinyassets.owner_runtime import require
        from tinyassets.providers.agent_chat_codec import decode_agent_message

        require(universe_dir, self.definition.owner_user_id)
        if universe_dir.name != self.definition.universe_id:
            raise PermissionError("subscription source belongs to a different command center")
        self.require_text_only_support(config)
        runtime = SubscriptionRuntime(self.definition.ref)
        model = config.selected_model.model_id if config.selected_model else self.model
        request = config.agent_request
        if request is not None:
            _, body = request.encode(prompt=prompt, system=system, selection=config.selected_model,
                                     temperature=config.temperature, max_tokens=config.max_tokens)
            # Native tools are disabled by the source's invoke argv. Tool calls
            # are parsed by the existing engine codec and executed by its owner.
            prompt = json.dumps(body)
            system = (
                "Process this conversation and its supplied tool definitions. Return exactly one "
                "JSON assistant message, without markdown: {\"role\":\"assistant\","
                "\"content\":\"answer\",\"tool_calls\":[]}. To request a tool, use tool_calls "
                "entries with id, type=\"function\", and function:{name,arguments}; arguments "
                "is a JSON string. Use only the supplied tools. Do not execute tools yourself."
            )
        started = time.monotonic()
        cancelled = threading.Event()
        invocation = asyncio.create_task(asyncio.to_thread(
            runtime.invoke, prompt, system, model, should_cancel=cancelled.is_set,
            timeout=config.absolute_cap_s or config.timeout))
        try:
            text = await asyncio.shield(invocation)
        except asyncio.CancelledError:
            cancelled.set()
            try:
                await invocation
            except Exception:
                pass
            raise
        except SubscriptionTimeout as exc:
            # A run past its bound says nothing about sign-in; probing it here
            # would block this loop on the source lock and the status CLI.
            raise ProviderReplyTimeoutError(str(exc)) from exc
        except SubscriptionError as exc:
            if not runtime.authenticated():
                raise ProviderAuthenticationError(str(exc)) from exc
            raise ProviderProtocolError(str(exc)) from exc
        reply = None
        if request is not None:
            try:
                message = json.loads(text)
                if not isinstance(message, dict):
                    raise ValueError("assistant message must be an object")
                reply = decode_agent_message(
                    message, finish="tool_calls" if message.get("tool_calls") else "stop",
                    receipt="", input_tokens=None, output_tokens=None, source_ref=self.name,
                    requested_model=model,
                    tool_names=frozenset(tool["function"]["name"] for tool in request.tools()))
                text = reply.text or ""
            except (ValueError, TypeError, KeyError) as exc:
                raise ProviderProtocolError("CLI returned an invalid engine reply") from exc
        return ProviderResponse(text=text, provider=self.name, model=model, family=self.family,
                                latency_ms=(time.monotonic() - started) * 1000,
                                cost_microunits=0, requested_model=model,
                                provider_display=runtime.source["display_name"], agent_reply=reply)
