"""Single source of truth for the served agent's engine-MCP tool allowlist.

Founder rule: every surface does the same things. The served universe answers on
different provider families (codex via ``codex exec``, claude via ``claude -p``),
and each wires the engine-MCP server with an ``enabled_tools`` allowlist. Those
two allowlists MUST be identical — a codex-served and a claude-served universe get
exactly the same capability. They used to be two hand-maintained tuples
(``codex_provider._ENGINE_MCP_ENABLED_TOOLS`` and
``universe_intelligence._ENGINE_MCP_TOOLS``), which silently drifted: ``run_graph``
landed on the claude list only, so a codex-served founder could not run their own
automations at all (caught 2026-08-23).

Making both providers import THIS one tuple removes the drift class structurally —
they are now the same object, so "meant to be in sync" is guaranteed by
construction, not by a guard test that only notices after the fact. To change what
the served agent can do, edit this list ONCE and every surface moves together.

Kept deliberately dependency-free so any provider module can import it without a
cycle.
"""

from __future__ import annotations

#: The engine-MCP handles the served agent may call, on EVERY provider surface.
#: A handler is only reachable when it is BOTH registered (``@mcp.tool`` in
#: ``engine_mcp_server``) AND present here.
#:
#: Included:
#:   read_graph, get_status, browse_commons, read_commons_shape  — read surfaces
#:       Model options and private agent bindings are pinned to this universe.
#:       Catalogue refresh uses admission; remote model text is untrusted.
#:   write_graph model_preferences/save and connection/configure_provider_capability
#:       (model_discovery only) reuse current-home preference CAS and existing
#:       owned connection grants. Neither authorizes inference or spending.
#:       Broad agent-binding mutation and pending-request answer remain unavailable.
#:   read_brain, write_brain                                     — the universe's own brain
#:   connect_compute                                             — register a compute
#:       provider (candidate-only, owner-gated, graph-pinned, secret-free; no
#:       execution / cross-universe reach)
#:   run_graph                                                   — RUN one of THIS
#:       universe's approved automations end-to-end (the "do the workflow you
#:       built" parity). Safety rests on #2498's sanitized invoke_branch
#:       (delegated child-authority + fail-closed actor + mapping/await
#:       confidentiality) PLUS run_graph's own gates: per-universe run allowlist,
#:       effect-spam rate limit, IDOR read/execute gate, founder-scoped
#:       capabilities. NOTE: run_graph is NOT author-only — its branch resolver
#:       admits a founder-owned OR a PUBLIC-foreign branch (a foreign PRIVATE
#:       branch is refused); the delegated-authority sanitization is what keeps
#:       that safe, not an author gate.
#:   write_graph   — BUILD or EDIT a branch (the "when the user creates/changes
#:       things" parity), target=branch, operation=create OR patch. Purpose-built
#:       (NOT the broad connector
#:       write_graph): it SANITIZES the spec and calls the author-gated,
#:       EFFECT-FREE build_branch directly with least-privilege caps (no
#:       submit_request). Two Codex review rounds (2026-08-23) hardened it — every
#:       known path to a persisted APPROVED source_code node is closed: submitted
#:       approval/author/fork stripped at node level, `node_ref` (foreign-node
#:       dereference) rejected, nested `graph` blob rejected, `fork_from` stripped,
#:       visibility forced private, size/node/type guards. A served-built
#:       source_code node persists UNAPPROVED as provenance and RUNS: since
#:       sandboxed-code-node the OS sandbox is the boundary and approval never
#:       gates a run (receipts fixed 2026-09-02). Gated to the same per-universe run allowlist as
#:       run_graph (u-tiny). RESIDUAL, tracked as the pre-second-user harden gate
#:       (served-agent-build-run): branches are author-scoped not universe-scoped,
#:       and build_branch's approval surface is broad enough that the robust
#:       multi-tenant fix is a force-unapproved build MODE (clear approval after
#:       any inherit/deref, before persist) + a branch↔universe binding. EDIT
#:       (operation=patch, 2026-08-24): edit an OWN branch in place — safe self-edit
#:       ops (add/remove edges+nodes+state, set entry_point, rename/retag/goal,
#:       remove_skill) ALLOWLISTED; publish / set-visibility-public / fork REFUSED; an
#:       add_node op runs the SAME create per-node sanitizer and MAY declare an effect
#:       on exactly creation's terms (2026-08-31; no per-branch effect-node ceiling
#:       exists — `no-graph-size-caps`); update_node retunes content
#:       (prompt/source/display_name), ordinary configuration (description/phase/
#:       model_hint/reasoning_effort/input_keys/output_keys/timeout_seconds, 2026-09-23
#:       `served-node-edit-parity`) and declarations (llm_policy/effects/workspace) —
#:       never execution/data authority (tools_allowed/invoke/handoffs/approval/author),
#:       and never the inert retry_policy/enabled pair no graph runtime consumes; metadata
#:       field types validated; author-gated + transactional patch_branch. Codex ADAPT
#:       (PR #2518) closed. Residuals tracked (same as create): author-scoped not
#:       universe-scoped, and no expected-version CAS (concurrency harden gate).
#:   read_graph / write_graph automation — inspect and control the owner's
#:       recurring work via the SAME owner-scoped adapter as the connector.
#:       Graph and actor are pinned; existing owner/admin, revision CAS and
#:       creation checks remain. Create is admission-limited; pause/delete do
#:       not require available execution budget. No provider rebind or secret
#:       deposit, and stopping a trigger does not attest a running job stopped.
#:
#:   write_graph / read_graph webhook — give the owner an inbound webhook URL for
#:       one of THEIR OWN branches (C16, 2026-09-24). Same owner-scoped
#:       ``mint_webhook``/``revoke_webhook``/``list_webhooks`` handlers as the
#:       connector's ``run_graph webhook_op``: universe and owner come from the
#:       server's pins, the branch must be the owner's, and every delivery runs
#:       as ``universe:<id>`` for that owner. The URL is shown once at create and
#:       goes to the owner's own agent, exactly as the connector already hands it
#:       to the owner's chatbot. Revoke takes the non-secret ``token_prefix`` the
#:       list shows. Source nodes (event-bus triggers) stay off this surface.
#:
#:   source_channel — APPROVE an outbound channel for your own universe (the consent
#:       half of "add a channel via the channel-agnostic node"). Owner-gated
#:       (source_channel's impl requires an admin ACL row for the bound founder;
#:       unbound / read-write collaborators get auth_failed), graph-PINNED
#:       (universe_id is never caller-supplied), SECRET-FREE (consent is a
#:       (sink, destination) allow — the token is deposited out of band via the browser
#:       form / connect_http, which is deliberately NOT here). SINK CONSENT ONLY:
#:       channel_type=="source_code" is refused (that approval sets approved_source_hash,
#:       the provenance the create-only write_graph strips — keeping it off this
#:       surface keeps a served build from attesting its own code). action=approve or
#:       revoke (change agent-access-controls: revoke narrows, so it may take back any
#:       sink, including a workspace consent the agent cannot grant). approve/revoke are
#:       now the ONLY source_channel operations: set_policy/get_policy were deleted with
#:       their store (2026-09-25), having read nothing. The raw-secret connect_http stays
#:       off-surface. What the agent holds reads back through
#:       read_graph target=access. Gated to the same u-tiny run
#:       allowlist; the outbound call also needs TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED.
#:
#: write_graph (BUILD half of the channel slice, 2026-08-25): the ONE channel-agnostic
#:   effect node (``authenticated_external_call``) is now allowed in a served create — an
#:   allowlist (every other sink, incl. ``wiki_write_back``, and the typed ``handoffs``
#:   path are refused), capped at a small effect-node count per build. Building declares
#:   only the sink NAME and fires nothing; the run-time effector re-checks the
#:   connection-grant-bound-to-this-universe + per-destination consent (granted via
#:   source_channel) + TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED + SSRF per dispatch.
#:
#: read, write, edit, bash (universe-harness S1, 2026-09-24) — the agent's four
#:   tools over its OWN universe folder, executed by the platform in the tool
#:   jail (``tinyassets.universe_tools``): the universe at ``/u`` only, its
#:   root read-only with just the agent-owned brain files and harness dirs
#:   read-write, every hidden root entry (credential vault, ``.runtime``,
#:   consent/usage DBs) masked, no network, no credential,
#:   rlimits + wall clock + output and process-tree caps. Pinned like every
#:   handle here; no parameter names a universe.
#:
#: Deliberately EXCLUDED pending their own review (tracked by the
#: ``served-agent-build-run`` OpenSpec change):
#:   remix_shape   — cross-author commons remix
#:   connect_http  — deposits a RAW SECRET; stays on the browser deposit form
#:   a proper per-root-run effect-dispatch cap (all surfaces) — the served build cap on
#:       effect-node count is the interim structural bound
BACKEND_ENGINE_CAPABILITIES: tuple[str, ...] = (
    "read_graph",
    "get_status",
    "run_graph",
    "write_graph",
    "browse_commons",
    "read_commons_shape",
    "read_brain",
    "write_brain",
    "connect_compute",
    "source_channel",
    "read",
    "write",
    "edit",
    "bash",
)

# Model visibility is independent of the signed backend grant. Keep the current
# surface until the coordinated starter cutover can install guidance in existing
# centers; shrinking this tuple must never narrow ta's backend authority.
SERVED_ENGINE_MCP_TOOLS = BACKEND_ENGINE_CAPABILITIES
FOUR_MODEL_TOOLS = ("read", "write", "edit", "bash")

#: ``tools_allowed`` entries that make a prompt node an agent node rather than
#: naming a tool. ``universe_self`` is the original spelling (#3836).
AGENT_NODE_MARKERS = frozenset({"agent", "universe_self"})


def node_tool_grant(tools_allowed) -> tuple[str, ...] | None:
    """An agent node's grant: ``None`` (everything served) or exactly its list.

    The owner narrows by listing tools; a name that is not served refuses rather
    than silently narrowing to less than the owner meant.
    """
    named = [t for t in (tools_allowed or []) if t not in AGENT_NODE_MARKERS]
    unknown = sorted(set(named) - set(BACKEND_ENGINE_CAPABILITIES))
    if unknown:
        raise ValueError(
            f"agent node grants tools that are not served: {unknown}; "
            f"backend capabilities are {list(BACKEND_ENGINE_CAPABILITIES)}"
        )
    if not named:
        return None
    return tuple(t for t in BACKEND_ENGINE_CAPABILITIES if t in named)


def granted_tools(config) -> tuple[str, ...]:
    """Backend authority for one turn, independent of model-visible tools."""
    grant = getattr(config, "engine_tool_grant", None)
    if grant is None:
        return BACKEND_ENGINE_CAPABILITIES
    return tuple(t for t in BACKEND_ENGINE_CAPABILITIES if t in grant)


def model_tools(config) -> tuple[str, ...]:
    """Only model-visible handles that this turn's backend grant permits."""
    granted = set(granted_tools(config))
    return tuple(t for t in SERVED_ENGINE_MCP_TOOLS if t in granted)


#: The engine server's environment variable holding the key that signs launch grants.
LAUNCH_GRANT_KEY_ENV = "TINYASSETS_ENGINE_MCP_GRANT_KEY"


def _launch_grant_mac(key: str, session_key: str, turn: str, names: str) -> str:
    import hashlib
    import hmac
    import json

    return hmac.new(key.encode(), json.dumps([session_key, turn, names]).encode(),
                    hashlib.sha256).hexdigest()


def launch_grant(key: str, session_key: str, turn: str, tools) -> str:
    """The platform's signed statement of one launch's served tools, or "".

    The launcher takes ``tools`` from ``granted_tools(config)`` and the key from
    its verified engine route. The grant is bound to the launch's session and
    turn; the key is one engine server's, so one owner's and one universe's.
    """
    if not key:
        return ""
    names = ",".join(t for t in BACKEND_ENGINE_CAPABILITIES if t in set(tools))
    return f"{names}.{_launch_grant_mac(key, session_key, turn, names)}"


def verified_launch_grant(key: str, session_key: str, turn: str,
                          grant: str) -> tuple[str, ...] | None:
    """The tools a launch's signed grant names; ``None`` unless the platform signed it.

    No key, no grant, another launch's grant and an edited one are all ``None``:
    nothing on the route is authority until the signature binds it to this launch.
    """
    import hmac

    names, dot, mac = str(grant or "").rpartition(".")
    if not key or not dot or not hmac.compare_digest(
        mac.encode(), _launch_grant_mac(key, session_key, turn, names).encode(),
    ):
        return None
    return tuple(t for t in BACKEND_ENGINE_CAPABILITIES if t in set(names.split(",")))


def connections_granted(tools) -> bool:
    """Whether a grant already reaches the owner's connections.

    Before ``ta`` an agent reached a connection only by building an effect node
    (``write_graph``) and running it (``run_graph``). ``ta`` calls a connection
    directly only for a grant holding both, so it adds no reach.
    """
    return {"write_graph", "run_graph"} <= set(tools)


# Explicit reviewed authority boundary. A future connector write action must not
# become agent-callable merely because it is added to the canonical adapter.
SERVED_AUTOMATION_WRITE_OPERATIONS = frozenset({"create", "pause", "resume", "delete"})

# The inbound-webhook operations the served agent may perform. Listing is a read
# (``read_graph target="webhooks"``); Source create/revoke stays connector-only.
SERVED_WEBHOOK_WRITE_OPERATIONS = frozenset({"create", "revoke"})
