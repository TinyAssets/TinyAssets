"""ta adapter for the one-extension lifecycle; no execution or credential grants."""
from __future__ import annotations

import base64
import binascii
import contextlib
import contextvars

from tinyassets.extension_manifest import KINDS, ExtensionError
from tinyassets.extension_state import ExtensionStore
from tinyassets.harness_settings import read_settings

_MOUNTS = contextvars.ContextVar("extension_mounts", default=frozenset())
#: Revision blobs one remote launch may receive; leaves room in ta's 8 MiB reply.
REMOTE_BUNDLE_BYTES = 4 * 1024 * 1024


@contextlib.contextmanager
def delivered(mounts):
    """Revisions a trusted remote-box host verified and delivered for this launch."""
    token = _MOUNTS.set(frozenset(mounts))
    try:
        yield
    finally:
        _MOUNTS.reset(token)


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


_TEXT = {"type": "string"}
_PIN = {"name": _TEXT, "revision": _TEXT,
        "expected_generation": {"type": "integer", "minimum": 0}}
_BINDINGS = {"type": "object", "additionalProperties": _schema(
    {"connection_id": _TEXT, "grant_id": _TEXT}, ["connection_id", "grant_id"])}
_MCP_ARGS = _schema({"action": {"enum": ["discover", "call"]}, "tool": _TEXT,
                     "arguments": {"type": "object"}, "catalog_hash": _TEXT}, ["action"])
LIFECYCLE = [
    {"name": "extension:help",
     "description": "Read the extension handbook: tools, hooks, UI cards, remote MCP servers",
     "arguments": _schema({"chapter": {"enum": ["overview", "ui"]}})},
    {"name": "extension:install",
     "description": ("Install an extension to add tools, hooks, cards, or connect a remote "
                     "MCP server by its URL; inert until activated, no grants"),
     "arguments": _schema({"files": {"type": "object", "additionalProperties": _TEXT}}, ["files"])},
    {"name": "extension:events", "description": "Read recent hook failures or skipped events",
     "arguments": _schema({})},
    {"name": "extension:list", "description": "List this agent's extension revisions",
     "arguments": _schema({})},
    *[{"name": f"extension:{action}",
       "description": f"{action.title()} an exact extension revision",
       "arguments": _schema({**_PIN, **({"bindings": _BINDINGS}
                                      if action == "activate" else {})}, _PIN)}
      for action in ("activate", "revoke")],
]

HANDBOOK = """Author one extension.json with schema_version: 2 and a name.
Optional lists: tools, commands, hooks, cards, connections, mcp_servers.
Tools/commands: name, description, arguments (JSON Schema); supply one package
executable, a relative file with a shebang. It receives entry name and JSON
arguments as argv[1:3] and writes one JSON result to stdout. Hooks use the same
entry contract plus event: input, turn_start, context, before_tool, after_tool,
turn_end. These events execute automatically with the triggering turn's current
bash grant, in the existing jail. Failures/skips are visible through
extension:events; observational hooks never cause completed effects to retry.
Hooks observe bounded version/event/payload
JSON and cannot widen authority. Stop does not launch a new hook. Cards: name,
description, asset (relative HTML or app_ui JSON file); read chapter ui.
Connection needs: name (logical slot), description, verbs (e.g. ["GET"]).
MCP: name, description, transport remote with url and optional declared slot,
or transport stdio with executable and args. No credentials/env/grants in manifests.

Install: ta extension:install --json '{"files":{"extension.json":"<base64>",
"run":"<base64>","helper":"<base64>"}}'. Include ALL package files. This is
inert installation into the private package store, charged to your storage.
List: ta extension:list --json '{}'. Activate: ta extension:activate --json
'{"name":"example","revision":"<digest>","expected_generation":0}'.
Use the generation from list for subsequent updates/revoke. Activation replaces
the active revision atomically and does not create or widen any permission.
Start a NEW bash invocation after activation to mount the installed bytes.
Use ta search extension: and ta describe <name> for exact revision-qualified calls.
Files are read-only under /ta/extensions; editing workspace files requires a
new install and explicit activation. settings.yaml extensions.enabled may narrow
the active set but cannot activate anything. Revoke fences new ta dispatch;
pre-U1 code already running in the same bash launch is not forcibly terminated.

Cards project through app_ui on activation and disappear on revoke.
Only stdio/package-cell admission remains unavailable pending U1.
Activate may include bindings: {"slot":{"connection_id":"...","grant_id":"..."}}.
Bindings use existing local grants only, pin their incarnation and never create
or widen grants. A new activation generation is required to change a binding.
A remote MCP slot binds an http connection the owner approved for the URL's
host and path with POST; for a keyless server that is a connect ask with
auth_scheme "none" (skills/connect, step 4). It then stays in ta search.
Remote MCP contributions accept {"action":"discover"}, returning tools and a
catalog_hash; call with {"action":"call","tool":"name","arguments":{},
"catalog_hash":"..."}. Every exchange uses existing connection effect policy,
OAuth custody and endpoint restrictions. Session headers stay outside the box.
Unknown outcomes are never replayed. Unbound slots report binding_required.
Package-specific credentials and persistent stdio need U1 admission. Existing
platform/connection tools keep their own live authority and owner approval rules.
Share extension files using existing command-center publishing/install consent;
recipients install and activate their own copy with their own local authority.
Never include credentials or private test responses in shared files.
"""

UI_HANDBOOK = """Declare cards: [{name, description, asset}] in extension.json.
An HTML asset is body markup; script tags inside markup do not execute. For an
interactive card, use a .json asset containing a tinyassets.app-ui.v1 component:
{kind:'tinyassets.app-ui.v1', version:1, ui_id:'working', name:'My UI',
 markup:'<button>Go</button>', style:'', script:''}. Use JSON double quotes.
Activation assigns a revision/generation-specific ui_id and projects the bytes
into the existing app_ui library. Select it using the existing UI picker. The
component version is always FORMAT version 1, not an extension revision.
Revoke removes the projection and fences stale reads even if cleanup failed.
Working-file edits have no effect: install and activate a new extension revision.
Editing the projected backend row directly cannot change its pinned code.

Optional app_ui fields, assets, libraries and script_type retain the existing
renderer contract. Text is bounded to 1048576 UTF-8 bytes per component. UI code
runs in the existing isolated frame and uses its capability bridge; protected
owner approvals remain outside that frame. No grant or credential is in a card.
Static HTML keeps source bytes in the package; interactive scripts belong in the
JSON component's script field. Read_graph target=app_ui query=index lists UIs,
and query=<ui_id> reads one component. Runtime validation reports renderability.
Sharing carries package files, never activation, local bindings or credentials.
"""


class ExtensionCapabilities:
    def __init__(self, backend):
        self.backend = backend
        ctx = backend.context
        self.store = ExtensionStore(backend.root.parent, owner=ctx.owner,
                                    universe=ctx.universe, agent=ctx.initiating_agent)

    def _authority(self, *, lifecycle=False):
        from tinyassets.auth.middleware import current_identity_or_none

        identity = getattr(self.backend, "outside_identity", None) or current_identity_or_none()
        if identity is not None:
            from tinyassets.outside_authority import check_identity

            check_identity(identity)
        if (lifecycle and identity is not None
                and identity.metadata.get("outside_origin") is not None):
            raise ExtensionError("outside clients cannot mutate extension owner lifecycle")
        ctx = self.backend.context
        if (ctx.research or ctx.delegated_authority != "serving-owner"
                or ctx.approval_id is not None or self.backend.check_authority()):
            raise ExtensionError("extension lifecycle requires current serving-owner authority")

    def _current(self):
        return set(self.backend.platform) | set(self.backend.connections())

    def _bindings(self, name, revision, requested):
        from tinyassets.storage.outbound_connections import ConnectionLedger

        doc, _ = self.store.load(name, revision).content()
        slots = {row["name"]: row for row in doc.get("connections", [])}
        ledger = ConnectionLedger(self.backend.root.parent / "outbound.db")
        available = self.backend.connections()
        result = {}
        for slot, pin in requested.items():
            if slot not in slots:
                raise ExtensionError("unknown extension connection slot")
            for verb in slots[slot]["verbs"]:
                match = available.get(f"connection:{pin['connection_id']}:{verb}")
                if match is None or match[0].grant_id != pin["grant_id"]:
                    raise ExtensionError("connection binding exceeds current grant")
            incarnation = ledger.incarnation(pin["connection_id"])
            if not incarnation:
                raise ExtensionError("connection binding unavailable")
            result[slot] = {**pin, "incarnation": incarnation}
        return result

    def connection(self, state, row, verb=None):
        """Resolve only private, revision-bound local authority; never author credentials."""
        from tinyassets.storage.outbound_connections import ConnectionLedger

        pin = self.store.bindings(state).get(row["name"])
        if pin is None:
            return None
        ceiling = self.store.active(state["name"], state["revision"], state["generation"],
                                    current_capabilities=self._current())
        ledger = ConnectionLedger(self.backend.root.parent / "outbound.db")
        if ledger.incarnation(pin["connection_id"]) != pin["incarnation"]:
            raise ExtensionError("connection binding incarnation changed")
        available = self.backend.connections()
        for required in row["verbs"]:
            key = f"connection:{pin['connection_id']}:{required}"
            match = available.get(key)
            if key not in ceiling or match is None or match[0].grant_id != pin["grant_id"]:
                raise ExtensionError("connection binding authority unavailable")
        if verb is not None and verb not in row["verbs"]:
            raise ExtensionError("connection slot does not declare required verb")
        identity = getattr(self.backend, "outside_identity", None)
        if identity is not None:
            from tinyassets.outside_authority import check_identity

            for required in row["verbs"]:
                check_identity(identity, universe=self.backend.context.universe,
                               agent=self.backend.context.initiating_agent,
                               capability=f"connection:{pin['connection_id']}:{required}")
        return pin

    def deliverable(self):
        """Active, enabled revisions this live authority may run; never grants or bindings."""
        try:
            self._authority()
        except ExtensionError:
            return []  # No extension authority never grants a mount or breaks ordinary bash.
        found = []
        for state in self.store.list():
            if state["state"] != "active":
                continue
            try:
                if not self._enabled(state["name"]):
                    continue
                revision = self.store.load(state["name"], state["revision"])
                revision.content()
            except (ValueError, LookupError, OSError):
                continue  # Catalog reports the failure; lifecycle/revoke remain reachable.
            found.append((state, revision))
        return found

    def materialize(self, directory):
        """Trusted private staging, mounted read-only for exactly one bash launch."""
        _MOUNTS.set(frozenset())
        try:
            self._authority()
        except ExtensionError:
            return False
        mounted = set()
        directory.mkdir()
        for state, revision in self.deliverable():
            _, files = revision.content()
            root = directory / state["name"] / state["revision"]
            for path, data in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o555)
            mounted.add(
                (state["name"], state["revision"], state["generation"]))
        _MOUNTS.set(frozenset(mounted))
        return True

    def bundle(self, limit=REMOTE_BUNDLE_BYTES):
        """Content-addressed revision blobs for one remote bash launch.

        Only installed package bytes cross: no host path, binding, grant or
        credential. Revisions past the size bound stay undelivered, so their
        contributions remain runtime_unavailable (fail closed).
        """
        delivered, undelivered, used = [], [], 0
        for state, revision in self.deliverable():
            pin = {"name": state["name"], "revision": state["revision"],
                   "generation": state["generation"]}
            if used + len(revision.blob) > limit:
                undelivered.append({**pin, "reason": "remote_bundle_limit"})
                continue
            used += len(revision.blob)
            delivered.append({**pin, "blob": base64.b64encode(revision.blob).decode()})
        return {"extensions": delivered, "undelivered": undelivered}

    def _mounted(self, state):
        return (state["name"], state["revision"], state["generation"]) in _MOUNTS.get()

    def _enabled(self, name):
        ctx = self.backend.context
        slug = None
        if ctx.initiating_agent != "main":
            from tinyassets.addressed_agents import resolve

            agent = resolve(self.backend.root.parent, universe_id=ctx.universe,
                            owner=ctx.owner, agent_id=ctx.initiating_agent)
            if agent is None or agent.agent_slug is None:
                return True  # No installed directory; never guess one from a binding ID.
            slug = agent.agent_slug
        settings = read_settings(self.backend.root, agent_slug=slug)
        enabled = settings.extensions
        return enabled is None or name in enabled

    def catalog(self):
        self._authority()
        entries = list(LIFECYCLE)
        for state in self.store.list():
            if state["state"] != "active":
                continue
            try:
                if not self._enabled(state["name"]):
                    continue
                doc, _ = self.store.load(state["name"], state["revision"]).content()
            except (ValueError, LookupError, OSError) as exc:
                entries.append({"name": self._key(state, "diagnostic", "unavailable"),
                                "description": f"Extension unavailable: {type(exc).__name__}",
                                "arguments": _schema({}), "availability": "invalid"})
                continue
            for kind in KINDS:
                for row in doc.get(kind, []):
                    entries.append({
                        "name": self._key(state, kind, row["name"]),
                        "description": row["description"],
                        "arguments": (_MCP_ARGS if kind == "mcp_servers"
                                      and row["transport"] == "remote" else
                                      row.get("arguments", _schema({}))),
                        "kind": kind, "revision": state["revision"],
                        **({"event": row["event"]} if kind == "hooks" else {}),
                        "generation": state["generation"],
                        "availability": ("projected" if kind == "cards" else
                                         "remote" if kind == "mcp_servers"
                                         and row["transport"] == "remote" else
                                         "requirement" if kind == "connections" else
                                         "jailed" if kind in {"tools", "commands", "hooks"}
                                         and self._mounted(state) else "runtime_unavailable"),
                    })
        return entries

    @staticmethod
    def _key(state, kind, name):
        return f"extension:{state['name']}:{state['revision']}:{state['generation']}:{kind}:{name}"

    def call(self, name, arguments):
        from jsonschema import Draft202012Validator

        self._authority()
        for capability in LIFECYCLE:
            if capability["name"] != name:
                continue
            if not Draft202012Validator(capability["arguments"]).is_valid(arguments):
                raise ExtensionError("invalid extension lifecycle arguments")
            if name == "extension:help":
                return {"handbook": UI_HANDBOOK if arguments.get("chapter") == "ui" else HANDBOOK}
            if name == "extension:events":
                from tinyassets.extension_hooks import read_evidence

                return read_evidence(self.store)
            if name == "extension:list":
                return {"extensions": self.store.list()}
            self._authority(lifecycle=True)
            if name == "extension:install":
                try:
                    files = {path: base64.b64decode(data, validate=True)
                             for path, data in arguments["files"].items()}
                except (ValueError, binascii.Error):
                    raise ExtensionError("files must contain base64 bytes") from None
                return self.store.install(files)
            if name == "extension:activate":
                from tinyassets.extension_ui import component

                doc, content = self.store.load(arguments["name"], arguments["revision"]).content()
                for card in doc.get("cards", []):
                    component(arguments, card, content, self.store.identity)
            state = self.store.transition(
                arguments["name"], arguments["revision"],
                expected_generation=arguments["expected_generation"],
                active=name == "extension:activate", ceiling=sorted(self._current()),
                bindings=self._bindings(arguments["name"], arguments["revision"],
                                        arguments.get("bindings", {}))
                if name == "extension:activate" else {},
            )
            from tinyassets.extension_ui import project

            return {**state, "ui_ids": project(self, state)}
        # Resolve from daemon state, never trust revision/generation claims from the client.
        for state in self.store.list():
            if state["state"] != "active":
                continue
            prefix = f"extension:{state['name']}:{state['revision']}:{state['generation']}:"
            if not name.startswith(prefix):
                continue
            if not self._enabled(state["name"]):
                raise ExtensionError("extension disabled by settings")
            doc, _ = self.store.load(state["name"], state["revision"]).content()
            for kind in KINDS:
                for row in doc.get(kind, []):
                    if self._key(state, kind, row["name"]) != name:
                        continue
                    current = self._current()
                    self.store.active(
                        state["name"], state["revision"], state["generation"],
                        current_capabilities=current)
                    if kind in {"tools", "commands", "hooks"} and self._mounted(state):
                        if not Draft202012Validator(row["arguments"]).is_valid(arguments):
                            raise ExtensionError("invalid extension contribution arguments")
                        root = f"/ta/extensions/{state['name']}/{state['revision']}"
                        return {"extension_execution": {
                            "executable": f"{root}/{doc['executable']}", "cwd": root,
                            "entry": row["name"], "arguments": arguments,
                        }}
                    if kind == "connections":
                        if arguments:
                            raise ExtensionError("connection requirement takes no arguments")
                        pin = self.connection(state, row)
                        return {"slot": row["name"], "verbs": row["verbs"],
                                "state": "bound" if pin else "binding_required",
                                "grants_created": False}
                    if kind == "mcp_servers" and row["transport"] == "remote":
                        if not Draft202012Validator(_MCP_ARGS).is_valid(arguments):
                            raise ExtensionError("invalid remote MCP arguments")
                        from tinyassets.extension_remote import invoke

                        return invoke(self, state, doc, row, arguments)
                    if kind == "cards":
                        if arguments:
                            raise ExtensionError("UI contribution takes no arguments")
                        from tinyassets.extension_ui import component

                        _, content = self.store.load(state["name"], state["revision"]).content()
                        entry = component(state, row, content, self.store.identity)
                        return {"ui_id": entry["ui_id"], "state": "projected",
                                "revision": state["revision"], "generation": state["generation"]}
                    return {"error": "extension_runtime_unavailable", "kind": kind,
                            "revision": state["revision"],
                            "detail": "Installed metadata; contribution runtime is not admitted"}
        raise ExtensionError("unknown or inactive extension capability")
