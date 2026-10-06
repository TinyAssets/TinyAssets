"""ta adapter for the one-extension lifecycle; no execution or credential grants."""
from __future__ import annotations

import base64
import binascii

from tinyassets.extension_manifest import KINDS, ExtensionError
from tinyassets.extension_state import ExtensionStore
from tinyassets.harness_settings import read_settings


def _schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required),
            "additionalProperties": False}


_TEXT = {"type": "string"}
_PIN = {"name": _TEXT, "revision": _TEXT,
        "expected_generation": {"type": "integer", "minimum": 0}}
LIFECYCLE = [
    {"name": "extension:help", "description": "Read the extension authoring handbook",
     "arguments": _schema({})},
    {"name": "extension:install", "description": "Install inert extension bytes; no grants",
     "arguments": _schema({"files": {"type": "object", "additionalProperties": _TEXT}}, ["files"])},
    {"name": "extension:list", "description": "List this agent's extension revisions",
     "arguments": _schema({})},
    *[{"name": f"extension:{action}",
       "description": f"{action.title()} an exact extension revision",
       "arguments": _schema(_PIN, _PIN)} for action in ("activate", "revoke")],
]

HANDBOOK = """Author one extension.json with schema_version: 2 and a name.
Optional lists: tools, commands, hooks, cards, connections, mcp_servers.
Tools/commands: name, description, arguments (JSON Schema); supply one package
executable, a relative file with a shebang. It receives entry name and JSON
arguments as argv[1:3] and writes one JSON result to stdout. Hooks use the same
entry contract plus event: input, turn_start, context, before_tool, after_tool,
turn_end. Hooks can currently be invoked explicitly through ta; automatic turn
events are not wired. Cards: name, description, asset (relative HTML file).
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

Cards/UI projection and MCP admission are currently unavailable, not connected.
Connection requirement calls describe unmet needs and create no grants.
Package-specific credentials and persistent stdio need U1 admission. Existing
platform/connection tools keep their own live authority and owner approval rules.
Share extension files using existing command-center publishing/install consent;
recipients install and activate their own copy with their own local authority.
Never include credentials or private test responses in shared files.
"""


class ExtensionCapabilities:
    def __init__(self, backend):
        self.backend = backend
        ctx = backend.context
        self.store = ExtensionStore(backend.root.parent, owner=ctx.owner,
                                    universe=ctx.universe, agent=ctx.initiating_agent)

    def _authority(self):
        ctx = self.backend.context
        if (ctx.research or ctx.delegated_authority != "serving-owner"
                or ctx.approval_id is not None or self.backend.check_authority()):
            raise ExtensionError("extension lifecycle requires current serving-owner authority")

    def _current(self):
        return set(self.backend.platform) | set(self.backend.connections())

    def materialize(self, directory):
        """Trusted private staging, mounted read-only for exactly one bash launch."""
        self._authority()
        self.backend.extension_mounts = set()
        directory.mkdir()
        for state in self.store.list():
            if state["state"] != "active" or not self._enabled(state["name"]):
                continue
            revision = self.store.load(state["name"], state["revision"])
            _, files = revision.content()
            root = directory / state["name"] / state["revision"]
            for path, data in files.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o555)
            self.backend.extension_mounts.add(
                (state["name"], state["revision"], state["generation"]))

    def _mounted(self, state):
        return (state["name"], state["revision"], state["generation"]) in getattr(
            self.backend, "extension_mounts", set())

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
            if state["state"] != "active" or not self._enabled(state["name"]):
                continue
            doc, _ = self.store.load(state["name"], state["revision"]).content()
            for kind in KINDS:
                for row in doc.get(kind, []):
                    entries.append({
                        "name": self._key(state, kind, row["name"]),
                        "description": row["description"],
                        "arguments": row.get("arguments", _schema({})),
                        "kind": kind, "revision": state["revision"],
                        "generation": state["generation"],
                        "availability": ("requirement" if kind == "connections" else
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
                return {"handbook": HANDBOOK}
            if name == "extension:list":
                return {"extensions": self.store.list()}
            if name == "extension:install":
                try:
                    files = {path: base64.b64decode(data, validate=True)
                             for path, data in arguments["files"].items()}
                except (ValueError, binascii.Error):
                    raise ExtensionError("files must contain base64 bytes") from None
                return self.store.install(files)
            return self.store.transition(
                arguments["name"], arguments["revision"],
                expected_generation=arguments["expected_generation"],
                active=name == "extension:activate", ceiling=sorted(self._current()),
            )
        # Resolve from daemon state, never trust revision/generation claims from the client.
        for state in self.store.list():
            if state["state"] != "active" or not self._enabled(state["name"]):
                continue
            doc, _ = self.store.load(state["name"], state["revision"]).content()
            for kind in KINDS:
                for row in doc.get(kind, []):
                    if self._key(state, kind, row["name"]) != name:
                        continue
                    current = self._current()
                    ceiling = self.store.active(
                        state["name"], state["revision"], state["generation"],
                        current_capabilities=current)
                    if kind in {"tools", "commands", "hooks"} and self._mounted(state):
                        if current - ceiling:
                            raise ExtensionError("launch authority exceeds activation ceiling")
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
                        return {"slot": row["name"], "verbs": row["verbs"],
                                "state": "binding_required", "grants_created": False}
                    return {"error": "extension_runtime_unavailable", "kind": kind,
                            "revision": state["revision"],
                            "detail": "Installed metadata; contribution runtime is not admitted"}
        raise ExtensionError("unknown or inactive extension capability")
