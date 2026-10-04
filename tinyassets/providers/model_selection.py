"""Per-attempt model validation, separate from advisory ordering and preferences.

Only the serving validator supplies these facts after checking a current accepted
member. A model reference or catalogue object from the caller is never a grant.
No release names or provider wire shapes belong in this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinyassets.providers.discovery_contract import SourceContract
    from tinyassets.providers.discovery_protocols import DiscoveryProtocol
    from tinyassets.providers.native_model_selection import NativeSelection

from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.providers.model_policy import (
    Catalog,
    Charge,
    ModelPolicy,
    ModelRef,
    order_models,
)


@dataclass(frozen=True, slots=True)
class SelectedModel:
    provider: str
    model_id: str
    discovery_protocol: str
    cost_caps: tuple[tuple[str, int], ...]
    source_digest: str
    context_tokens: int
    supports_tools: bool = False
    execution_contract: SourceContract | DiscoveryProtocol | None = field(default=None, repr=False)

    def contract(self):
        if self.execution_contract is not None:
            return self.execution_contract
        from tinyassets.providers.discovery_protocols import discovery_protocol

        return discovery_protocol(self.discovery_protocol)

    def cost_upper_bound(self, output_tokens: int) -> int:
        """Conservative USD micros for this text-only request at accepted caps.

        Use the whole model context as an input upper bound, not a tokenizer
        guess. Captured source quantities also bound declared internal samples
        and overhead; legacy text arithmetic stays unchanged. Actual response
        cost remains unknown unless reported; this is reservation evidence.
        """
        from tinyassets.providers.discovery_contract import SourceContract

        if isinstance(self.execution_contract, SourceContract):
            return self.execution_contract.cost_upper_bound(
                self.cost_caps, self.context_tokens, output_tokens,
            )
        caps = dict(self.cost_caps)
        input_cost = (self.context_tokens * caps["input_million_tokens_usd"] + 999999) // 1000000
        output_cost = (output_tokens * caps["output_million_tokens_usd"] + 999999) // 1000000
        return input_cost + output_cost + caps["request_usd"]

    def affordable_output(
        self, remaining_cost: int, *, output_limit: int | None = None,
    ) -> int | None:
        from tinyassets.providers.discovery_contract import SourceContract

        if isinstance(self.execution_contract, SourceContract):
            if output_limit is None:
                raise ValueError("configured source requires a finite output limit")
            return self.execution_contract.affordable_output(
                self.cost_caps, self.context_tokens, output_limit, remaining_cost,
            )
        available = remaining_cost - self.cost_upper_bound(0)
        if available < 0:
            return 0
        price = dict(self.cost_caps)["output_million_tokens_usd"]
        return None if price == 0 else available * 1000000 // price


def prepare_selected_model(
    *,
    base_path: Path,
    owner_user_id: str,
    universe_id: str,
    provider: str,
    model_id: str,
    access: ModelAccess,
    needs_tools: bool = False,
    effort: str = "",
) -> tuple[SelectedModel | NativeSelection | None, Callable[[], None] | None]:
    """Prepare an accepted source; native defaults carry no HTTP model facts.

    This is not an independent authority entrypoint. The caller must validate the
    exact current member before and after this IO, under assignment admission.
    Native defaults return (None, None); current member and custody validation
    remains the caller's responsibility before and after this preparation.
    """
    from tinyassets.providers.discovery_snapshot import refresh_model_discovery
    from tinyassets.providers.native_model_selection import accepted_native_selection

    native = accepted_native_selection(provider, model_id, access)
    if native is not None:
        return native, None
    if _native_discovery_needed(provider, model_id, access):
        from tinyassets.providers.native_discovery import discover_native_models_sync

        snapshot = discover_native_models_sync(
            base_path=base_path, owner_user_id=owner_user_id,
            universe_id=universe_id, provider=provider,
        )
        return _validate_native_snapshot(
            snapshot, base_path, owner_user_id, universe_id, provider, model_id, access,
            effort,
        )
    if _native_default(provider, model_id, access):
        return None, None
    definition = _selection_definition(
        base_path, owner_user_id, universe_id, provider, model_id, access
    )
    snapshot = refresh_model_discovery(
        owner_user_id=owner_user_id,
        universe_id=universe_id,
        definition_id=definition.id,
    )
    return _validate_snapshot(
        definition, snapshot, provider, model_id, access, needs_tools=needs_tools,
    )


async def prepare_selected_model_async(
    *, base_path: Path, owner_user_id: str, universe_id: str,
    provider: str, model_id: str, access: ModelAccess,
    needs_tools: bool = False, effort: str = "",
) -> tuple[SelectedModel | NativeSelection | None, Callable[[], None] | None]:
    """Refresh without blocking ingress; the caller re-fences authority afterward.

    No assignment lock or SQLite transaction may span this await. The snapshot
    is advisory data, not permission, and cancelled callers never reach launch.
    """
    from tinyassets.providers.discovery_snapshot import refresh_model_discovery_async
    from tinyassets.providers.native_model_selection import accepted_native_selection

    native = accepted_native_selection(provider, model_id, access)
    if native is not None:
        return native, None
    if _native_discovery_needed(provider, model_id, access):
        from tinyassets.providers.native_discovery import discover_native_models

        snapshot = await discover_native_models(
            base_path=base_path, owner_user_id=owner_user_id,
            universe_id=universe_id, provider=provider,
        )
        return _validate_native_snapshot(
            snapshot, base_path, owner_user_id, universe_id, provider, model_id, access,
            effort,
        )
    if _native_default(provider, model_id, access):
        return None, None
    definition = _selection_definition(
        base_path, owner_user_id, universe_id, provider, model_id, access
    )
    snapshot = await refresh_model_discovery_async(
        owner_user_id=owner_user_id,
        universe_id=universe_id,
        definition_id=definition.id,
    )
    return _validate_snapshot(
        definition, snapshot, provider, model_id, access, needs_tools=needs_tools,
    )


def saved_effort_level(base_path, owner_user_id, universe_id, ref):
    """The owner's stored effort level for exactly this model, or empty.

    READ from storage at launch rather than accepted as a parameter: effort
    changes what a turn costs, so letting a caller supply it would be a way to
    raise a served turn's effort without the owner choosing it -- the same
    reason the model id comes from validated authority and not ModelConfig.

    An unreadable preference row is NOT swallowed into "no effort": it raises,
    and the caller holds the launch, because running at a different level than
    the owner saved while reporting success is the silent failure to avoid.
    """
    from tinyassets.storage.model_preferences import ModelPreferenceStore

    policy = ModelPreferenceStore(base_path).get(owner_user_id, universe_id).policy
    return "" if policy is None else policy.effort_for(ref)


def _native_discovery_needed(provider, model_id, access):
    from tinyassets.provider_serving_binding import _PROVIDER_SERVICE

    return (provider in _PROVIDER_SERVICE and bool(model_id)
            and type(access) is ModelAccess and access.model_scope == "discovered")


def _validate_native_snapshot(snapshot, base, owner, uid, provider, model_id, access,
                              effort=""):
    from tinyassets.providers.native_discovery import NativeDiscoverySnapshot

    if type(snapshot) is not NativeDiscoverySnapshot:
        raise PermissionError("native model requires fresh owned enumeration")
    snapshot.assert_current()
    selected = snapshot.select(
        provider=provider, owner=owner, universe=Path(base) / uid, custody=snapshot.custody,
        model_id=model_id, access=access, effort=effort,
    )
    return selected, snapshot.assert_current


def _native_default(provider, model_id, access):
    """Native defaults need member custody, not fabricated HTTP model facts.

    The caller still validates the exact accepted member and snapshots its owned
    credential. This predicate grants nothing and performs no account discovery.
    Explicit native model IDs require their executor's discovery/selection path.
    """
    from tinyassets.provider_serving_binding import _PROVIDER_SERVICE

    if not isinstance(provider, str) or provider not in _PROVIDER_SERVICE:
        return False
    if (
        type(model_id) is not str or model_id != "" or type(access) is not ModelAccess
        or (access.model_scope == "explicit" and "" not in access.model_ids)
    ):
        raise PermissionError("native model is outside the supported default selection scope")
    return True


def _selection_definition(base_path, owner_user_id, universe_id, provider, model_id, access):
    from tinyassets.providers.definition import get_definition
    from tinyassets.storage import data_dir

    if Path(base_path).resolve() != data_dir().resolve():
        raise PermissionError("model discovery requires the current storage root")
    if (
        not isinstance(model_id, str)
        or not model_id
        or len(model_id) > 200
        or not model_id.isprintable()
        or model_id != model_id.strip()
        or access.model_scope == "legacy"
        or (access.model_scope == "explicit" and model_id not in access.model_ids)
    ):
        raise PermissionError("model is outside the accepted selection scope")
    if not isinstance(provider, str) or not provider.startswith("api_key_http:"):
        raise PermissionError("dynamic selection is not supported by this executor yet")
    definition_id = provider.removeprefix("api_key_http:")
    definition = get_definition(universe_id, definition_id)
    if definition is None or definition.owner_user_id != owner_user_id:
        raise PermissionError("selected provider definition is unavailable")
    return definition


def _accepted_caps(contract, access):
    """The owner's accepted price ceilings for this source's components."""
    components = contract.price_components
    caps = (
        tuple((name, 0) for name in sorted(components))
        if access.cost_caps is None
        else access.cost_caps
    )
    if {name for name, _ in caps} != components:
        raise PermissionError("selected executor cannot enforce the accepted price components")
    return caps


def _eligible_order(definition, snapshot, *, access, needs_tools):
    """Automatic eligibility over an already-fetched snapshot: fresh capability
    AND permitted pricing. An explicit preference cannot use the advisory
    kernel's stale-list allowance.

    Pure data -- no IO, no authority -- so it is safe to call inside the
    reservation transaction. Shared with :func:`eligible_model_ids` so that
    "may this model run" and "which model when none was named" cannot answer
    differently; a second ordering is how a run came to pin a model its own
    account could not run (see `eligible_model_ids`).
    """
    contract = snapshot.contract()
    caps = _accepted_caps(contract, access)
    if type(needs_tools) is not bool:
        raise PermissionError("invalid required model capability")
    order = order_models(
        Catalog(definition.owner_user_id, definition.universe_id, (snapshot.models,)),
        ModelPolicy(
            generation=0,
            mode="automatic",
            fallbacks=(),
            cost_caps=tuple(Charge(name, amount, True) for name, amount in caps),
        ),
        replace(contract.text_interaction, needs_tools=needs_tools),
        owner_id=definition.owner_user_id,
        universe_id=definition.universe_id,
    )
    return contract, caps, order


def eligible_model_ids(definition, snapshot, *, access, needs_tools=False):
    """This source's currently runnable models, best first.

    What an omitted workflow model resolves to. It used to resolve to
    ``snapshot.default_model_id or definition.model`` -- the source's DECLARED
    default -- which is a claim about registration time, not about now. Live
    2026-09-30 (universe ``u-01ky3zh1arr8qth8jee7zx63pq``): the declared
    ``inclusionai/ling-3.0-flash-vl:free`` had left the account's 632-model
    catalogue and OpenRouter's user-models protocol reports no default, so every
    unpinned node pinned a model that no longer existed while the owner's chat
    turn ran fine on the fresh order.
    """
    return tuple(
        candidate.ref.model_id
        for candidate in _eligible_order(
            definition, snapshot, access=access, needs_tools=needs_tools,
        )[2].candidates
    )


def _validate_snapshot(definition, snapshot, provider, model_id, access, *, needs_tools=False):
    from tinyassets.providers.discovery_snapshot import assert_discovery_snapshot_current

    owner_user_id, universe_id = definition.owner_user_id, definition.universe_id
    if (
        snapshot.owner_id != owner_user_id or snapshot.universe_id != universe_id
        or snapshot.provider != provider
    ):
        raise PermissionError("discovery snapshot does not match selected provider")
    from tinyassets.providers.wire_dialects import same_dialect

    if not same_dialect(definition.protocol, snapshot.contract().inference_protocol):
        raise PermissionError("discovery and inference protocols do not match")
    contract, caps, order = _eligible_order(
        definition, snapshot, access=access, needs_tools=needs_tools,
    )
    contract.constrain_inference({"model": model_id, "messages": []}, caps)
    ref = ModelRef(provider, model_id)
    if ref not in {candidate.ref for candidate in order.candidates}:
        raise PermissionError("selected model lacks fresh capability or permitted pricing")
    assert_discovery_snapshot_current(snapshot)
    model = next(model for model in snapshot.models.models if model.model_id == model_id)
    selected = SelectedModel(
        provider,
        model_id,
        snapshot.models.provider_scope,
        caps,
        snapshot.source_digest,
        model.context_tokens,
        needs_tools,
        contract,
    )
    return selected, lambda: assert_discovery_snapshot_current(snapshot)
