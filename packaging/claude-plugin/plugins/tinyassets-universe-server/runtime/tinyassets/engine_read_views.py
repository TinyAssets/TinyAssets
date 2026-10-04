"""What a served agent gets by DEFAULT from the two biggest engine reads.

The generic ceiling (``engine_result_bounds``) is a backstop: it stops a turn
dying of context overflow, but a truncated catalogue is still a bad answer. These
projections are the good answer -- the same reads, shaped so the agent can act on
them without asking for a megabyte first.

Measured on the live turn that caused this (2026-09-26, free-model universe,
``read_graph target="model_options"``): 1,274,067 bytes of model catalogue and
32.6 KB of status, in a turn that only wanted to know which model was selected.

Both projections are ADDITIVE about detail, never about existence: a count is
always beside a page, so the agent can see there are 347 models even when it is
looking at 8 of them. Nothing here decides anything for the agent; it decides
what arrives unasked.

Scope, per function — the module name says "engine" because that is where both
started, and one of them has since outgrown it:

* ``compact_model_options`` is SHARED by both model-door surfaces: the engine's
  ``read_graph target="model_options"`` and the connector's (where
  ``model_options_summary`` is a synonym). One projection, so the two surfaces
  cannot drift into disagreeing about what a compact catalogue is.
* ``universe_status_view`` is ENGINE-ONLY. The connector's ``get_status`` carries
  the conversation peek's own page contract, which this view would drop.

What neither function touches: ``read_model_options`` (the collector) and
``model_options_document()`` (the row). The complete catalogue is the OWNER
door's (``tinyassets/owner_door``, ``/app/api/read``): the owner's picker renders
every choice there, and nothing in this module can reach it -- the owner door
cannot import this module (``tests/test_owner_door_import_boundary.py``).
"""

from __future__ import annotations

import json
from typing import Any

from tinyassets.engine_result_bounds import CLIPPED_KEY, clip_to_fit, page_to_fit

#: Models shown per source in the default (unfiltered, first-page) view. Enough
#: to choose from -- the top of the platform's own ordering is where a sane
#: choice already is -- without enumerating a whole provider's inventory.
TOP_PER_SOURCE = 8

#: Rows per page once the agent filters or pages explicitly. It asked, so it gets
#: more, still bounded.
PAGE_ROWS = 25

#: The target a caller asks again for more rows. On both model-door surfaces
#: ``model_options`` IS this projection and honours the selectors, so one name
#: serves both. (Until 2026-09-30 the connector's ``model_options`` was the
#: complete document, for the app's picker, and needed a second name here; the
#: app now reads the complete catalogue through the owner door.)
MORE_TARGET = "model_options"


def _more_hint(target: str) -> str:
    return (
        f'read_graph target="{target}" query="<text>" filters by model id or '
        "provider; output_offset=<the next_offset a page returned> walks the rest"
    )

#: Status blocks that describe the HOST and the deployment, not this universe:
#: activity-log tails, byte counts of the host's disk, ship health, supervisor
#: and release state. The uptime probes read them; a universe agent asked to
#: build a UI does not, and they are most of the 32.6 KB. Present in full via
#: ``query="full"``.
#:
#: A DENY list on purpose: a status field added next month is universe state far
#: more often than host telemetry, so the default must be to pass it through.
#: Dropping something the agent needed is a silent wrong answer; carrying one
#: extra small field is not.
HOST_STATUS_BLOCKS = (
    "evidence",
    "evidence_caveats",
    "identity_evidence",
    "storage_utilization",
    "provider_admission",
    "supervisor_liveness",
    "auto_ship_health",
    "open_brain",
    "release_state",
    "daemon",
    "active_host",
    "tier_routing_policy",
    "missing_data_files",
)

_STATUS_FULL_HINT = 'read_graph target="status" query="full" returns every block'


def _reference(row: object) -> tuple[str, str]:
    reference = row.get("reference") if isinstance(row, dict) else None
    if not isinstance(reference, dict):
        return "", ""
    return (
        str(reference.get("provider_ref") or ""),
        str(reference.get("model_id") or ""),
    )


def _compact_row(row: dict) -> dict:
    """One model as the facts a choice actually turns on.

    Kept: who serves it, its id, where the platform's own order puts it, whether
    it can be selected at all, how its availability was established, its context
    window, tool support, and whether it costs anything. Dropped: per-component
    pricing, modality lists, benchmark scores, freshness stamps and eligibility
    prose -- available in the full read.

    ``availability_basis`` is kept deliberately and not for size reasons: the
    shared learned catalogue (#4028) adds rows this universe has NOT verified
    itself, carrying ``platform_verified_elsewhere`` with
    ``in_candidate_catalog: false``. Dropping it would leave the agent unable to
    tell a model it can use from one somebody else proved works.
    """
    provider_ref, model_id = _reference(row)
    pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
    compact = {
        "provider_ref": provider_ref,
        "model_id": model_id,
        "order_index": row.get("order_index"),
        "selectable": bool(row.get("in_candidate_catalog")),
        "availability_basis": row.get("availability_basis"),
        "context_tokens": row.get("context_tokens"),
        "tools": row.get("tools"),
        "unmetered": pricing.get("unmetered"),
    }
    labels = row.get("labels")
    if isinstance(labels, list) and labels:
        compact["labels"] = labels
    reasons = row.get("reasons")
    if isinstance(reasons, list) and reasons:
        # Only the reason word: a model the agent cannot pick must say why, but
        # the component/prose belongs to the full read.
        compact["reasons"] = sorted({
            str(item.get("reason")) for item in reasons if isinstance(item, dict)
        })
    return compact


def _ordered(rows: list[dict]) -> list[dict]:
    """The platform's existing ordering, with unordered models after it.

    ``order_index`` is the plan's own candidate order -- this adds no ranking of
    its own. A model outside that order is still a choice, so it follows rather
    than disappearing.
    """
    def key(row: dict) -> tuple:
        index = row.get("order_index")
        ordered = type(index) is int
        provider_ref, model_id = _reference(row)
        return (0 if ordered else 1, index if ordered else 0, provider_ref, model_id)

    return sorted(rows, key=key)


def _matches(row: dict, needle: str) -> bool:
    provider_ref, model_id = _reference(row)
    return needle in model_id.lower() or needle in provider_ref.lower()


def compact_model_options(
    document: object, *, query: str = "", offset: int = 0,
    more_target: str = MORE_TARGET,
) -> object:
    """Project the full advisory catalogue into a default a small model can read.

    An error document, or anything that is not the catalogue, passes through
    untouched: a projection must never turn a refusal into data.

    Three shapes, one function:

    * no query and ``offset`` 0 -- per source: how many models it has, how many
      are selectable, the current choice, and the top ``TOP_PER_SOURCE`` of the
      platform's own order;
    * a ``query`` -- the matching rows, ``PAGE_ROWS`` at a time;
    * ``offset`` past 0 -- every row, ``PAGE_ROWS`` at a time, same order.

    Every shape carries totals and ``next_offset``, so "there are more" is never
    something the agent has to infer.

    ``more_target`` names the target the caller asks again: ``model_options``,
    which is this projection on every model-door surface.
    """
    if not isinstance(document, dict) or "options" not in document or document.get("error"):
        return document
    rows = [row for row in document.get("options") or () if isinstance(row, dict)]
    needle = (query or "").strip().lower()
    # A CURSOR, not an index: the agent copies back the ``next_offset`` it was
    # handed. 0 means "no cursor yet", which is the default view -- so the flat
    # listing's first row is cursor 1 and no row is unreachable.
    cursor = offset if type(offset) is int and offset > 0 else 0
    start = max(0, cursor - 1)
    view: dict[str, object] = {
        "kind": document.get("kind"),
        "view": "compact",
        "generation": document.get("generation"),
        "policy_source": document.get("policy_source"),
        "mode": document.get("mode"),
        "binding_state": document.get("binding_state"),
        "binding": document.get("binding"),
        "choice_authority": document.get("choice_authority"),
        "selected": _selected(document),
        "total_models": len(rows),
        "selectable_models": sum(1 for row in rows if row.get("in_candidate_catalog")),
        "unavailable_count": len(document.get("unavailable") or ()),
        "source_failures": document.get("source_failures"),
        "how_to_see_more": _more_hint(more_target),
    }
    if needle or cursor:
        matching = _ordered([row for row in rows if not needle or _matches(row, needle)])
        page = matching[start:start + PAGE_ROWS]
        after = start + len(page)
        view["query"] = needle
        view["models"] = [_compact_row(row) for row in page]
        view["page"] = {
            "offset": cursor or 1, "returned": len(page), "matching": len(matching),
            "next_offset": (after + 1) if after < len(matching) else None,
        }
        return view
    view["sources"] = _source_summaries(rows, document)
    shown = sum(len(source["top"]) for source in view["sources"])
    view["page"] = {
        "offset": 0, "returned": shown, "matching": len(rows),
        # The whole flat listing from row one, not "the rest after the tops":
        # each source's top rows are a sample, so resuming past them would skip
        # models that belong to no source's head.
        "next_offset": 1 if shown < len(rows) else None,
    }
    return view


def _selected(document: dict) -> object:
    """The current choice as the picker's own ``order`` reports it."""
    order = document.get("order")
    if isinstance(order, list) and order and isinstance(order[0], dict):
        return order[0]
    return None


def _source_summaries(rows: list[dict], document: dict) -> list[dict]:
    """Per-source counts plus the head of the order, in source document order."""
    by_source: dict[str, list[dict]] = {}
    for row in rows:
        by_source.setdefault(_reference(row)[0], []).append(row)
    listed = [
        str(source.get("provider_ref") or "")
        for source in document.get("sources") or ()
        if isinstance(source, dict)
    ]
    order = [ref for ref in listed if ref in by_source]
    order += [ref for ref in by_source if ref not in set(order)]
    summaries = []
    for ref in order:
        owned = _ordered(by_source[ref])
        summaries.append({
            "provider_ref": ref,
            "models": len(owned),
            "selectable": sum(1 for row in owned if row.get("in_candidate_catalog")),
            "top": [_compact_row(row) for row in owned[:TOP_PER_SOURCE]],
        })
    return summaries


def universe_status_view(document: object) -> object:
    """Drop the host/deployment telemetry from a served agent's status read.

    The dropped block NAMES are kept in ``host_blocks_omitted`` beside the hint
    that returns them, because an agent that cannot see a field was omitted will
    conclude the platform does not report it.
    """
    if not isinstance(document, dict) or document.get("error"):
        return document
    omitted = [key for key in HOST_STATUS_BLOCKS if key in document]
    if not omitted:
        return document
    view = {key: value for key, value in document.items() if key not in set(omitted)}
    view["host_blocks_omitted"] = omitted
    view["how_to_see_more"] = _STATUS_FULL_HINT
    return view


# -- Access and automations (live 2026-10-01) ------------------------------------
#
# Both reads used to be cut by the ceiling instead of shaped for it. Here, in the
# model door, they are filtered and paged so every row stays reachable; the owner
# door keeps the complete documents and cannot import this module.

#: The row lists a model door can filter with ``query`` and page by section.
PAGED_SECTIONS = (
    "channels", "channel_consents", "workspace_consents",
    "waiting_requests", "standing_decisions",
)

#: Bytes a model door keeps free under its result ceiling for the transport's
#: own framing, so a projection that fits here is never cut downstream.
CEILING_HEADROOM_BYTES = 1_024


def _bytes(value: Any) -> int:
    # The engine returns ``json.dumps(..., default=str)`` verbatim; measuring the
    # same rendering (ASCII-escaped, so never smaller) means "fits" here is
    # "fits" on both doors.
    return len(json.dumps(value, default=str).encode("utf-8"))


def _row_contains(row: Any, needle: str) -> bool:
    return needle in json.dumps(row, default=str, ensure_ascii=False).lower()


#: Longest ``query`` a model door accepts. The query is echoed in every
#: continuation call, so an unbounded one could by itself overflow the result.
MAX_QUERY_CHARS = 512


def _section_call(section: str, offset: int, query: str, scope: str = "") -> str:
    call = f'read_graph target="access"{scope} field_name="{section}"'
    if offset:
        call += f" output_offset={offset}"
    if query:
        call += f" query={json.dumps(query, ensure_ascii=False)}"
    return call


def project_access(
    document: dict[str, Any], *, query: str = "", section: str = "",
    offset: int = 0, budget: int, scope: str = "",
) -> dict[str, Any]:
    """The access read as a model door serves it: filtered, sectioned, never cut.

    Live 2026-09-28..10-01 (the founder's universe): this read grew to 25,001
    bytes, the result ceiling cut it at 21,764, and the tail -- the standing
    decisions -- was unreadable on every wake; ``query`` changed nothing because
    nothing read it. Data size must not change what the agent can see, so:

    - ``query`` keeps only the rows (in every paged section) whose JSON contains
      it, case-insensitive, and reports how many matched per section;
    - ``section`` (``field_name`` on the tool) reads one section's rows from
      ``offset``, a page at a time, with ``next_offset`` until ``complete``;
    - with no section, a document over ``budget`` inlines the sections that fit
      and replaces each other one with its row count and the exact call that
      reads it. Every row stays reachable; nothing is silently dropped.

    ``budget`` is bytes of rendered JSON. One row larger than the whole budget is
    returned alone with its long strings clipped and named in ``clipped_chars``
    (``engine_result_bounds.page_to_fit``), so the cursor past it survives.
    ``scope`` is appended to every continuation call (the connector's
    ``graph_id``), so a call read off the result reads the same universe.
    """
    if not isinstance(document, dict) or "error" in document:
        return document
    if len((query or "").strip()) > MAX_QUERY_CHARS:
        return {"error": "query_too_long", "max_chars": MAX_QUERY_CHARS}
    needle = (query or "").strip().lower()
    doc = dict(document)
    matched: dict[str, int] = {}
    if needle:
        for name in PAGED_SECTIONS:
            rows = doc.get(name)
            if isinstance(rows, list):
                doc[name] = [row for row in rows if _row_contains(row, needle)]
                matched[name] = len(doc[name])
    filters = {"query": query.strip(), "matched": matched} if needle else {}

    wanted = (section or "").strip().lower()
    if wanted:
        if wanted not in PAGED_SECTIONS:
            return {
                "error": "unknown_access_section",
                "field_name": section,
                "sections": list(PAGED_SECTIONS),
            }
        rows = doc.get(wanted)
        if not isinstance(rows, list):
            # An unreadable section reports itself; it has no rows to page.
            return {"universe_id": doc.get("universe_id"), "section": wanted,
                    wanted: rows, **filters}
        start = max(0, int(offset or 0))
        return page_to_fit(
            rows, start=start, budget=budget,
            render=lambda value: json.dumps(value, default=str),
            build=lambda page, next_offset: {
                "universe_id": doc.get("universe_id"), "section": wanted,
                "total": len(rows), "offset": start, "rows": page, **filters,
                "complete": next_offset is None, "next_offset": next_offset,
                "next": (None if next_offset is None
                         else _section_call(wanted, next_offset, query.strip(), scope)),
            },
        )

    doc.update(filters)
    if _bytes(doc) <= budget:
        return doc
    # Too big to send whole: inline sections in their usual order while they
    # fit, point at the rest. The pointer is reserved first so the final
    # document is measured with every pointer it will actually carry.
    pointers = {
        name: {"count": len(doc[name]),
               "read_with": _section_call(name, 0, query.strip(), scope)}
        for name in PAGED_SECTIONS if isinstance(doc.get(name), list)
    }
    projected = {key: value for key, value in doc.items() if key not in pointers}
    projected["complete"] = False
    projected["sectioned"] = dict(pointers)
    projected["note"] = (
        "This access read is too large for one result. Sections under "
        "`sectioned` are not inline: read each with its read_with call and "
        "follow next_offset until complete, or narrow every section with query."
    )
    for name in PAGED_SECTIONS:
        if name not in pointers:
            continue
        trial = {**projected, name: doc[name]}
        trial["sectioned"] = {k: v for k, v in projected["sectioned"].items() if k != name}
        if _bytes(trial) <= budget:
            projected = trial
    if not projected["sectioned"]:
        for key in ("sectioned", "note"):
            projected.pop(key)
        projected["complete"] = True
    # Last resort for the fixed parts (spend allowances, verbs): clip, never cut.
    return clip_to_fit(projected, budget=budget,
                       render=lambda value: json.dumps(value, default=str))


# -- Model-door projection ----------------------------------------------------
#
# Live 2026-10-01 (the founder's universe): 8 active automations projected to
# 282,886 bytes, because every row carries its whole ``inputs`` (33-90 KB each:
# an agent node's prompt and context). The result ceiling cut the list inside
# the FIRST row, so the agent could not recover its own morning-note schedule id
# and re-audited instead. The owner door keeps the complete rows; a model door
# serves this projection, which pages itself under the ceiling and never cuts.


def _value_text(value: Any) -> str:
    """An input as the agent reads it: strings verbatim, the rest as JSON."""
    return value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, default=str,
    )


def _without_input_bodies(row: dict[str, Any], scope: str = "") -> dict[str, Any]:
    """A row with each input's character count instead of its value."""
    inputs = row.get("inputs")
    if not isinstance(inputs, dict):
        return row
    summarized = {key: value for key, value in row.items() if key != "inputs"}
    summarized["input_chars"] = {key: len(_value_text(v)) for key, v in inputs.items()}
    summarized["inputs_read_with"] = (
        f'read_graph target="automation"{scope} '
        f'automation_id="{row.get("automation_id", "")}" field_name="<input name>"'
    )
    return summarized


def project_automations(
    result: dict[str, Any], *, budget: int, render, offset: int = 0,
    max_rows: int | None = None, scope: str = "",
) -> dict[str, Any]:
    """A ``list`` result as a model door serves it: every id, paged to fit.

    Rows carry ``input_chars`` (name -> size) instead of input bodies; read a
    body with ``target="automation"`` and ``field_name``. ``offset`` continues
    from the returned ``next_offset``. ``render`` is the exact text the door
    returns, so the fit is measured on its real bytes. ``scope`` is appended to
    every continuation call (the connector's ``graph_id``).
    """
    rows = result.get("automations")
    if not isinstance(rows, list):
        return result
    rows = [_without_input_bodies(r, scope) if isinstance(r, dict) else r for r in rows]
    start = max(0, int(offset or 0))
    head = {k: v for k, v in result.items() if k not in {"automations", "count"}}

    def build(page, next_offset):
        return {
            **head, "automations": page, "count": len(page),
            "total": len(rows), "offset": start,
            "complete": next_offset is None, "next_offset": next_offset,
            "next": (None if next_offset is None else
                     f'read_graph target="automations"{scope} '
                     f'output_offset={next_offset}'),
        }

    return page_to_fit(rows, start=start, budget=budget, build=build,
                       render=render, max_rows=max_rows)


def project_automation(
    result: dict[str, Any], *, budget: int, render, field_name: str = "",
    offset: int = 0, max_chars: int = 8192, scope: str = "",
) -> dict[str, Any]:
    """A ``get`` result as a model door serves it: whole, or read in parts.

    With ``field_name`` it returns that input's text from ``offset``, a chunk at
    a time (``next_offset`` until ``complete``). Without, the row is returned
    whole when it fits ``budget``, else with ``input_chars`` in place of input
    bodies. The result keeps the ``automation`` key and its ``owner`` so the
    door's provenance wrapping applies to a chunk exactly as to the row.
    """
    row = result.get("automation")
    if not isinstance(row, dict):
        return result
    if not field_name:
        if len(render(result).encode("utf-8")) <= budget:
            return result
        summary = {**result, "automation": _without_input_bodies(row, scope)}
        return clip_to_fit(summary, budget=budget, render=render)
    inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
    # Exact key first: input names are arbitrary, so " prompt " is not "prompt".
    name = field_name if field_name in inputs else field_name.strip()
    if name not in inputs:
        # Under ``automation`` with its owner, so another owner's input names
        # keep the door's provenance wrapping even in a refusal.
        return {"error": "unknown_automation_input", "field_name": field_name,
                "automation": {"automation_id": row.get("automation_id"),
                               "owner": row.get("owner"), "inputs": sorted(inputs)}}
    text = _value_text(inputs[name])
    start = max(0, int(offset or 0))
    size = max(1, min(32768, int(max_chars or 8192)))
    title = str(row.get("name") or "")
    head = {"automation_id": row.get("automation_id"), "name": title[:256],
            "owner": row.get("owner"), "input": name[:256], "total_chars": len(text),
            "offset": start}
    if len(title) > 256 or len(name) > 256:
        # Metadata is bounded so the chunk, not the labels, gets the budget.
        head[CLIPPED_KEY] = {k: len(v) for k, v in (("name", title), ("input", name))
                             if len(v) > 256}

    def chunk(n: int) -> dict[str, Any]:
        end = min(len(text), start + n)
        more = end < len(text)
        return {"automation": {
            **head, "value": text[start:end], "complete": not more,
            "next_offset": end if more else None,
        }}

    document = chunk(size)
    # Escaping can grow a chunk past the ceiling (a CJK character renders as six
    # ASCII bytes), so shrink the chunk, never the cursor's honesty.
    while size > 1 and len(render(document).encode("utf-8")) > budget:
        size = max(1, size * budget // len(render(document).encode("utf-8")) - 1)
        document = chunk(size)
    return document


# Public immutable definitions: metadata first; exact bodies remain pageable.
def _agent_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _agent_bytes(value):
    # Both served doors may escape non-ASCII when wrapping the result.
    return len(json.dumps(value).encode("utf-8"))


def agent_summary(row, *, compact=False):
    """A bounded preview, never component bodies or private import-stage data."""
    components = row.get("components") or {}
    result = {key: row.get(key) for key in (
        "agent_definition_id", "author_id", "name", "description", "tags",
        "content_fingerprint", "created_at",
    )}
    result["component_count"] = len(components)
    result["component_kinds"] = sorted({str(c.get("kind", ""))[:64]
                                        for c in components.values()})[:8]
    package = components.get("package") or {}
    result["publication_kind"] = (
        "command_center" if "tinyassets.command-center-package.v1" in (row.get("tags") or [])
        and package.get("kind") == "tinyassets.package.v1" else
        "workflows" if components and all(c.get("kind") == "tinyassets.branch-ref.v1"
                                          for c in components.values()) else "system"
    )
    if package.get("kind") == "tinyassets.package.v1":
        result["package"] = {k: (package.get(k) if type(package.get(k)) in (int, float)
                                  else str(package.get(k) or "")[:64]) for k in
                             ("version", "size_bytes", "file_count", "blob_sha256")}
        agents = package.get("agents")
        result["package"]["agent_count"] = len(agents) if isinstance(agents, list) else 0
        needs = package.get("needs")
        needs = needs if isinstance(needs, dict) else {}
        connections = needs.get("connections")
        connections = connections if isinstance(connections, list) else []
        result["package"]["needs"] = {
            "model": str(needs.get("model") or "")[:64],
            "connection_count": len(connections),
            "connections": [str(x)[:32] for x in connections[:4]],
        }
    # Every shortened preview is explicitly named; @definition recovers all data.
    clipped = []
    for key, size in (("name", 64), ("description", 96)):
        value = str(result.get(key) or "")
        result[key] = value[:size]
        if len(value) > size:
            clipped.append(key)
    tags = result.get("tags") or []
    result["tags"] = [str(t)[:32] for t in tags[:4]]
    result["tag_count"] = len(tags)
    if result["tags"] != tags:
        clipped.append("tags")
    result["summary_only"] = True
    result["clipped_fields"] = clipped
    result["read_with"] = {"target": "agent", "agent_definition_id": row["agent_definition_id"]}
    result["full_definition_field"] = "@definition"
    if compact:
        for key in ("name", "description"):
            if len(result[key]) > 24:
                result[key] = result[key][:24]
                clipped.append(key)
        result["tags"] = [str(t)[:16] for t in tags[:2]]
        result["component_kinds"] = [kind[:24] for kind in result["component_kinds"][:2]]
        if "package" in result:
            result["package"]["needs"]["model"] = result["package"]["needs"]["model"][:24]
            result["package"]["needs"]["connections"] = []
        result["compact_preview"] = True
    return result


def project_agent(row, *, field_name="", offset=0, max_chars=8192, budget=23000):
    """Component catalog or lossless JSON chunk; offsets never silently reset."""
    if type(offset) is not int or offset < 0:
        return {"error": "output_offset must be a non-negative integer"}
    if type(max_chars) is not int or not 1 <= max_chars <= 32768:
        return {"error": "output_max_chars must be between 1 and 32768"}
    components = row.get("components") or {}
    if field_name:
        if field_name != "@definition" and field_name not in components:
            return {"error": "unknown_agent_component", "field_name": field_name}
        text = _agent_json(row if field_name == "@definition" else components[field_name])
        if offset > len(text):
            return {"error": "output_offset is past the selected component"}
        size = min(max_chars, len(text) - offset)
        while True:
            end = offset + size
            result = {"agent_definition_id": row["agent_definition_id"],
                      "field_name": field_name, "encoding": "json",
                      "chunk": text[offset:end], "offset": offset,
                      "offset_unit": "unicode_code_points", "total_chars": len(text),
                      "next_offset": end if end < len(text) else None,
                      "complete": end == len(text)}
            if _agent_bytes(result) <= budget:
                return result
            if size <= 1:
                return {"error": "agent_read_budget_too_small"}
            size = max(1, size // 2)
    names = sorted(components)
    if offset > len(names):
        return {"error": "output_offset is past the component catalog"}
    summary = agent_summary(row, compact=budget < 8192)
    page = []

    def build(end):
        return {"agent": summary, "components": page, "offset": offset,
                "offset_unit": "components", "total_components": len(names),
                "next_offset": end if end < len(names) else None,
                "complete": end == len(names)}

    for index in range(offset, len(names)):
        key = names[index]
        component = components[key]
        item = {"key": key, "kind": str(component.get("kind", ""))[:64],
                "name": str(component.get("name", ""))[:64],
                "total_chars": len(_agent_json(component)),
                "field_name": key}
        page.append(item)
        if _agent_bytes(build(index + 1)) > budget:
            page.pop()
            if not page:
                return {"error": "agent_read_budget_too_small"}
            break
    return build(offset + len(page))


def project_agents(rows, *, offset=0, budget=23000, more=False):
    """Page summaries by encoded size without skipping any definition."""
    page = []

    def build():
        next_offset = offset + len(page) if more or len(page) < len(rows) else None
        return {"agents": page, "count": len(page), "offset": offset,
                "offset_unit": "definitions", "next_offset": next_offset,
                "complete": next_offset is None}

    for row in rows:
        page.append(agent_summary(row, compact=budget < 8192))
        if _agent_bytes(build()) > budget:
            page.pop()
            if not page:
                return {"error": "agent_read_budget_too_small"}
            break
    return build()
