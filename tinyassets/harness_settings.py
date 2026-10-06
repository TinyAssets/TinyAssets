"""Versioned turn settings. A snapshot describes behavior, never authority."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

import yaml

from tinyassets.universe_files import load_untrusted_yaml, read_universe_file

MAX_SETTINGS_BYTES = 64 * 1024


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
                        "starter", "loop"}, "settings")
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
    )


def read_settings(universe_dir: Path, *, agent_slug: str | None = None) -> SettingsSnapshot:
    """Read only under a trusted launch root; callers resolve the roster slug."""
    path = "settings.yaml"
    if agent_slug is not None:
        slug = _relative(agent_slug)
        if "/" in slug:
            raise SettingsError("agent slug must be one path component")
        path = f"agents/{slug}/settings.yaml"
    try:
        raw = read_universe_file(universe_dir, path, max_bytes=MAX_SETTINGS_BYTES)
    except FileNotFoundError:
        raw = None
    except OSError as exc:
        raise SettingsError("cannot read bound agent settings") from exc
    return parse_settings(raw)


def package_settings(raw: bytes) -> bytes:
    """Export behavior and logical model needs, never source connection handles."""
    snapshot = parse_settings(raw)
    if snapshot.model is None or snapshot.model.connection is None:
        return raw
    doc = yaml.load(raw.decode("utf-8"), Loader=_UniqueLoader)
    del doc["model"]["connection"]
    return yaml.safe_dump(doc, sort_keys=False).encode("utf-8")
