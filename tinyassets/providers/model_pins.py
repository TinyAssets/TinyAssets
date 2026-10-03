"""Resolve a node model pin's ``provider`` to one of this universe's sources.

A source is addressed by its exact ref, ``<access method>:<definition id>``
(``api_key_http:provdef_...``) or a bare subscription name (``codex``). An agent
writing a pin naturally names the access method and the model --
``{"provider": "api_key_http", "model_id": "google/gemma-4-31b-it:free"}`` --
and live 2026-10-01 every such run failed "workflow requests an unavailable
accepted provider", a refusal that named neither the shape it wanted nor the
refs it would have taken.

A bare access method resolves to the universe's ONE connected source of that
method, or to the one of them that offers the pinned model. Anything else is
refused with the choices listed, so the pin is fixable from the message alone.
Resolution only ever names a source the caller already admitted; it grants
nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from tinyassets.exceptions import ProviderAuthorityHeldError

#: The shape a pin takes, quoted in every refusal so it can be copied.
PIN_SHAPE = '{"preferred": {"provider": "<source>", "model_id": "<model>"}}'
#: Lower-case lead-in every refusal starts with; stored-error classifiers key
#: on it because only the string survives the async runner.
PIN_REFUSAL_MARKER = "model pin provider '"


class ModelPinError(ProviderAuthorityHeldError):
    """A node pin names no single source of this universe. Lists the choices.

    Held authority -- nothing was invoked -- but raised as its own words: the
    generic held lead-in clips its cause to 200 characters, which cut the very
    list of refs this refusal exists to give.
    """


def _method(ref: str) -> str:
    return ref.split(":", 1)[0]


def resolve_pin_source(
    provider: str, model_id: str, sources: Mapping[str, Iterable[str] | None],
) -> str:
    """The exact source ref ``provider`` names among ``sources``.

    ``sources`` maps each admitted source ref to the model ids it offers, or to
    ``None`` when the caller does not know them. An exact ref (``kind:id``) is
    returned as is, admitted or not: the caller's own check refuses it.
    A bare access method resolves to its single source, or -- when several share
    it -- to the single one known to offer ``model_id``. Raises
    :class:`ModelPinError` naming the accepted refs otherwise.
    """
    provider = str(provider or "").strip()
    if provider in sources or ":" in provider:
        # An exact ref -- held or not -- is the caller's own boundary to judge,
        # with its existing refusal: listing this universe's refs in answer to
        # a ref it does not hold would say more than that refusal does.
        return provider
    model_id = str(model_id or "").strip()
    same_method = [ref for ref in sources if ":" in ref and _method(ref) == provider]
    if len(same_method) == 1:
        return same_method[0]
    if same_method and model_id:
        offering = [
            ref for ref in same_method
            if sources[ref] is not None and model_id in set(sources[ref])
        ]
        if len(offering) == 1:
            return offering[0]
    choices = ", ".join(sorted(same_method or sources)) or "none connected"
    if same_method:
        raise ModelPinError(
            f"model pin provider '{provider}' matches several sources; name one of: "
            f"{choices} as {PIN_SHAPE}"
        )
    raise ModelPinError(
        f"model pin provider '{provider}' is not a source of this universe; use "
        f"{PIN_SHAPE} with provider one of: {choices}"
    )
