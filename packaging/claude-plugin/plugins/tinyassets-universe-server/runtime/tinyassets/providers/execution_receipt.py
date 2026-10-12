"""Request-owned answer telemetry, never routing authority or shared state."""

import json
from dataclasses import dataclass, field
from typing import Any

from tinyassets.providers.base import ProviderResponse
from tinyassets.providers.model_policy import ModelRef


def _label(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= maximum or not value.isprintable():
        return ""
    return value.strip()


#: The three fields every receipt has carried. ``provider_display`` and
#: ``requested_model`` are optional and ABSENT when nothing resolved, so a row
#: stored before they existed still normalizes and renders no blank label.
_REQUIRED_FIELDS = {"provider", "model", "model_status"}
_OPTIONAL_FIELDS = (
    "provider_display", "requested_model", "configured_model", "resolved_model",
    "reported_model", "family", "source_kind", "execution", "outcome",
    "usage_provenance", "usage_id", "parent_usage_id", "learning_failure",
)


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
    configured_model: str = ""
    resolved_model: str = ""
    reported_model: str = ""
    family: str = ""
    source_kind: str = ""
    execution: str = ""
    outcome: str = ""
    usage_provenance: str = ""
    usage_id: str = ""
    parent_usage_id: str = ""
    learning_failure: str = ""
    fallback_used: int | None = None
    attempt: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_microunits: int | None = None
    usage: dict[str, Any] | None = field(default=None, compare=False, hash=False)


def normalize_execution_receipt(value: object) -> dict[str, Any] | None:
    """Return only consistent, bounded labels, without retaining caller objects."""
    if isinstance(value, ExecutionReceipt):
        value = {
            **({"provider": value.provider, "model": value.model,
                "model_status": value.model_status}
               if any((value.provider, value.model, value.model_status)) else {}),
            **{name: getattr(value, name) for name in _OPTIONAL_FIELDS if getattr(value, name)},
            **{name: getattr(value, name) for name in (
                "fallback_used", "attempt", "input_tokens", "output_tokens", "cost_microunits",
            ) if getattr(value, name) is not None},
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
            or set(value) - _REQUIRED_FIELDS - set(_OPTIONAL_FIELDS) - {
                "usage", "fallback_used", "attempt", "input_tokens", "output_tokens",
                "cost_microunits",
            }):
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
    for name in ("fallback_used", "attempt", "input_tokens", "output_tokens", "cost_microunits"):
        if name not in value:
            continue
        count = value[name]
        if type(count) is not int or count < 0:
            return None
        out[name] = count
    return out


def receipt_from_response(
    response: ProviderResponse,
    *,
    requested: ModelRef | None = None,
) -> ExecutionReceipt:
    """Detach the completed call's evidence without inferring unknown usage as zero."""
    reported = _label(response.reported_model, 200)
    requested_id = requested.model_id if requested is not None else response.requested_model
    usage_id = (response.request_receipt or {}).get("usage_id", "")
    return ExecutionReceipt(
        provider=_label(response.provider, 400),
        model=reported,
        model_status="reported" if reported else "unknown",
        provider_display=_label(response.provider_display, 200),
        requested_model=_label(requested_id, 200),
        configured_model=_label(response.configured_model, 200),
        resolved_model=_label(response.model, 200),
        reported_model=reported,
        family=_label(response.family, 200),
        execution=("native_agent" if response.native_evidence is not None
                   else "model_response"),
        outcome="failed" if response.failure_class or response.degraded else "succeeded",
        usage_provenance=("provider_reported" if response.input_tokens is not None
                          or response.output_tokens is not None else "unknown"),
        usage_id=_label(usage_id, 32),
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_microunits=response.cost_microunits,
        usage=_project_request_usage(response.request_receipt),
    )
