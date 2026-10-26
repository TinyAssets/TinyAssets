"""W1/S29: where a model source's work may run is source data, resolved once.

A source record's ``execution`` block names its allowed placements, how it
bills and whom it may serve. ``resolve_placement`` is the one resolver, at
model-plan time; dispatch re-checks the executor it reaches against the same
block. Nothing changes placement silently: a source with no available
placement is refused with each placement tried and why.
"""
from __future__ import annotations

PLACEMENTS = ("vendor", "thin_loop", "device", "hosted_box")
BILLING = ("plan", "free", "metered")
SERVES = ("owner_only", "may_serve_others")

#: Detected sources (no declared record) run on the thin loop and serve only their owner.
DETECTED = {"placement": ["thin_loop"], "billing": ["metered"], "serves": "owner_only"}

#: Placements with no runner yet. They are refused, never substituted.
NOT_BUILT = {
    "device": "the device runner is not built yet (S7)",
    "vendor": "vendor-hosted execution is not built yet",
}

# The executor each runnable placement reaches today.
_EXECUTORS = {"thin_loop": "api_key_http", "hosted_box": "subscription_cli"}


def _placement(executor):
    return next((p for p, method in _EXECUTORS.items() if method == executor), None)


class PlacementRefused(PermissionError):
    """No allowed placement is available; names each one tried and why."""

    def __init__(self, source: str, tried: dict[str, str]):
        self.source, self.tried = source, dict(tried)
        reasons = "; ".join(f"{placement}: {why}" for placement, why in tried.items())
        super().__init__(f"model source {source} has no available placement ({reasons})")


def validate(execution, *, cli: bool) -> dict:
    """The block's own rules; raises ValueError for a malformed declaration."""
    if (not isinstance(execution, dict)
            or set(execution) != {"placement", "billing", "serves"}):
        raise ValueError("source execution requires placement, billing and serves")
    placement, billing, serves = (execution["placement"], execution["billing"],
                                  execution["serves"])
    for values, allowed in ((placement, PLACEMENTS), (billing, BILLING)):
        if (not isinstance(values, list) or not values or len(set(values)) != len(values)
                or any(value not in allowed for value in values)):
            raise ValueError("invalid source execution values")
    if serves not in SERVES:
        raise ValueError("invalid source execution serves")
    if cli and not set(placement) <= {"hosted_box", "device"}:
        raise ValueError("a CLI source may run only in a hosted box or on a device")
    if ("device" in placement or "plan" in billing) and serves != "owner_only":
        raise ValueError("device and plan sources serve only their owner")
    return execution


def resolve_placement(source: str, execution: dict, facts: dict[str, str | None]) -> str:
    """The first allowed placement that is available, else a loud refusal.

    ``facts`` maps a placement to ``None`` when it is available now, or to why
    it is not. A placement missing from ``facts`` is unavailable here.
    """
    tried = {}
    for placement in execution["placement"]:
        why = NOT_BUILT.get(placement) or facts.get(placement, "not available here")
        if why is None:
            return placement
        tried[placement] = why
    raise PlacementRefused(source, tried)


def require_dispatch(execution: dict, executor: str) -> str:
    """Dispatch reaches only an executor the source's placements allow."""
    placement = _placement(executor)
    if placement is None or placement not in execution["placement"]:
        raise PermissionError("model source cannot run on this executor")
    return placement


def require_serves(execution: dict, *, owner: str, caller: str) -> None:
    """An owner-only source serves its owner's agents and no one else."""
    if execution["serves"] == "owner_only" and caller != owner:
        raise PermissionError("model source serves only its owner")


def execution_for(definition) -> dict:
    """The execution block behind an owner's registered model definition.

    A CLI definition names its declared record. An HTTP definition names only
    its connection grant, so it carries the detected default until a record
    is linked to the grant.
    """
    if _placement(definition.access_method) == "hosted_box":
        from tinyassets.owner_runtime import current
        from tinyassets.providers.source_record import records

        owner = current()  # An owner's own record overrides the built-in one.
        record = records(owner.root if owner is not None else None).get(definition.ref)
        if record is None:
            raise PermissionError("model source has no declared record")
        return record.execution
    return DETECTED
