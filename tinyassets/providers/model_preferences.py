"""Versioned owner preferences, never a provider grant or actual-model receipt."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from tinyassets.providers.model_policy import Charge, ModelPolicy, ModelRef

MAX_FALLBACKS = 1024
MAX_GENERATION = 2**63 - 1
MAX_POLICY_BYTES = 4 * 1024 * 1024
#: Saved effort choices are kept per model so switching away and back does not
#: silently reset the setting, which bounds this by the owner's catalogue size.
MAX_EFFORTS = 1024
#: Documents written from now on. Version 1 remains READABLE: a stored row is
#: the owner's existing saved default, and refusing to parse it would hold the
#: picker rather than simply carrying no effort choice.
POLICY_VERSION = 2


def exact_generation(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_GENERATION:
        raise ValueError("invalid preference generation")
    return value


def _identifier(value: object, limit: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not value and not empty)
        or len(value) > limit
        or value != value.strip()
        or (value and not value.isprintable())
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise ValueError("invalid model preference identifier")
    return value


def _object(value: object, fields: set[str]) -> dict:
    if not isinstance(value, dict) or value.keys() != fields:
        raise ValueError("invalid model preference fields")
    return value


def _ref(value: object) -> ModelRef:
    doc = _object(value, {"provider_ref", "model_id"})
    return ModelRef(
        _identifier(doc["provider_ref"], 400),
        _identifier(doc["model_id"], 200, empty=True),
    )


def _effort(value: object) -> "ModelEffort":
    doc = _object(value, {"provider_ref", "model_id", "level"})
    return ModelEffort(
        ModelRef(
            _identifier(doc["provider_ref"], 400),
            _identifier(doc["model_id"], 200, empty=True),
        ),
        _identifier(doc["level"], 100),
    )


def _ref_doc(ref: ModelRef | None) -> dict[str, str] | None:
    if ref is None:
        return None
    return {"provider_ref": ref.connection_id, "model_id": ref.model_id}


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate model preference field")
        result[key] = value
    return result


def strict_json(raw: bytes | str) -> object:
    """Bounded JSON, including duplicate-key rejection at every nesting level."""
    if not isinstance(raw, (bytes, str)):
        raise ValueError("invalid model preferences JSON")
    try:
        size = len(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
        if size > MAX_POLICY_BYTES:
            raise ValueError("model preferences too large")
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        return json.loads(text, object_pairs_hook=_unique_object)
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("invalid model preferences JSON") from exc


@dataclass(frozen=True, slots=True)
class ModelEffort:
    """One model's saved reasoning/effort level, keyed by the model it applies to.

    Deliberately NOT a field on :class:`ModelRef`: a ref is an identity used as
    a dict key and set member throughout ordering, and folding effort into it
    would make one model at two levels read as two different models.

    The level is not checked against a vocabulary here. There is no shared one
    to check against -- Claude Code offers ``max`` and no ``minimal``, Codex the
    reverse, and a single model may drop a level its siblings have. The
    admissible set is whatever that model ADVERTISED, which this module cannot
    see; the plan that holds a catalogue enforces it.
    """

    ref: ModelRef
    level: str

    def __post_init__(self) -> None:
        if type(self.ref) is not ModelRef:
            raise ValueError("invalid model reference")
        _identifier(self.ref.connection_id, 400)
        _identifier(self.ref.model_id, 200, empty=True)
        _identifier(self.level, 100)


@dataclass(frozen=True, slots=True)
class ModelPreferences:
    mode: Literal["automatic", "explicit"]
    saved_default: ModelRef | None
    fallbacks: tuple[ModelRef, ...]
    efforts: tuple[ModelEffort, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in ("automatic", "explicit") or type(self.fallbacks) is not tuple:
            raise ValueError("invalid model preference mode or order")
        if len(self.fallbacks) > MAX_FALLBACKS:
            raise ValueError("too many model fallbacks")
        if type(self.efforts) is not tuple or len(self.efforts) > MAX_EFFORTS:
            raise ValueError("invalid saved effort choices")
        if any(type(item) is not ModelEffort for item in self.efforts):
            raise ValueError("invalid saved effort choice")
        if len({item.ref for item in self.efforts}) != len(self.efforts):
            raise ValueError("duplicate effort choice for one model")
        if self.mode == "automatic":
            if self.saved_default is not None or self.fallbacks:
                raise ValueError("automatic preferences cannot contain an explicit order")
        elif self.saved_default is None:
            raise ValueError("explicit preferences require a default")
        refs = (() if self.saved_default is None else (self.saved_default,)) + self.fallbacks
        for ref in refs:
            if type(ref) is not ModelRef:
                raise ValueError("invalid model reference")
            _identifier(ref.connection_id, 400)
            _identifier(ref.model_id, 200, empty=True)
        if len(set(refs)) != len(refs):
            raise ValueError("duplicate model preference reference")

    def effort_for(self, ref: ModelRef | None) -> str:
        """The saved level for exactly this model, or empty for none.

        Empty means "the executor's own default", never a level this platform
        picked. Kept per model and independent of mode, so an automatic plan
        still runs whichever model it chooses at the level the owner set for it.
        """
        if ref is None:
            return ""
        for item in self.efforts:
            if item.ref == ref:
                return item.level
        return ""

    @classmethod
    def from_document(cls, value: object) -> ModelPreferences:
        fields = {"version", "mode", "saved_default", "fallbacks"}
        if not isinstance(value, dict):
            raise ValueError("invalid model preference fields")
        version = value.get("version")
        if type(version) is not int or version not in (1, POLICY_VERSION):
            raise ValueError("unsupported model preference version")
        # Version 1 predates per-model effort and carries no such field; it
        # reads as "no effort choice saved", not as an unreadable row.
        if version == POLICY_VERSION:
            fields = fields | {"efforts"}
        doc = _object(value, fields)
        if not isinstance(doc["fallbacks"], list) or len(doc["fallbacks"]) > MAX_FALLBACKS:
            raise ValueError("invalid model fallback order")
        raw_efforts = doc.get("efforts", [])
        if not isinstance(raw_efforts, list) or len(raw_efforts) > MAX_EFFORTS:
            raise ValueError("invalid saved effort choices")
        return cls(
            mode=doc["mode"],
            saved_default=None if doc["saved_default"] is None else _ref(doc["saved_default"]),
            fallbacks=tuple(_ref(ref) for ref in doc["fallbacks"]),
            efforts=tuple(_effort(item) for item in raw_efforts),
        )

    def document(self) -> dict:
        return {
            "version": POLICY_VERSION,
            "mode": self.mode,
            "saved_default": _ref_doc(self.saved_default),
            "fallbacks": [_ref_doc(ref) for ref in self.fallbacks],
            "efforts": [
                {"provider_ref": item.ref.connection_id, "model_id": item.ref.model_id,
                 "level": item.level}
                for item in self.efforts
            ],
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.document(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )


def parse_preference_write(raw: bytes) -> tuple[int, ModelPreferences]:
    doc = _object(strict_json(raw), {"expected_generation", "policy"})
    return exact_generation(doc["expected_generation"]), ModelPreferences.from_document(
        doc["policy"]
    )


def capture_preference_policy(
    *, saved: ModelPreferences | None, observed_generation: int,
    current: ModelPreferences | None = None, ranking_source: str | None = None,
    cost_caps: tuple[Charge, ...] | None = None,
) -> tuple[ModelPolicy, str] | None:
    """Convert validated choices to one advisory plan, without reading or saving.

    The authenticated caller supplies observed storage state and trusted ranking/
    cost bounds. This neither discovers models nor grants execution authority.
    None preserves the legacy path when no choice exists. Current automatic is
    an override too: it clears the saved primary and tail for this turn only.
    """
    exact_generation(observed_generation)
    for value in (saved, current):
        if value is not None and type(value) is not ModelPreferences:
            raise ValueError("invalid model preferences")
    if (saved is None) != (observed_generation == 0):
        raise ValueError("preference generation does not match saved state")
    if saved is None and current is None:
        return None
    chosen = current if current is not None else saved
    return ModelPolicy(
        generation=observed_generation, mode=chosen.mode, fallbacks=chosen.fallbacks,
        current_selection=None if current is None else current.saved_default,
        saved_default=chosen.saved_default if current is None else None,
        ranking_source=ranking_source, cost_caps=cost_caps,
        # A per-turn override that names no effort must not silently clear the
        # owner's SAVED levels: switching model for one message is not a
        # decision about effort. Fall back to the stored map in that case.
        efforts=tuple(
            (item.ref, item.level)
            for item in (chosen.efforts or (saved.efforts if saved is not None else ()))
        ),
    ), "current" if current is not None else "saved"
