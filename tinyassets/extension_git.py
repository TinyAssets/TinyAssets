"""Revision-bound git routes on the existing owner broker and checking egress."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace

from tinyassets.extension_manifest import ExtensionError


@contextmanager
def for_launch(backend):
    with ExitStack() as stack:
        try:
            prefix = stack.enter_context(_routes_for_launch(backend))
        except (OSError, RuntimeError, LookupError, ValueError):
            prefix = ("printf '%s\\n' 'Authenticated git unavailable: extension binding, "
                      "broker or egress not admitted.' >&2; export GIT_TERMINAL_PROMPT=0; ")
        yield prefix


@contextmanager
def _routes_for_launch(backend):
    if backend is None:
        yield ""
        return
    from tinyassets.extension_capabilities import ExtensionCapabilities
    from tinyassets.storage.workspace_authority import parse_git_scope

    service = ExtensionCapabilities(backend)
    contributions = []
    for state in service.store.list():
        if state["state"] != "active" or not service._enabled(state["name"]):
            continue
        doc, _ = service.store.load(state["name"], state["revision"]).content()
        for row in doc.get("connections", []):
            scopes = tuple(v for v in row["verbs"] if parse_git_scope(v))
            if not scopes:
                continue
            pin = service.connection(state, row)
            if pin is not None:
                contributions.append((state, row, pin, scopes))
    if not contributions:
        yield ""
        return
    from tinyassets import universe_egress
    from tinyassets.git_egress import routes
    from tinyassets.storage.outbound_connections import _broker_channel

    def check():
        service._authority()
        for state, row, pin, _ in contributions:
            if not service._enabled(state["name"]) or service.connection(state, row) != pin:
                raise ExtensionError("extension git authority changed")

    check()
    records = []
    current = backend.connections()
    for _, _, pin, scopes in contributions:
        grant, view, _ = current[f"connection:{pin['connection_id']}:{scopes[0]}"]
        records.append((grant, replace(view, scopes=scopes), pin["incarnation"]))
    first = contributions[0][2]
    channel = _broker_channel(backend.root.parent, principal=backend.context.owner,
                              command_center=backend.context.universe,
                              grant_id=first["grant_id"], connection_id=first["connection_id"])
    if channel is None:
        raise ExtensionError("authenticated git requires the existing credential broker")
    proxy = universe_egress._PROXIES.get(str(backend.root.resolve()))
    if proxy is None:
        raise ExtensionError("authenticated git requires the checking egress proxy")
    with ExitStack() as stack:
        prefix = stack.enter_context(routes(proxy, channel._client, records,
                                            backend.context.initiating_agent, authority=check))
        yield prefix
