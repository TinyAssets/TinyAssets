"""The one agent definition: an ``agents/<name>/`` directory of editable files.

Founder rule 2026-10-06: one definition supplies an agent's tools,
instructions and capabilities to every provider and every agent. A provider
adapter only renders it into its own launch -- flags, config, a JSON-RPC
protocol, a model catalog -- and must leave the model-visible tools,
instructions and capabilities exactly as defined here.
``tests/test_one_agent_definition.py`` checks every registered provider kind.

An agent IS a directory (C8). The main agent is the command center root; every
other agent of the owner is ``agents/<name>/`` beside it, holding the same
harness files a package lands there: ``AGENTS.md`` (its instructions),
``settings.yaml`` (behaviour, never authority), ``skills/``, ``extensions/``,
``MEMORY.md``. No table, catalogue or editor stands beside these files; the
owner's agent edits them with the four tools, and a package installs them.

``settings.yaml`` may name the agent::

    schema_version: 1
    agent:
      id: agent_binding_01J...   # durable address; defaults to the directory name
      name: Evidence Weaver      # shown in the roster; defaults to the directory name
      owner: user_01H...         # the one account that may address it; absent
                                 # means every admin of the command center
      retired: false             # a retired agent answers nothing and is not listed

The id is the thread key (``addressed_agents.memory_session``) and what every
activity, wake and request records, so an agent moved from the retired SQL
binding store keeps answering under the id it always had. ``owner`` keeps that
store's rule: a binding was its creator's alone, so on a shared command center
a co-admin's migrated agent is neither listed for nor answerable by anyone
else; the loader takes the caller and returns only what the caller may address.

The tools are the engine route's own registrations, discovered from the
owner-pinned route the turn is granted (``engine_tool_client``), so their
names, descriptions and schemas have one source. Every model-visible tool
executes on that route, which is where the per-tool fences live (owner
steering, the activity yield/pause/stop fence).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from tinyassets.boxes.provider import BoxError, BoxPathError
from tinyassets.universe_files import (
    list_universe_dir,
    list_universe_entries,
    load_untrusted_yaml,
    read_universe_file,
    read_universe_text,
)

#: What every agent can do, whichever provider runs it.
#:
#: ``engine_route_tools`` -- every model-visible tool executes on the owner's
#: engine route; the provider exposes no tool of its own.
#: ``activities`` -- the agent may run an activity (a background run); the
#: route refuses its tools once the activity yields, pauses or stops.
#: ``owner_steering`` -- owner messages sent mid-turn reach the agent with its
#: next tool result (the route's ``OwnerSteering``).
AGENT_CAPABILITIES = frozenset({"engine_route_tools", "activities", "owner_steering"})

MAIN_AGENT = "main"
AGENTS_DIR = "agents"
INSTRUCTIONS_FILE = "AGENTS.md"
SETTINGS_FILE = "settings.yaml"
MAX_SETTINGS_BYTES = 64 * 1024
MAX_INSTRUCTIONS_BYTES = 256 * 1024
#: One path component, never ``main`` and never something a key could misread.
AGENT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    input_schema: dict[str, Any]

    def key(self) -> str:
        """Canonical form: equal keys are the same model-visible tool."""
        return json.dumps([self.name, self.description, self.input_schema],
                          sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    tools: tuple[AgentTool, ...]
    instructions: str
    capabilities: frozenset[str] = AGENT_CAPABILITIES

    def tool_keys(self) -> tuple[str, ...]:
        return tuple(sorted(tool.key() for tool in self.tools))


def agent_tool(tool) -> AgentTool:
    """An MCP ``Tool`` (as the engine route lists it) as an :class:`AgentTool`."""
    return AgentTool(tool.name, tool.description or "", dict(tool.inputSchema or {}))


def agent_definition(tools, instructions: str) -> AgentDefinition:
    """The definition for one turn: the route's granted tools and its instructions."""
    return AgentDefinition(tuple(agent_tool(tool) for tool in tools), instructions or "")


# --------------------------------------------------------------------------- #
# settings.yaml: versioned turn settings. A snapshot describes behaviour, never
# authority.
# --------------------------------------------------------------------------- #


class SettingsError(ValueError):
    """Owner-visible configuration failure; callers must not use defaults."""


class LocalModelBindingRequired(SettingsError):
    """A legacy logical model need must be bound by the current owner."""


class _UniqueLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise SettingsError("settings keys must be unique strings")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _object(value, fields, where):
    if not isinstance(value, dict) or set(value) - set(fields):
        raise SettingsError(f"invalid {where} fields")
    return value


def _text(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or not value.isprintable()):
        raise SettingsError("settings identifiers must be nonempty printable strings")
    return value


def _relative(value):
    value = _text(value)
    if ("\\" in value or ":" in value
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise SettingsError("settings identifier must be a relative path without links")
    return value


def _selection(doc, field, key, *, paths=False):
    if field not in doc:
        return None
    item = _object(doc[field], {key}, field)
    values = item.get(key)
    if not isinstance(values, list):
        raise SettingsError(f"{field}.{key} must be a list")
    values = tuple((_relative if paths else _text)(v) for v in values)
    if len(set(values)) != len(values):
        raise SettingsError(f"duplicate {field} selection")
    return values


@dataclass(frozen=True)
class ModelBinding:
    connection: str | None
    id: str
    effort: str | None = None


@dataclass(frozen=True)
class LoopSettings:
    reserve_tokens: int | None = None
    trigger_tokens: int | None = None
    attempts: int | None = None
    backoff_seconds: float | None = None


@dataclass(frozen=True)
class AgentIdentity:
    """The ``agent:`` block: who this directory is, for the roster and the thread key."""

    id: str | None = None
    name: str | None = None
    #: The one account that may address this agent; None is every admin of
    #: the command center.
    owner: str | None = None
    retired: bool = False
    #: Private operational configuration carried over from the retired binding
    #: store; the platform reads nothing from it.
    configuration: dict[str, Any] | None = None


@dataclass(frozen=True)
class SettingsSnapshot:
    raw: bytes | None
    revision: str
    model: ModelBinding | None = None
    legacy_model: str | None = None
    tools: tuple[str, ...] | None = None
    skills: tuple[str, ...] | None = None
    extensions: tuple[str, ...] | None = None
    starter_hooks: bool = True
    loop: LoopSettings = LoopSettings()
    agent: AgentIdentity = AgentIdentity()

    def model_choice(self, explicit: dict | None = None) -> dict | None:
        """Adapt to the existing owner model planner; it checks local authority."""
        if explicit is not None:
            return explicit
        if self.legacy_model is not None:
            raise LocalModelBindingRequired("settings model needs a local connection binding")
        if self.model is None:
            return None
        if self.model.connection is None:
            raise LocalModelBindingRequired("settings model needs a local connection binding")
        from tinyassets.providers.model_policy import ModelRef
        from tinyassets.providers.model_preferences import ModelEffort, ModelPreferences

        ref = ModelRef(self.model.connection, self.model.id)
        efforts = (ModelEffort(ref, self.model.effort),) if self.model.effort else ()
        return ModelPreferences("explicit", ref, (), efforts).document()


def _agent_block(doc) -> AgentIdentity:
    if "agent" not in doc:
        return AgentIdentity()
    item = _object(doc["agent"], {"id", "name", "owner", "retired", "configuration"}, "agent")
    identity = None
    if "id" in item:
        identity = _text(item["id"])
        if not AGENT_NAME.fullmatch(identity) or identity == MAIN_AGENT:
            raise SettingsError("agent.id must be one path-safe name other than main")
    retired = item.get("retired", False)
    if type(retired) is not bool:
        raise SettingsError("agent.retired must be a boolean")
    configuration = item.get("configuration")
    if configuration is not None and not isinstance(configuration, dict):
        raise SettingsError("agent.configuration must be a mapping")
    return AgentIdentity(identity, _text(item["name"]) if "name" in item else None,
                         _text(item["owner"]) if "owner" in item else None,
                         retired, configuration)


def parse_settings(raw: bytes | None) -> SettingsSnapshot:
    """Absent alone means defaults. Invalid supplied bytes always fail visibly."""
    if raw is None:
        return SettingsSnapshot(None, "absent")
    try:
        text = raw.decode("utf-8")
        # Apply the shared byte/alias/depth bound before constructing mappings.
        load_untrusted_yaml(text, max_bytes=MAX_SETTINGS_BYTES)
        doc = yaml.load(text, Loader=_UniqueLoader)
    except (OSError, ValueError, yaml.YAMLError, RecursionError) as exc:
        raise SettingsError("invalid settings YAML") from exc
    doc = _object(doc, {"schema_version", "model", "tools", "skills", "extensions",
                        "starter", "loop", "agent"}, "settings")
    legacy = isinstance(doc.get("model"), str) and "schema_version" not in doc
    if legacy:
        if set(doc) != {"model"}:
            raise SettingsError("legacy settings support only a logical model need")
    elif type(doc.get("schema_version")) is not int or doc["schema_version"] != 1:
        raise SettingsError("settings require schema_version: 1")
    model = None
    if "model" in doc and not legacy:
        item = _object(doc["model"], {"connection", "id", "effort"}, "model")
        model = ModelBinding(_text(item["connection"]) if "connection" in item else None,
                             _text(item.get("id")),
                             _text(item["effort"]) if "effort" in item else None)
    hooks = True
    if "starter" in doc:
        item = _object(doc["starter"], {"hooks"}, "starter")
        hooks = item.get("hooks", True)
        if type(hooks) is not bool:
            raise SettingsError("starter.hooks must be a boolean")
    loop = {}
    if "loop" in doc:
        item = _object(doc["loop"], {"compaction", "retry"}, "loop")
        for group, fields in (("compaction", {"reserve_tokens", "trigger_tokens"}),
                              ("retry", {"attempts", "backoff_seconds"})):
            if group not in item:
                continue
            for key, value in _object(item[group], fields, f"loop.{group}").items():
                types = (int, float) if key == "backoff_seconds" else (int,)
                if type(value) not in types or value < 0:
                    raise SettingsError(f"invalid loop.{group}.{key}")
                try:
                    finite = math.isfinite(value)
                except OverflowError:
                    finite = False
                if not finite:
                    raise SettingsError(f"invalid loop.{group}.{key}")
                loop[key] = value
    return SettingsSnapshot(
        raw, hashlib.sha256(raw).hexdigest(), model,
        _text(doc["model"]) if legacy else None,
        _selection(doc, "tools", "allow"),
        _selection(doc, "skills", "enabled", paths=True),
        _selection(doc, "extensions", "enabled", paths=True), hooks, LoopSettings(**loop),
        _agent_block(doc) if not legacy else AgentIdentity(),
    )


def agent_directory(agent_slug: str) -> str:
    """``agents/<slug>`` for one checked directory name."""
    slug = _relative(agent_slug)
    if "/" in slug or not AGENT_NAME.fullmatch(slug) or slug == MAIN_AGENT:
        raise SettingsError("agent name must be one path component other than main")
    return f"{AGENTS_DIR}/{slug}"


def read_settings(universe_dir: Path, *, agent_slug: str | None = None) -> SettingsSnapshot:
    """Read only under a trusted launch root; callers resolve the roster slug."""
    path = SETTINGS_FILE
    if agent_slug is not None:
        path = f"{agent_directory(agent_slug)}/{SETTINGS_FILE}"
    try:
        raw = read_universe_file(universe_dir, path, max_bytes=MAX_SETTINGS_BYTES)
    except FileNotFoundError:
        raw = None
    except OSError as exc:
        raise SettingsError("cannot read bound agent settings") from exc
    return parse_settings(raw)


def package_settings(raw: bytes) -> bytes:
    """Export behaviour and logical model needs, never source connection handles."""
    snapshot = parse_settings(raw)
    if snapshot.model is None or snapshot.model.connection is None:
        return raw
    doc = yaml.load(raw.decode("utf-8"), Loader=_UniqueLoader)
    del doc["model"]["connection"]
    return yaml.safe_dump(doc, sort_keys=False).encode("utf-8")


# --------------------------------------------------------------------------- #
# The owner's agents, read from their directories. Never from a table.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AgentFile:
    """One agent of the command center, as its files describe it."""

    #: Directory name under ``agents/``; ``main`` for the command center root.
    slug: str
    #: Durable address: ``settings.agent.id`` or the directory name.
    agent_id: str
    name: str
    instructions: str
    settings: SettingsSnapshot
    retired: bool = False
    #: ``settings.agent.owner``: the one account that may address this agent;
    #: None is every admin of the command center.
    owner: str | None = None

    @property
    def directory(self) -> str:
        return "" if self.slug == MAIN_AGENT else f"{AGENTS_DIR}/{self.slug}"

    def addressable_by(self, owner: str) -> bool:
        return self.owner is None or self.owner == owner


def _instructions(universe_dir: Path, directory: str) -> str:
    path = f"{directory}/{INSTRUCTIONS_FILE}" if directory else INSTRUCTIONS_FILE
    try:
        return read_universe_text(universe_dir, path, max_bytes=MAX_INSTRUCTIONS_BYTES)
    except FileNotFoundError:
        return ""


def _agent_file(universe_dir: Path, slug: str) -> AgentFile:
    directory = agent_directory(slug)
    settings, instructions = _agent_parts(universe_dir, directory)
    identity = settings.agent
    return AgentFile(
        slug=slug, agent_id=identity.id or slug, name=identity.name or slug,
        instructions=instructions,
        settings=settings, retired=identity.retired, owner=identity.owner,
    )


def _agent_parts(universe_dir: Path, directory: str) -> tuple[SettingsSnapshot, str]:
    """Parse the canonical agent files from one admitted box generation."""
    from tinyassets.control_plane.binding import file_binding

    binding = file_binding(universe_dir)
    if binding is None:
        slug = directory.rsplit("/", 1)[-1] if directory else None
        return read_settings(universe_dir, agent_slug=slug), _instructions(universe_dir, directory)
    prefix = f"{directory}/" if directory else ""
    settings_path, instructions_path = prefix + SETTINGS_FILE, prefix + INSTRUCTIONS_FILE
    snapshot = binding.provider.read_many(
        binding.handle, (settings_path, instructions_path),
        max_total=MAX_SETTINGS_BYTES + MAX_INSTRUCTIONS_BYTES,
    )
    binding.check_read(snapshot.generation)
    if set(snapshot.files) | set(snapshot.missing) != {settings_path, instructions_path}:
        raise SettingsError("incomplete agent file snapshot")
    if set(snapshot.files) & set(snapshot.missing):
        raise SettingsError("ambiguous agent file snapshot")
    raw_settings = snapshot.files.get(settings_path)
    raw_instructions = snapshot.files.get(instructions_path)
    if ((raw_settings is not None and
         (not isinstance(raw_settings, bytes) or len(raw_settings) > MAX_SETTINGS_BYTES))
            or (raw_instructions is not None and
                (not isinstance(raw_instructions, bytes) or
                 len(raw_instructions) > MAX_INSTRUCTIONS_BYTES))):
        raise SettingsError("agent files exceed their byte bounds")
    settings = parse_settings(raw_settings)
    instructions = "" if raw_instructions is None else raw_instructions.decode("utf-8")
    return settings, instructions.replace("\r\n", "\n").replace("\r", "\n")


def list_agent_files(universe_dir: Path, *, owner: str,
                     include_retired: bool = False) -> list[AgentFile]:
    """Every ``agents/<name>/`` directory that is an agent ``owner`` may address.

    By name; never ``main``. An agent whose ``settings.agent.owner`` names
    another account is not listed (``AgentFile.addressable_by``). A directory
    with neither ``AGENTS.md`` nor ``settings.yaml`` is not an agent (a package
    may land other files there). A malformed settings file fails visibly: an
    agent whose settings cannot be read must not answer with defaults.
    """
    universe_dir = Path(universe_dir)
    try:
        entries = list_universe_entries(universe_dir, AGENTS_DIR)
    except FileNotFoundError:
        return []
    found = []
    for slug, info in entries:
        if (not stat.S_ISDIR(info.st_mode) or not AGENT_NAME.fullmatch(slug)
                or slug == MAIN_AGENT):
            continue
        directory = agent_directory(slug)
        try:
            entries = set(list_universe_dir(universe_dir, directory))
        except BoxPathError:
            continue  # A file or a link named like an agent is not one.
        except BoxError:
            raise
        except OSError:
            continue
        if not entries & {INSTRUCTIONS_FILE, SETTINGS_FILE}:
            continue
        agent = _agent_file(universe_dir, slug)
        if (agent.retired and not include_retired) or not agent.addressable_by(owner):
            continue
        found.append(agent)
    return found


def load_agent(universe_dir: Path, agent_ref: str, *, owner: str,
               include_retired: bool = False) -> AgentFile:
    """The agent ``agent_ref`` names for ``owner``: ``main``, a directory name or
    an ``agent.id``.

    ``owner`` is the account the caller already admitted to this command
    center. Raises ``LookupError`` when no agent of theirs answers to the
    reference (an unknown name, another account's agent: the message never
    says which), and when two directories claim the same id (an address must
    have exactly one reading).
    """
    universe_dir = Path(universe_dir)
    ref = str(agent_ref or "").strip() or MAIN_AGENT
    if ref == MAIN_AGENT:
        settings, instructions = _agent_parts(universe_dir, "")
        return AgentFile(MAIN_AGENT, MAIN_AGENT, MAIN_AGENT,
                         instructions, settings)
    matches = [agent for agent in list_agent_files(universe_dir, owner=owner,
                                                   include_retired=include_retired)
               if ref in (agent.slug, agent.agent_id)]
    if not matches:
        raise LookupError(f"no agent named {ref!r}")
    if len(matches) > 1:
        raise LookupError(f"agent {ref!r} is claimed by more than one directory")
    return matches[0]
