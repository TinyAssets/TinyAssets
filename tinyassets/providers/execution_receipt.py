"""Request-owned answer telemetry, never routing authority or shared state."""

import json
from dataclasses import dataclass, field
from typing import Any

from tinyassets.providers.base import ProviderResponse


def _label(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= maximum or not value.isprintable():
        return ""
    return value.strip()


#: The three fields every receipt has carried. ``provider_display`` and
#: ``requested_model`` are optional and ABSENT when nothing resolved, so a row
#: stored before they existed still normalizes and renders no blank label.
_REQUIRED_FIELDS = {"provider", "model", "model_status"}
_OPTIONAL_FIELDS = ("provider_display", "requested_model")


_USAGE_COUNTS = ("reserved", "dispatched", "succeeded", "failed", "unknown", "not_sent")
_USAGE_PURPOSES = {"reply", "tool_review", "review", "helper", "learning"}


def normalize_request_usage(value: object) -> dict[str, Any] | None:
    """Small detached display evidence; detailed attempts stay in their usage store."""
    required = {"reserved", "dispatched", "closed", "sources", "sources_omitted",
                "quota_authoritative", "count_basis"}
    if (not isinstance(value, dict) or not required <= value.keys()
            or value.keys() - required - {"usage_id"}
            or value["quota_authoritative"] is not False
            or value["count_basis"] != "local_provider_dispatch"
            or type(value["closed"]) is not bool
            or any(type(value[key]) is not int or not 0 <= value[key] <= 2**63 - 1
                   for key in ("reserved", "dispatched", "sources_omitted"))
            or value["dispatched"] > value["reserved"]
            or not isinstance(value["sources"], list) or len(value["sources"]) > 64):
        return None
    usage_id = value.get("usage_id")
    if usage_id is not None and (not isinstance(usage_id, str) or len(usage_id) != 32
                                or any(c not in "0123456789abcdef" for c in usage_id)):
        return None
    sources = []
    for source in value["sources"]:
        if (not isinstance(source, dict)
                or source.keys() != {"source_ref", "purpose", *_USAGE_COUNTS}
                or not source["source_ref"]
                or _label(source["source_ref"], 400) != source["source_ref"]
                or not isinstance(source["purpose"], str)
                or source["purpose"] not in _USAGE_PURPOSES
                or any(type(source[key]) is not int or not 0 <= source[key] <= 2**63 - 1
                       for key in _USAGE_COUNTS)
                or source["reserved"] == 0
                or source["dispatched"] + source["not_sent"] > source["reserved"]
                or sum(source[key] for key in ("succeeded", "failed", "unknown"))
                > source["dispatched"]):
            return None
        sources.append(dict(source))
    if (sum(source["reserved"] for source in sources) + value["sources_omitted"]
            > value["reserved"] or sum(source["dispatched"] for source in sources)
            > value["dispatched"]):
        return None
    if not value["sources_omitted"] and any(
        sum(source[key] for source in sources) != value[key] for key in ("reserved", "dispatched")
    ):
        return None
    out = {**value, "sources": sources}
    return out if len(json.dumps(out, ensure_ascii=False)) <= 2500 else None


def _project_request_usage(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    fields = {key: value[key] for key in ("reserved", "dispatched", "closed", "sources",
                                         "quota_authoritative", "count_basis", "usage_id")
              if key in value}
    if not isinstance(fields.get("sources"), list):
        return None
    fields["sources"] = list(fields["sources"])
    fields["sources_omitted"] = 0
    # Paid/local policies have no small global cap. Keep totals and the durable
    # selector when their breakdown exceeds the conversation metadata bound.
    while fields["sources"] and (len(fields["sources"]) > 64
                                  or len(json.dumps(fields, ensure_ascii=False)) > 2500):
        fields["sources"].pop()
        fields["sources_omitted"] += 1
    return normalize_request_usage(fields)


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    """Hashable historical observation; never model choice or access authority."""

    provider: str = ""
    model: str = ""
    model_status: str = ""
    provider_display: str = ""
    requested_model: str = ""
    usage: dict[str, Any] | None = field(default=None, compare=False, hash=False)


def normalize_execution_receipt(value: object) -> dict[str, Any] | None:
    """Return only consistent, bounded labels, without retaining caller objects."""
    if isinstance(value, ExecutionReceipt):
        value = {
            **({"provider": value.provider, "model": value.model,
                "model_status": value.model_status}
               if any((value.provider, value.model, value.model_status)) else {}),
            **{name: getattr(value, name) for name in _OPTIONAL_FIELDS if getattr(value, name)},
            **({"usage": value.usage} if value.usage is not None else {}),
        }
    if not isinstance(value, dict):
        return None
    usage = normalize_request_usage(value.get("usage")) if "usage" in value else None
    if "usage" in value and usage is None:
        return None
    if set(value) == {"usage"}:
        return {"usage": usage}
    if (not _REQUIRED_FIELDS <= set(value)
            or set(value) - _REQUIRED_FIELDS - set(_OPTIONAL_FIELDS) - {"usage"}):
        return None
    provider, model, status = value["provider"], value["model"], value["model_status"]
    if not isinstance(provider, str) or not provider or _label(provider, 400) != provider:
        return None
    if not isinstance(model, str) or (model and _label(model, 200) != model):
        return None
    if status != ("reported" if model else "unknown"):
        return None
    out = {"provider": provider, "model": model, "model_status": status}
    if usage is not None:
        out["usage"] = usage
    for name in _OPTIONAL_FIELDS:
        if name not in value:
            continue
        label = value[name]
        # An empty or malformed label is a REFUSAL, not a blank: the caller is
        # claiming a display name (or a request) it does not have, and the
        # renderer must fall back rather than print nothing beside its words.
        if not isinstance(label, str) or not label or _label(label, 200) != label:
            return None
        out[name] = label
    return out


@dataclass(slots=True)
class WriterExecutionReceipt:
    """Create once per reply; pass observe ONLY to that reply's writer call.

    The collector keeps the first completed response, not a last-call slot that
    learning or other requests could overwrite. It holds only safe labels, never
    prompts, response text, credentials, tool output or mutable provider objects.
    """

    _receipt: tuple[str, str, str, str] | None = field(default=None, init=False)
    _usage: dict[str, Any] | None = field(default=None, init=False)

    def observe(self, response: ProviderResponse) -> None:
        if not isinstance(response, ProviderResponse):
            return
        if self._usage is None:
            self._usage = _project_request_usage(response.request_receipt)
        if self._receipt is not None:
            return
        provider = _label(response.provider, 400)
        if not provider or response.degraded or response.failure_class is not None:
            return
        self._receipt = (
            provider,
            _label(response.reported_model, 200),
            # The owner's own name for the source, when the router resolved one.
            _label(getattr(response, "provider_display", ""), 200),
            # What the call asked for. Kept apart from `model`: a request is not
            # evidence of what answered, so it never turns `unknown` into
            # `reported` (a source that reports nothing still says so).
            _label(getattr(response, "requested_model", ""), 200),
        )

    def projection(self) -> dict[str, Any] | None:
        usage = normalize_request_usage(self._usage)
        if self._receipt is None:
            return {"usage": usage} if usage is not None else None
        provider, model, display, requested = self._receipt
        return {
            "provider": provider,
            "model": model,
            "model_status": "reported" if model else "unknown",
            **({"provider_display": display} if display else {}),
            **({"requested_model": requested} if requested else {}),
            **({"usage": usage} if usage is not None else {}),
        }
