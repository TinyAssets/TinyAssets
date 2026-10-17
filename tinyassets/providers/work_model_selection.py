"""Prepare workflow discovery outside admission; authorize it at reservation.

Only owned connection metadata is fetched here. This is advisory preparation,
not a grant: the store reconstructs current member/model authority before arming.
"""

from pathlib import Path


def prepare_work_model_snapshot(*, base_path, universe_id, owner_user_id,
                                provider, model_id=""):
    """Discover one currently connected source before the reservation transaction."""
    from tinyassets.providers.served_model_plan import connected_sources

    base = Path(base_path)
    universe = base / universe_id
    if provider not in connected_sources(base, universe, owner_user_id):
        raise PermissionError(f"source {provider!r} is not connected for this owner")
    from tinyassets.providers.discovery_snapshot import refresh_model_discovery

    return refresh_model_discovery(
        owner_user_id=owner_user_id, universe_id=universe_id,
        definition_id=provider.removeprefix("api_key_http:"),
    )




def selected_work_model(selection):
    """Reconstruct data covered by the carrier seal; this grants nothing alone."""
    from tinyassets.providers.discovery_contract import SourceContract
    from tinyassets.providers.discovery_protocols import discovery_protocol
    from tinyassets.providers.model_selection import SelectedModel

    if selection is None:
        return None
    evidence = selection.model_evidence()
    if evidence is None:
        return None
    contract = evidence["execution_contract"]
    if contract["kind"] == "declared":
        from tinyassets.providers.declared_models import contract_from_evidence

        compiled = contract_from_evidence(contract["value"])
    else:
        compiled = (SourceContract.compile(contract["value"])
                    if contract["kind"] == "configured"
                    else discovery_protocol(contract["value"]))
    return SelectedModel(
        selection.provider, selection.model_id, evidence["discovery_protocol"],
        tuple(sorted(evidence["cost_caps"].items())), evidence["source_digest"],
        evidence["context_tokens"], evidence["supports_tools"], compiled,
    )


def selection_with_model(selection, model, snapshot):
    """Persist the exact validated model and discovery interval, without callbacks."""
    import json
    from dataclasses import replace

    from tinyassets.providers.declared_models import DeclaredModelContract
    from tinyassets.providers.discovery_contract import SourceContract

    contract = model.contract()
    if isinstance(contract, DeclaredModelContract):
        persisted = {"kind": "declared", "value": contract.descriptor()}
    elif isinstance(contract, SourceContract):
        persisted = {"kind": "configured", "value": json.loads(contract.descriptor_json)}
    else:
        persisted = {"kind": "installed", "value": model.discovery_protocol}
    evidence = {
        "discovery_protocol": model.discovery_protocol,
        "source_digest": model.source_digest,
        "context_tokens": model.context_tokens,
        "supports_tools": model.supports_tools,
        "cost_caps": dict(model.cost_caps),
        "execution_contract": persisted,
        "observed_at": snapshot.observed_at.isoformat().replace("+00:00", "Z"),
        "completed_at": snapshot.completed_at.isoformat().replace("+00:00", "Z"),
    }
    return replace(
        selection, model_id=model.model_id, executor_id=contract.inference_protocol,
        model_evidence_json=json.dumps(
            evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ),
    )


def bound_work_model_tokens(selection, max_tokens, max_cost_microunits):
    """Reserve a finite affordable output before arming a selected HTTP call."""
    model = selected_work_model(selection)
    if model is None:
        return max_tokens
    affordable = model.affordable_output(max_cost_microunits, output_limit=max_tokens)
    limit = max_tokens if affordable is None else min(max_tokens, affordable)
    if limit < 1:
        raise PermissionError("selected model exceeds the workflow cost allowance")
    return limit
