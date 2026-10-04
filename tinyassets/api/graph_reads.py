"""The ``read_graph`` dispatch: ONE domain read, shared by both doors.

Moved out of ``tinyassets/universe_server.py`` (2026-09-30,
``openspec/changes/archive/2026-09-30-owner-door-complete-reads/``). Two callers use it:

* the **model door**, ``universe_server.read_graph`` (the MCP connector). It
  projects what this returns for a model's context: the single-result ceiling in
  ``_structured_return`` and the compact ``model_options`` view.
* the **owner door**, ``tinyassets/owner_door`` (``/app/api/read``). It returns
  this whole to the owner's app.

So this module returns COMPLETE data, and it cannot do otherwise. It imports no
bounding or projection module; ``tests/test_owner_door_import_boundary.py``
fails if it does. A size, ceiling or truncation belongs to the model door, which
is the only place a model's context window exists. The live incident this
prevents: the founder's 34 KB request queue crossed the connector's 24 KB model
ceiling, and the app, reading through the same path, hid the whole rail.

Authority is unchanged by the move. Every target reaches the same domain
function, and that function applies its own owner gate under the request
identity. This module adds no gate and removes none.
"""

from __future__ import annotations

import json

from tinyassets.api.automations import automations as _automations_impl
from tinyassets.api.cloud_connections import cloud_connections as _cloud_connections_impl
from tinyassets.api.custom_agents import custom_agents as _custom_agents_impl
from tinyassets.api.extensions import _extensions_impl
from tinyassets.api.market import goals as _goals_impl
from tinyassets.api.status import get_status as _get_status_impl
from tinyassets.api.universe import _universe_impl
from tinyassets.command_center_names import internal_value

#: Targets that list rows and so take a page size. The dispatch has no default
#: for it: a caller that lists names its page.
PAGED_TARGETS = frozenset({
    "receivers", "graphs", "branches", "goals", "runs", "automations",
    "agents", "agent_bindings",
})


def _unknown_target(handle: str, target: str, allowed: tuple[str, ...]) -> str:
    return json.dumps({
        "error": "unknown_target",
        "handle": handle,
        "target": target,
        "allowed_targets": allowed,
    })


def read_graph(
    target: str = "status",
    graph_id: str = "",
    goal_id: str = "",
    run_id: str = "",
    branch_id: str = "",
    automation_id: str = "",
    agent_definition_id: str = "",
    agent_binding_id: str = "",
    agent_stage_id: str = "",
    query: str = "",
    tags: str = "",
    author: str = "",
    run_status: str = "",
    limit: int | None = None,
    field_name: str = "",
    output_offset: int = 0,
    output_max_chars: int = 8192,
    request_key: str = "",
    file_id: str = "",
    file_offset: int = 0,
    file_max_bytes: int = 524288,
) -> str:
    """Read graph state without changing it; a JSON string, complete.

    The parameters mean what ``universe_server.read_graph`` documents (that
    docstring is the public tool contract). ``limit`` has no default here; see
    ``PAGED_TARGETS``.
    """
    normalized = str(internal_value((target or "status").strip().lower()))
    if normalized in PAGED_TARGETS and limit is None:
        # No default page. A page size nobody asked for is a silent cut of the
        # owner's own rows; the caller names the page it wants (the connector
        # names its model-door page; the app names the one it renders).
        return json.dumps({
            "error": "limit_required",
            "target": normalized,
            "detail": "this read lists rows; pass limit=<rows per page>",
        })
    if normalized == "conversation_turn":
        from tinyassets.api.helpers import _base_path, _request_universe
        from tinyassets.api.permissions import current_actor_id, is_authenticated_request
        from tinyassets.consumer_runtime import read_turn

        if not is_authenticated_request():
            return json.dumps({"error": "not_found"})
        return json.dumps(read_turn(_base_path(), owner=current_actor_id(),
                                    universe=_request_universe(graph_id), request_key=request_key))
    if normalized == "conversation":
        # The lossless read of the caller's OWN retained thread -- the same
        # reader and the same binding the engine route uses
        # (engine_mcp_server.read_graph), exposed here because the app speaks
        # only this surface. get_status's peek bounds each turn at 4000 chars
        # and says so (`truncated` + `total_chars`); this is how a client gets
        # the rest instead of drawing a preview as if it were the message.
        #
        # Both modes of the existing reader are exposed, unchanged: omit
        # field_name for the bounded keyset catalogue, pass a turn id for exact
        # Unicode-code-point chunks. One retrieval capability, identical across
        # the engine and public surfaces -- a public-only sub-mode would be a
        # second contract for the same read.
        #
        # Binding: the principal is the VERIFIED current caller, never a
        # browser-supplied session, principal or store path. An explicit
        # graph_id is VERIFIED rather than ignored -- require_founder_home
        # refuses a universe that is not this caller's current home with admin,
        # so a foreign id can never return this caller's bytes under its label.
        from tinyassets import addressed_agents
        from tinyassets.api.helpers import _base_path, _request_universe
        from tinyassets.api.permissions import current_actor_id, is_authenticated_request
        from tinyassets.conversation_retrieval import read_conversation_page
        from tinyassets.shared_self import require_founder_home

        if not is_authenticated_request():
            return json.dumps({"error": "not_found"})
        actor = current_actor_id()
        try:
            base, uid = _base_path(), _request_universe(graph_id)
            root = require_founder_home(base, uid, actor)
            agent = addressed_agents.resolve(
                base, universe_id=uid, owner=actor, agent_id=agent_binding_id,
            )
            session = addressed_agents.memory_session(actor, agent.agent_id if agent else "main")
            payload = read_conversation_page(
                root, session, field_name=field_name,
                offset=output_offset, max_chars=output_max_chars,
            )
        except addressed_agents.AgentNotAddressable as exc:
            return json.dumps({"error": str(exc), "agent_not_found": True})
        except PermissionError:
            # Same envelope an absent thread gets: a refusal here must not
            # confirm another account's home exists.
            return json.dumps({"error": "not_found"})
        except ValueError as exc:
            return json.dumps({"error": str(exc)})   # the caller's own selector
        except Exception:  # noqa: BLE001 - storage detail is never disclosed
            return json.dumps({"error": "conversation_read_failed"})
        # Retained transcript text is content to observe, never instructions --
        # marked exactly as the get_status peek marks it.
        return json.dumps(
            dict(payload, content_is_untrusted=True,
                 fence="BEGIN_UNTRUSTED_TRANSCRIPT", fence_end="END_UNTRUSTED_TRANSCRIPT"),
            ensure_ascii=False,
        )
    if normalized in {"run_file", "run_file_limits"}:
        from tinyassets.api.run_files import file_limits, read_file

        if normalized == "run_file_limits":
            return file_limits(universe_id=graph_id)
        return read_file(universe_id=graph_id, run_id=run_id, file_id=file_id,
                         offset=file_offset, count=file_max_bytes)
    if normalized in {"receiver", "receivers", "output_links", "delivery"}:
        action = {"receiver": "inspect_receiver", "receivers": "discover_receivers",
                  "output_links": "list_output_links",
                  "delivery": "get_delivery"}[normalized]
        payload = ({"receiver_id": query} if normalized == "receiver"
                   else {"delivery_id": query} if normalized == "delivery"
                   else {"query": query, "limit": limit} if normalized == "receivers"
                   else {})
        return _extensions_impl(action=action, universe_id=graph_id,
                                payload_json=json.dumps(payload))
    if normalized == "status":
        return _get_status_impl(universe_id=graph_id)
    if normalized == "graphs":
        return _universe_impl(action="list", limit=limit)
    if normalized == "graph":
        return _universe_impl(action="inspect", universe_id=graph_id)
    if normalized == "branches":
        # The universe's OWN workflows by name + branch_def_id (+ tags/goal). Until
        # now a user had to already know a branch's internal id to read/edit/run
        # it — Claude.ai hit "Global workflow enumeration is not exposed by the
        # advertised handles" when asked to rename a workflow (2026-08-25). The
        # extensions layer already hides branches bound to non-public goals.
        # scope="mine": the caller's OWN workflows, published or not. The default
        # scope ("published") hides every private branch a user just built — which
        # is precisely the "I can't find the workflow you named" failure.
        return _extensions_impl(action="list_branches", scope="mine", limit=limit)
    if normalized == "goals":
        if query:
            return _goals_impl(action="search", query=query, limit=limit)
        return _goals_impl(action="list", tags=tags, author=author, limit=limit)
    if normalized == "goal":
        return _goals_impl(action="get", goal_id=goal_id)
    if normalized == "runs":
        return _extensions_impl(action="list_runs", status=run_status, limit=limit,
                                universe_id=graph_id)
    if normalized == "run":
        # PR-180 SEE half: a founder reads their own run's terminal result +
        # structured failure reason (status, output/external_write_results,
        # error, failure_class/suggested_action/actionable_by/error_detail).
        return _extensions_impl(action="get_run", run_id=(run_id or graph_id),
                                universe_id=graph_id if run_id else "")
    if normalized == "run_output":
        return _extensions_impl(
            action="get_run_output", run_id=run_id, universe_id=graph_id,
            field_name=field_name, bounded_output=True, output_offset=output_offset,
            output_max_chars=output_max_chars,
        )
    if normalized == "branch":
        # SEE-for-branches: a founder reads their branch's full graph + node
        # configs (timeout_seconds, model_hint, prompt_template, edges, state
        # schema) so an edit via write_graph target=branch is informed, not
        # blind. Completes the read/edit symmetry with PR-180.
        #
        # Visibility model (commons-first, deliberate — Codex review of #1404):
        # public BranchDefinitions are a GLOBAL remix commons, readable cross
        # universe by anyone (you remix what you can read). The confidentiality
        # boundary is private branches, which get_branch already author-gates
        # with a "not found" envelope (branches.py:443) so a non-author cannot
        # even confirm existence. get_branch was already callable here via the
        # deprecated 'extensions' tool; this only makes it first-class.
        return _extensions_impl(action="get_branch", branch_def_id=(branch_id or graph_id))
    if normalized in {"automations", "automation"}:
        # The owner's own recurring runs (user-owned-automations 3.1). The read
        # path carries no payload, so `list` here always hides retired rows;
        # an owner who wants the deleted ones asks through the write handle
        # with payload_json {"include_retired": true}.
        return json.dumps(
            _automations_impl(
                action=("list" if normalized == "automations" else "get"),
                universe_id=graph_id,
                automation_id=automation_id,
                **({"limit": limit} if normalized == "automations" else {}),
            )
        )
    if normalized == "connections":
        return json.dumps(
            _cloud_connections_impl(
                action="list",
                universe_id=graph_id,
            )
        )
    if normalized == "pending_requests":
        # The left-rail tabs: what the agent is waiting on the user for. Same
        # read from every surface, which is what makes them addressable from the
        # phone without a second mechanism. Carries no credential material.
        #
        # EVERY pending row, and `limit` does not reach it: a page size here
        # hid the 31st request an owner was waiting on (2026-09-30).
        from tinyassets.api.pending_requests import list_requests

        return json.dumps(list_requests(universe_id=graph_id))
    if normalized == "access":
        # Everything the owner's agent holds in this universe, owner-only and
        # secret-free (change agent-access-controls).
        from tinyassets.api.agent_access import read_access

        return json.dumps(read_access(universe_id=graph_id), default=str)
    if normalized == "agents":
        return json.dumps(
            _custom_agents_impl(
                action="list_agents",
                query=query,
                tags=tags,
                author_id=author,
                limit=limit,
            )
        )
    if normalized == "agent":
        return json.dumps(
            _custom_agents_impl(
                action=("get_import_stage" if agent_stage_id else "get_agent"),
                definition_id=(agent_definition_id or graph_id),
                stage_id=agent_stage_id,
            )
        )
    if normalized == "agent_bindings":
        return json.dumps(
            _custom_agents_impl(
                action="list_bindings",
                universe_id=graph_id,
                limit=limit,
            )
        )
    if normalized == "agent_binding":
        return json.dumps(
            _custom_agents_impl(
                action="get_binding",
                universe_id=graph_id,
                binding_id=agent_binding_id,
            )
        )
    if normalized in {"universe_file", "universe_files"}:
        # The OWNER's read of their universe folder (/u): the files their agents
        # share. Admin-only, link-free, bounded; every refusal is not_found.
        from tinyassets.api import universe_file_reads

        if normalized == "universe_files":
            return json.dumps(universe_file_reads.list_files(universe_id=graph_id, path=query))
        return json.dumps(universe_file_reads.read_file(
            universe_id=graph_id, path=query, offset=file_offset,
            # This handle's file_max_bytes defaults to run_file's 512 KiB; a
            # folder read pages at most MAX_READ_BYTES, so the default clamps.
            count=min(file_max_bytes, universe_file_reads.MAX_READ_BYTES)
            if isinstance(file_max_bytes, int) else file_max_bytes,
        ))
    if normalized == "command_center_packages":
        from tinyassets.command_center_picker import read_packages

        return json.dumps(read_packages(universe_id=graph_id))
    if normalized == "command_center_updates":
        from tinyassets.api.command_center_update_surface import read_updates

        return json.dumps(read_updates(universe_id=graph_id))
    if normalized == "command_center_preview":
        from tinyassets.command_center_preview import read_preview

        return json.dumps(read_preview(
            universe_id=graph_id, definition_id=agent_definition_id,
        ))
    if normalized == "app_ui":
        # The caller's own UI library + choice; keyed by the authenticated caller.
        from tinyassets.api.app_ui import read_app_ui

        # query="index" is the row without UI bodies; query=<ui_id> one UI,
        # field_name one chunk of it. No query is the whole row (the app's read).
        return json.dumps(read_app_ui(
            universe_id=graph_id, ui_id=query, field_name=field_name,
            output_offset=output_offset, output_max_chars=output_max_chars,
        ))
    if normalized == "compute":
        # The read sibling of write_graph target=connection operation=connect_compute:
        # list the compute providers registered for this universe (candidates). Owner-
        # gated + no secret. Lets a user SEE what they registered from any surface.
        from tinyassets.api.compute_connection import read_compute_providers

        return json.dumps(read_compute_providers(universe_id=graph_id))
    if normalized == "model_options":
        from tinyassets.api.model_options import read_model_options

        # The complete owned catalogue. Complete HERE, always: this module is
        # the shared domain read. The model door (``universe_server``) projects
        # it for a model's context; the owner door returns it whole.
        return json.dumps(read_model_options(universe_id=graph_id))
    return _unknown_target(
        "read_graph",
        target,
        (
            "status",
            "graphs",
            "graph",
            "goals",
            "goal",
            "runs",
            "run",
            "branch",
            "automations",
            "automation",
            "connections",
            "pending_requests",
            "access",
            "conversation",
            "compute",
            "model_options",
            "run_file",
            "run_file_limits",
            "agents",
            "agent",
            "agent_bindings",
            "agent_binding",
            "app_ui",
            "command_center_packages",
            "command_center_preview",
            "command_center_updates",
            "command_center_file",
            "command_center_files",
            "receiver",
            "receivers",
            "output_links",
            "delivery",
        ),
    )
