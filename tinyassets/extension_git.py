"""Revision-bound git grants, projected into a box command's environment."""
from contextlib import contextmanager
from dataclasses import replace

from tinyassets.extension_manifest import ExtensionError


@contextmanager
def for_launch(backend):
    """The ``TINYASSETS_BOX_GIT`` projection for the launch, or an empty mapping.

    The box's forwarder opens the broker git routes from it before the command
    forks (``boxes.git_bridge.prepare``); a binding, broker or grant refusal
    leaves the command with no authenticated git, never with the daemon's.
    """
    try:
        yield _environment_for_launch(backend)
    except (OSError, RuntimeError, LookupError, ValueError):
        yield {}


def _environment_for_launch(backend):
    if backend is None:
        return {}
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
        return {}
    from tinyassets.boxes.git_bridge import environment

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
    return environment(records, backend.context.initiating_agent)
