"""Authenticated structured delivery through the existing graph action boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from tinyassets import delivery_runtime, engine_admissions, runs
from tinyassets.api import receiver_links as management
from tinyassets.branches import BranchDefinition
from tinyassets.storage import deliveries, receiver_links

WRITE_ACTIONS = frozenset({
    "create_receiver", "update_receiver", "revoke_receiver", "connect_output",
    "disconnect_output", "deliver_output", "answer_delivery",
})
READ_ACTIONS = frozenset({
    "inspect_receiver", "discover_receivers", "list_output_links", "get_delivery",
    "list_deliveries",
})


@dataclass(frozen=True)
class NodeDeliverySource:
    """Parent-only compiled placement and persisted run authority; never RPC data."""

    owner_user_id: str
    universe_id: str
    actor: str
    run_id: str
    branch_def_id: str
    node_id: str
    output_keys: tuple[str, ...]


def node_occurrence_id(source, link_id, occurrence_id):
    """Reuse canonical effect identity; content belongs only in the replay digest."""
    from tinyassets.idempotency import derive_effect_key

    receiver_links._name(occurrence_id)
    receiver_links._name(link_id)
    return derive_effect_key(
        goal_id=deliveries._exact_json([
            "node-delivery-v1", source.owner_user_id, source.universe_id, link_id,
        ]),
        schedule_period=source.run_id,
        item_fingerprint=hashlib.sha256(deliveries._exact_json({
            "placement_id": source.node_id, "occurrence_id": occurrence_id,
        }).encode("utf-8")).hexdigest(),
    )


def deliver_node_output(base, *, source, link_id, occurrence_id, outputs, should_cancel):
    """Trusted parent entry. No request identity or caller authority selectors."""
    if should_cancel():
        raise ValueError("delivery_source_cancelled")
    if not isinstance(source, NodeDeliverySource) or not all((
        source.owner_user_id, source.universe_id, source.actor, source.run_id,
        source.branch_def_id, source.node_id,
    )):
        raise PermissionError("delivery_source_unavailable")
    return _accept_output(
        base, principal=source.owner_user_id, universe_id=source.universe_id,
        link_id=link_id, occurrence_id=node_occurrence_id(source, link_id, occurrence_id),
        outputs=outputs, source=source, should_cancel=should_cancel,
    )


def _check_source(conn, source, link):
    row = conn.execute("SELECT * FROM runs WHERE run_id=?", (source.run_id,)).fetchone()
    if (row is None or row["status"] != "running"
            or row["owner_user_id"] != source.owner_user_id
            or row["actor"] != source.actor or row["queue_universe_id"] != source.universe_id
            or row["branch_def_id"] != source.branch_def_id
            or link["branch_def_id"] != source.branch_def_id or link["node_id"] != source.node_id
            or set(json.loads(link["mapping_json"])) - set(source.output_keys)):
        raise PermissionError("delivery_source_unavailable")


FILE_KINDS = frozenset({"file", "file_bundle"})


def _declared_files(branch):
    """File inputs the RECEIVER's own branch declares -- the only consent there is.

    A receiver contract reports its ``state_schema`` type, which ``io_manifest``
    requires to be dict/list for a file input, so the contract type alone can
    never name a file field. The manifest declaration is authoritative; the
    contract is only cross-checked against it below.
    """
    manifest = branch.to_dict().get("io_manifest") or {}
    inputs = manifest.get("inputs") or []
    if not any(isinstance(item, dict) and item.get("io_type") in FILE_KINDS
               for item in inputs):
        return {}
    from tinyassets.run_file_binding import file_declarations

    return {item.name: item.io_type for item in file_declarations(branch).inputs if item.is_file}


def _file_fields(receiver, values, branch):
    """Declared file fields actually present, in stable name order."""
    declared = _declared_files(branch)
    present = {}
    undeclared = []
    for field in json.loads(receiver["contract_json"]):
        name = field["name"]
        if field["type"] in FILE_KINDS and name not in declared:
            undeclared.append(name)
        if name not in declared:
            continue
        if name not in values:
            if field["required"]:
                raise ValueError("receiver input is required")
            continue
        present[name] = declared[name]
    if undeclared:
        raise ValueError(
            "receiver branch declares no file input for " + ", ".join(sorted(undeclared))
        )
    return present


def _declared_file_limits(branch, values, file_fields):
    """Limits come from the RECEIVER's own branch declaration, never the contract.

    ``declared_file_inputs`` already validates min/max count, max bytes and media
    types against ``io_manifest``; a contract file field the receiver's branch does
    not declare has no consent behind it and is refused. No per-user quota exists
    or is introduced: retained-byte ceilings stay the operational subsystem ones.
    """
    from tinyassets.authoring.models import ManifestViolation
    from tinyassets.run_file_binding import file_declarations
    from tinyassets.run_file_contract import declared_file_inputs

    try:
        resolved = declared_file_inputs(file_declarations(branch), values, branch.state_schema)
    except ManifestViolation as exc:
        raise ValueError(f"receiver_file_declaration_refused: {exc}") from exc
    missing = sorted(set(file_fields) - set(resolved))
    if missing:
        raise ValueError("receiver branch declares no file input for " + ", ".join(missing))
    return resolved


def _reject_undeclared_references(values, file_fields):
    """Mapped positions outside a receiver-declared file input stay fail-closed.

    The receiver's own declaration is the only consent there is, so a custody
    reference may occupy exactly the positions it declares -- never an ordinary
    contract field, and never nested inside one, however valid the envelope. A
    declared file position that is present but refused by ``_file_fields`` never
    reaches here. This is the same default dispatch already applies to every
    non-provenance position; applied here it runs before any reservation,
    allocation, byte copy or acceptance, where a later refusal is too late.
    """
    delivery_runtime.reject_file_references(
        {name: value for name, value in values.items() if name not in file_fields}
    )


def _refuse_reserved_contract_fields(receiver):
    """A reserved attribution name may never be an ADVERTISED field, at any age.

    The save-time guard in ``api/receiver_links.py`` only covers receivers created
    after it shipped, which left the "a sender can never land a value here"
    invariant contingent on validation rather than structural. A receiver predating
    it could advertise a reserved name as a FILE input; injection would then write
    the authenticated principal and ``storage/deliveries.py`` ``_run_inputs`` would
    replace it with the sender's own file reference (cross-family review,
    2026-09-26). Checked against the ADMITTED contract on every acceptance, so a
    receiver in that state cannot receive until its owner renames the field --
    loudly refused, never silently accepted with forged provenance.
    """
    advertised = {field["name"] for field in json.loads(receiver["contract_json"])}
    collide = sorted(advertised & set(receiver_links.SENDER_ATTRIBUTION_FIELDS))
    if collide:
        raise ValueError(
            "receiver advertises platform-supplied sender attribution as an input; "
            "its owner must rename: " + ", ".join(collide)
        )


def _replayed_inputs(prior, link, outputs):
    """Validate a retry against the STORED acceptance and carry it forward verbatim.

    The comparison covers SENDER content only. Platform-supplied attribution is not
    something the sender sent, so recomputing it in order to compare -- this
    change's first shape -- both added fragility and broke retries of occurrences
    accepted before attribution existed, since their stored inputs have no such key.
    Returning the stored dict unchanged is what keeps the replay digest identical for
    a pre-upgrade row and a post-upgrade one alike.

    A changed payload still conflicts: here on the mapped comparison, and again in
    ``accept_in_transaction``, whose digest also covers the raw request payload.
    """
    stored = json.loads(prior["inputs_json"])
    sent = {target: outputs[source] for source, target
            in json.loads(link["mapping_json"]).items() if source in outputs}
    reserved = set(receiver_links.SENDER_ATTRIBUTION_FIELDS)
    if sent != {name: value for name, value in stored.items() if name not in reserved}:
        raise deliveries.OccurrenceConflict()
    return stored


def _sender_attribution(snapshot_json, link, values):
    """Fill the reserved attribution fields the RECEIVER's own snapshot declares.

    Source is the stored link row -- ``owner_id`` is the sending principal and
    ``universe_id`` the universe the send is made from -- never the request
    payload, so a sender cannot name itself. Only declared fields are filled:
    ``tinyassets/runs.py`` feeds a run row's stored inputs back into
    ``_invoke_prepared_branch`` on the waiting-run resume path, so an undeclared
    key here would reach a graph whose state schema has no channel for it.

    A receiver that declares neither field is unchanged. Attribution is still
    unconditional where it is authoritative: ``graph_deliveries`` records the
    sender on every acceptance, and the receiver's side of the receipt shows it.

    ``snapshot_json`` is passed rather than read off a receiver row because WHICH
    snapshot governs differs by path: a first acceptance uses the receiver's
    current pinned snapshot, and a replay must use the ADMITTED one. Both are
    reproducible from stored state, which is what keeps a retry byte-identical --
    these values land in ``inputs_json`` and therefore in the replay digest.
    """
    branch = BranchDefinition.from_dict(json.loads(snapshot_json))
    declared = {
        field["name"]
        for field in branch.state_schema
        if isinstance(field, dict) and isinstance(field.get("name"), str)
    }
    attribution = dict(zip(
        receiver_links.SENDER_ATTRIBUTION_FIELDS, (link["owner_id"], link["universe_id"]),
    ))
    return {
        **values,
        **{name: value for name, value in attribution.items() if name in declared},
    }


def _structured_inputs(receiver, link, outputs, *, allow_files=False):
    _refuse_reserved_contract_fields(receiver)
    if not isinstance(outputs, dict):
        raise ValueError("outputs must be an object")
    delivery_runtime.validate_output_envelopes(outputs, allow_file_references=allow_files)
    deliveries._exact_json(outputs)
    mapping = json.loads(link["mapping_json"])
    if set(mapping) - set(outputs):
        raise ValueError("outputs are missing mapped source fields")
    values = {target: outputs[source] for source, target in mapping.items()}
    kinds = {"str": str, "int": int, "float": (float, int), "bool": bool,
             "list": list, "dict": dict}
    branch = BranchDefinition.from_dict(json.loads(receiver["snapshot_json"]))
    file_fields = _file_fields(receiver, values, branch)
    if file_fields and not allow_files:
        raise ValueError("delivery_file_transfer_not_implemented")
    _reject_undeclared_references(values, file_fields)
    for field in json.loads(receiver["contract_json"]):
        kind = field["type"]
        if kind in FILE_KINDS or field["name"] in file_fields:
            continue
        if field["name"] not in values:
            if field["required"]:
                raise ValueError("receiver input is required")
            continue
        value = values[field["name"]]
        if kind != "any" and (
            kind not in kinds or not isinstance(value, kinds[kind])
            or (kind in {"int", "float"} and isinstance(value, bool))
        ):
            raise ValueError("receiver input type mismatch")
    if file_fields:
        _declared_file_limits(branch, values, file_fields)
    # Before preflight, so a receiver may declare an attribution field REQUIRED and
    # have it satisfied by the platform rather than refused as a missing input.
    values = _sender_attribution(receiver["snapshot_json"], link, values)
    runs.preflight_required_inputs(branch, values)
    return values


def deliver_output(*, universe_id, link_id, occurrence_id, outputs):
    principal = management._principal(write=True)
    base = management._base()
    return _accept_output(base, principal=principal, universe_id=universe_id,
                          link_id=link_id, occurrence_id=occurrence_id, outputs=outputs)


def _envelope_records(values, file_fields):
    """Flatten declared file positions into stable (field, ordinal) order."""
    records = []
    for field in sorted(file_fields):
        value = values[field]
        items = value if file_fields[field] == "file_bundle" else [value]
        if not isinstance(items, list) or not items:
            raise ValueError("receiver file input type mismatch")
        for ordinal, reference in enumerate(items):
            if not delivery_runtime.is_file_reference(reference):
                raise ValueError("receiver file input type mismatch")
            records.append({"field_name": field, "ordinal": ordinal, "reference": reference})
    return records


def _accepted_transfer(conn, delivery_id):
    """An already-accepted occurrence keeps its custody rows; no second copy."""
    rows = conn.execute(
        "SELECT * FROM graph_delivery_files WHERE delivery_id=? ORDER BY field_name, ordinal",
        (delivery_id,),
    ).fetchall()
    if not rows:
        return None
    return {"replay": True, "records": [
        {"field_name": row["field_name"], "ordinal": row["ordinal"],
         "sender_file_id": row["sender_file_id"], "receiver_file_id": row["receiver_file_id"],
         "sha256": row["sha256"], "size_bytes": row["size_bytes"]}
        for row in rows
    ]}


def _transfer_files(base, *, principal, universe_id, link_id, occurrence_id, outputs, source,
                    should_cancel):
    """Phase A (read-only) then Phase B (copy), both ABOVE the acceptance fences.

    Acceptance holds the platform and runs writers together; a copy underneath
    them would open a second writer on both from the same thread. Copying first
    also means the receiver already owns its bytes before it is told yes, so a
    sender release right after acceptance cannot orphan an accepted delivery.
    """
    from tinyassets import run_file_crossowner

    with (
        management._owner_authority(base, universe_id, principal),
        deliveries.transaction(base) as conn,
    ):
        link = dict(receiver_links._owned_link(conn, link_id, principal, universe_id))
        # Same rule as in `_accept_output`, enforced on this path too because the
        # byte copy runs ABOVE every acceptance fence: checking only there would
        # copy a refused sender's files into the intake owner's custody first.
        # One definition (`patch_intake.require_send_consent`), two paths -- the
        # shape `_enforce_sender_rate_limit` already uses for the same reason.
        _require_patch_intake_consent(universe_id, link["receiver_id"])
        _check_source(conn, source, link)
        receiver = dict(conn.execute(
            "SELECT * FROM graph_receivers WHERE receiver_id=?", (link["receiver_id"],),
        ).fetchone())
        mapping = json.loads(link["mapping_json"])
        prior = conn.execute(
            "SELECT * FROM graph_deliveries WHERE sender_id=? AND sender_universe_id=? "
            "AND link_id=? AND occurrence_id=?",
            (principal, universe_id, link_id, occurrence_id),
        ).fetchone()
        if prior is not None:
            # Mirrors the authoritative replay branch structure so a changed replay
            # is refused before any reservation, allocation or byte is moved.
            _replayed_inputs(prior, link, outputs)
            return _accepted_transfer(conn, prior["delivery_id"])
        _, resolved = receiver_links.resolve_link_in_transaction(
            conn, link_id=link_id, owner_id=principal, universe_id=universe_id,
        )
        receiver = dict(resolved)
        _refuse_reserved_contract_fields(receiver)
        # The per-sender bound BEFORE any byte is copied. This runs under the same
        # two writers acceptance holds, so a sequential sender past its limit copies
        # nothing at all; concurrent senders are bounded by requests already in
        # flight rather than by the window, because the copy itself happens after
        # this transaction closes. A capacity reservation spanning the copy would
        # close that remainder and is not attempted here.
        _enforce_sender_rate_limit(conn, receiver, sender_id=principal)
        values = {target: outputs[field] for field, target in mapping.items()}
        branch = BranchDefinition.from_dict(json.loads(receiver["snapshot_json"]))
        file_fields = _file_fields(receiver, values, branch)
        _reject_undeclared_references(values, file_fields)
        if not file_fields:
            return None
        _declared_file_limits(branch, values, file_fields)
        records = _envelope_records(values, file_fields)
        # Read-only: every envelope must resolve under the trusted source run.
        for record in records:
            run_file_crossowner.resolve_bound_source(conn, source, record["reference"])
    try:
        copies = run_file_crossowner.copy_owned_custody_file(
            base, source=source, receiver_owner_id=receiver["owner_id"],
            receiver_universe_id=receiver["universe_id"], link_id=link_id,
            occurrence_id=occurrence_id,
            references=[record["reference"] for record in records],
            should_cancel=lambda: bool(should_cancel()),
        )
    except run_file_crossowner.store.FileCustodyRefused as exc:
        # The operation id is keyed on the occurrence alone, so a replay citing
        # different sender files re-enters the SAME operation with a different
        # request digest and conflicts before capacity, metadata or reservation.
        if str(exc) == "file_operation_conflict":
            raise deliveries.OccurrenceConflict() from exc
        raise
    if len(copies) != len(records):
        raise RuntimeError("cross-owner custody copy returned an unexpected bundle")
    return {"replay": False, "kinds": dict(file_fields), "records": [
        {"field_name": record["field_name"], "ordinal": record["ordinal"],
         "sender_file_id": record["reference"]["file_id"],
         "receiver_file_id": copy["file_id"], "sha256": copy["sha256"],
         "size_bytes": copy["size_bytes"], "reference": copy,
         # Kept so final acceptance can freshly resolve the SENDER envelope in
         # its own transaction; never persisted and never shown to the receiver.
         "source_reference": record["reference"]}
        for record, copy in zip(records, copies)
    ]}


def _enforce_sender_rate_limit(conn, receiver, *, sender_id):
    """Apply the OWNER's own per-sender policy, if they set one.

    Off unless the owner chose a number: the platform imposes no default and no
    ceiling on traffic through someone else's receiver (founder, 2026-09-30 --
    account limits are cloud storage and concurrent agent seats). The old
    justification was that a stranger could spend the owner's run admission
    budget; there is no such budget now, and a delivered run queues for a seat.

    When the owner HAS set one, it runs on first acceptance only, inside the
    acceptance transaction, and BEFORE any further admission, so a refused sender
    does no work on the receiving side. A retry of an already-accepted occurrence
    never reaches here, so replay is neither charged nor refused. One code path
    for every account -- an enumerated sender gets the same rule as a stranger.
    """
    limit = receiver["sender_rate_limit"]
    if not limit:  # NO_SENDER_RATE_LIMIT: the owner set no policy
        return
    accepted = deliveries.sender_window_count(
        conn, receiver_id=receiver["receiver_id"], sender_id=sender_id,
        window_seconds=deliveries.SENDER_RATE_WINDOW_SECONDS,
    )
    if accepted >= limit:
        raise ValueError(
            "receiver_sender_rate_limit_exceeded: this receiver's owner accepts "
            f"{limit} deliveries per sender per "
            f"{int(deliveries.SENDER_RATE_WINDOW_SECONDS)}s and you have sent "
            f"{accepted}; the limit is theirs to set or remove"
        )


def _revalidate_source_bindings(conn, source, transfer):
    """Acceptance re-resolves the sender bindings before the delivery exists.

    The copy runs above every acceptance fence, so a sender release, rebind or
    metadata change between the copy and this transaction would otherwise be
    accepted. Only a FIRST acceptance re-resolves: an accepted replay keeps its
    admitted custody rows and must never re-check, re-copy or re-bind.
    """
    from tinyassets import run_file_crossowner

    rows = run_file_crossowner.assert_bound_sources(
        conn, source, [record["source_reference"] for record in transfer["records"]],
    )
    for row, record in zip(rows, transfer["records"]):
        if (row["sha256"], row["size_bytes"]) != (record["sha256"], record["size_bytes"]):
            raise run_file_crossowner.store.FileCustodyRefused("file_source_changed")


def _require_patch_intake_consent(universe_id, receiver_id):
    """Fence the platform-offered patch intake behind the owner's grant.

    A thin adapter so the delivery path names one call and the rule itself has
    one home (``tinyassets/patch_intake.py``). ``PatchIntakeConsentMissing`` is a
    ``ValueError``, so the served action boundary already reports it as an
    invalid delivery request with the reason, rather than as an outage.
    """
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.patch_intake import require_send_consent

    require_send_consent(
        universe_dir=_universe_dir(universe_id), receiver_id=receiver_id
    )


def _accept_output(base, *, principal, universe_id, link_id, occurrence_id, outputs,
                   source=None, should_cancel=lambda: False):
    # Non-exact JSON fails before accepting or reserving a run. Without a trusted
    # source run there is nothing to resolve a file reference against, so the
    # unsourced RPC path keeps refusing every file envelope outright.
    delivery_runtime.validate_output_envelopes(outputs, allow_file_references=source is not None)
    deliveries._exact_json(outputs)
    transfer = None
    if source is not None and delivery_runtime.carries_file_reference(outputs):
        transfer = _transfer_files(
            base, principal=principal, universe_id=universe_id, link_id=link_id,
            occurrence_id=occurrence_id, outputs=outputs, source=source,
            should_cancel=should_cancel,
        )
    with (
        management._owner_authority(base, universe_id, principal),
        deliveries.transaction(base) as conn,
    ):
        link = dict(receiver_links._owned_link(conn, link_id, principal, universe_id))
        # The ONE receiver the platform itself put in front of this universe needs
        # the owner's recorded yes before their words reach another user. Checked
        # here rather than at connect time because this is where content actually
        # crosses, so a grant taken back stops the next send even on a link that
        # already exists. Every other receiver is one this universe found itself,
        # where the receiving owner's exposure is the whole authority. Uniform for
        # a replay too: a withdrawn consent must not be re-honoured by retrying.
        _require_patch_intake_consent(universe_id, link["receiver_id"])
        if source is not None:
            _check_source(conn, source, link)
        branch = management._owned_branch(base, universe_id, link["branch_def_id"], principal)
        placement = next((n for n in branch.graph_nodes if n.id == link["node_id"]), None)
        definition = next((n for n in branch.node_defs if placement
                           and n.node_id == (placement.node_def_id or placement.id)), None)
        if (definition is None
                or set(json.loads(link["mapping_json"])) - set(definition.output_keys)):
            raise ValueError("source output contract changed; reconnect the output")
        prior = conn.execute(
            "SELECT * FROM graph_deliveries WHERE sender_id=? AND sender_universe_id=? "
            "AND link_id=? AND occurrence_id=?",
            (principal, universe_id, link_id, occurrence_id),
        ).fetchone()
        receiver = dict(conn.execute(
            "SELECT * FROM graph_receivers WHERE receiver_id=?", (link["receiver_id"],),
        ).fetchone())
        if prior is None:
            _, receiver = receiver_links.resolve_link_in_transaction(
                conn, link_id=link_id, owner_id=principal, universe_id=universe_id,
            )
            management._owned_branch(
                base, receiver["universe_id"], receiver["branch_def_id"], receiver["owner_id"],
            )
        else:
            # Replay uses the admitted contract/snapshot, not a later revision.
            receiver["snapshot_json"] = prior["snapshot_json"]
            mapped = _replayed_inputs(prior, link, outputs)
        values = (_structured_inputs(receiver, link, outputs, allow_files=source is not None)
                  if prior is None else mapped)
        if prior is None and transfer is not None and not transfer["replay"]:
            _revalidate_source_bindings(conn, source, transfer)
        if should_cancel():
            raise ValueError("delivery_source_cancelled")
        ticket = None
        if prior is None:
            _enforce_sender_rate_limit(conn, receiver, sender_id=principal)
            ticket = engine_admissions.admit(receiver["universe_id"])
        receipt = deliveries.accept_in_transaction(
            conn, sender_id=principal, sender_universe_id=universe_id,
            link_id=link_id, occurrence_id=occurrence_id, request_payload=outputs,
            validated_inputs=values,
            source_run_id=source.run_id if source is not None else None,
            file_transfer=transfer,
        )
        private = deliveries.read_receipt_in_transaction(
            conn, delivery_id=receipt["delivery_id"], principal_id=receiver["owner_id"],
            universe_id=receiver["universe_id"],
        )
    if source is not None:
        # A committed cross-owner transfer is a write even if the node later
        # fails/cancels. Final settlement cannot be refunded by the effect chain.
        engine_admissions.settle_write(source.run_id)
    if ticket is not None:
        engine_admissions.attach_run(ticket, private["run_id"])
    # Queue only after the authoritative acceptance transaction commits.
    delivery_runtime.dispatch_accepted_delivery(
        base, delivery_id=receipt["delivery_id"], attempt=receipt["attempt"],
    )
    return receipt


def enveloped(receipt):
    """A receipt whose answer note is marked as the receiving owner's words."""
    from tinyassets.untrusted import envelope

    answer = receipt.get("answer")
    if answer:
        receipt = dict(receipt, answer=dict(
            answer, note=envelope("receiving-owner", answer["note"])))
    return receipt


def take_answered_notices(*, universe_id, principal_id):
    """One platform notice per newly answered delivery this sender made, told once.

    Turn context, never a resident prompt line: the agent that filed a patch
    request learns it shipped (or was declined), so a workaround or a "the
    platform can't do X" belief built on the gap does not outlive the fix.
    """
    with deliveries.transaction(management._base()) as conn:
        answered = deliveries.take_new_answers_in_transaction(
            conn, principal_id=principal_id, universe_id=universe_id,
        )
    notices = []
    for receipt in answered:
        delivery_id = receipt["delivery_id"]
        note = json.dumps(enveloped(receipt)["answer"]["note"], ensure_ascii=False)
        notices.append(
            f"[Platform notice] A request you sent was answered: delivery {delivery_id} "
            f"is {receipt['outcome']}. Re-check anything in your own files that "
            "depended on it -- a workaround script, a skill, a note or belief that "
            "the platform cannot do this -- and update or retire it. "
            f'read_graph target="delivery" query="{delivery_id}" shows it again. '
            f"The receiving owner's note: {note}"
        )
    return notices


def answer_delivery(*, universe_id, delivery_id, outcome, note=""):
    """The receiving owner says what came of a delivery: ``resolved`` or ``declined``."""
    principal = management._principal(write=True)
    base = management._base()
    management._require_admin(base, universe_id, principal)
    with deliveries.transaction(base) as conn:
        return enveloped(deliveries.answer_in_transaction(
            conn, delivery_id=delivery_id, principal_id=principal,
            universe_id=universe_id, outcome=outcome, note=note,
        ))


def action(action_name, kwargs):
    """Called only by the canonical extensions action/scope dispatcher."""
    try:
        payload = json.loads(kwargs.get("payload_json") or kwargs.get("inputs_json") or "{}")
        if not isinstance(payload, dict) or "universe_id" in payload:
            raise ValueError("payload must be an object without an authority selector")
        uid = kwargs.get("universe_id") or ""
        functions = {
            "create_receiver": management.save_receiver,
            "update_receiver": management.save_receiver,
            "revoke_receiver": management.revoke_receiver,
            "connect_output": management.connect_output,
            "disconnect_output": management.disconnect_output,
            "deliver_output": deliver_output,
            "answer_delivery": answer_delivery,
        }
        if action_name in functions:
            if action_name == "create_receiver" and payload.get("receiver_id"):
                raise ValueError("create_receiver cannot select an existing receiver")
            if action_name == "update_receiver" and not payload.get("receiver_id"):
                raise ValueError("update_receiver requires receiver_id")
            result = functions[action_name](universe_id=uid, **payload)
        elif action_name == "inspect_receiver":
            result = management.inspect_receiver(**payload)
        elif action_name == "discover_receivers":
            result = management.discover_receivers(universe_id=uid, **payload)
        else:
            principal = management._principal(write=False)
            base = management._base()
            management._require_admin(base, uid, principal)
            with deliveries.transaction(base) as conn:
                if action_name == "get_delivery":
                    result = enveloped(deliveries.read_receipt_in_transaction(
                        conn, delivery_id=payload["delivery_id"],
                        principal_id=principal, universe_id=uid,
                    ))
                elif action_name == "list_deliveries":
                    result = {"deliveries": [enveloped(item) for item in (
                        deliveries.list_sent_in_transaction(
                            conn, principal_id=principal, universe_id=uid,
                            limit=payload.get("limit") or 20,
                        ))]}
                elif action_name == "list_output_links":
                    result = {"links": [dict(row) for row in conn.execute(
                        "SELECT link_id, branch_def_id, node_id, receiver_id, "
                        "receiver_generation, mapping_json, disconnected_at "
                        "FROM graph_output_links WHERE owner_id=? AND universe_id=?",
                        (principal, uid),
                    )]}
                else:
                    raise ValueError("unknown_delivery_action")
        return json.dumps(result, ensure_ascii=False)
    except PermissionError:
        return json.dumps({"error": "receiver_or_link_not_found"})
    except (ValueError, TypeError, KeyError) as exc:
        return json.dumps({"error": "invalid_delivery_request", "detail": str(exc)})
