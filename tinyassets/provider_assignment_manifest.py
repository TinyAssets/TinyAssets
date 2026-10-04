"""Accepted connection/model scope stored with the existing serving assignment.

These records are not launch authority. Execution must also validate the current
root, binding, live custody, selected model, capabilities and permitted pricing.
Preference order never belongs in this manifest.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, fields

MODEL_ACCESS_FORMAT = (
    'model_access must map provider names to objects with exactly these fields: '
    'model_scope ("legacy", "explicit", or "discovered"), '
    'model_ids (a nonempty array of unique model IDs for explicit; [] otherwise), '
    'cost_caps (null for free-only, or a nonempty object mapping cost components '
    'to nonnegative integer ceilings). Use "" as the native provider-default ID. '
    'Example: {"provider-name":{"model_scope":"explicit",'
    '"model_ids":["gpt-6-astra"],"cost_caps":null}}. '
    'For auto-detect use model_scope="discovered", model_ids=[]. '
    'Preserve other accepted providers and existing cost_caps.'
)


def _text(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 500
        and value.isprintable()
        and value == value.strip()
    )


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelAccess:
    model_scope: str = "legacy"
    model_ids: tuple[str, ...] = ()
    # None means free-only. Units are part of each component identifier.
    cost_caps: tuple[tuple[str, int], ...] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_scope, str) or self.model_scope not in {
            "legacy",
            "explicit",
            "discovered",
        }:
            raise ValueError("unknown model scope")
        if type(self.model_ids) is not tuple or any(
            not isinstance(model, str)
            or (model != "" and (not _text(model) or len(model) > 200 or model != model.strip()))
            for model in self.model_ids
        ):
            raise ValueError("invalid explicit model identifiers")
        if len(set(self.model_ids)) != len(self.model_ids):
            raise ValueError("duplicate model identifier")
        if (self.model_scope == "explicit") != bool(self.model_ids):
            raise ValueError("only explicit model scope carries model identifiers")
        if self.cost_caps is not None:
            if type(self.cost_caps) is not tuple or not self.cost_caps:
                raise ValueError("cost caps must be a nonempty tuple")
            names = []
            for cap in self.cost_caps:
                if (
                    type(cap) is not tuple
                    or len(cap) != 2
                    or not _text(cap[0])
                    or type(cap[1]) is not int
                    or cap[1] < 0
                ):
                    raise ValueError("invalid cost cap")
                names.append(cap[0])
            if len(set(names)) != len(names):
                raise ValueError("duplicate cost component")
            object.__setattr__(self, "cost_caps", tuple(sorted(self.cost_caps)))
        # Membership is a set, not a preference order. Keep in-memory equality
        # identical to the canonical persisted representation used by fences.
        object.__setattr__(self, "model_ids", tuple(sorted(self.model_ids)))

    def document(self) -> dict[str, object]:
        return {
            "model_scope": self.model_scope,
            "model_ids": sorted(self.model_ids),
            "cost_caps": None if self.cost_caps is None else dict(sorted(self.cost_caps)),
        }

    @classmethod
    def from_json(cls, raw: str) -> ModelAccess:
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {
            "model_scope",
            "model_ids",
            "cost_caps",
        }:
            raise ValueError("invalid model access document: " + MODEL_ACCESS_FORMAT)
        models, caps = document["model_ids"], document["cost_caps"]
        if not isinstance(models, list) or (caps is not None and not isinstance(caps, dict)):
            raise ValueError("invalid model access fields: " + MODEL_ACCESS_FORMAT)
        return cls(
            document["model_scope"],
            tuple(models),
            None if caps is None else tuple(sorted(caps.items())),
        )


def parse_model_access(document: object) -> dict[str, ModelAccess]:
    """The complete accepted membership parser shared by owner ingresses."""
    if type(document) is not dict or not document:
        raise ValueError("model_access must be a nonempty object")
    if any(not _text(name) for name in document):
        raise ValueError("invalid model_access provider")
    return {
        name: ModelAccess.from_json(json.dumps(value, allow_nan=False))
        for name, value in document.items()
    }


@dataclass(frozen=True, slots=True)
class AssignmentCandidate:
    provider: str
    binding_id: str
    binding_generation: int
    binding_digest: str
    credential_reference_id: str
    credential_reference_generation: int
    credential_reference_digest: str
    access: ModelAccess = ModelAccess()

    def __post_init__(self) -> None:
        for name in ("binding_generation", "credential_reference_generation"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError("invalid candidate generation")
        for item in fields(self):
            if item.name not in {"binding_generation", "credential_reference_generation", "access"}:
                if not _text(getattr(self, item.name)):
                    raise ValueError("invalid candidate identity")
        if not isinstance(self.access, ModelAccess):
            raise ValueError("invalid candidate model access")

    def identity(self) -> dict[str, object]:
        # Binding generation/digest are issued AFTER the assignment digest;
        # including them here would create a circular signing dependency.
        return {
            "provider": self.provider,
            "binding_id": self.binding_id,
            "credential_reference_id": self.credential_reference_id,
            "credential_reference_generation": self.credential_reference_generation,
            "credential_reference_digest": self.credential_reference_digest,
            "access": self.access.document(),
        }

    def digest(self, universe_id: str, assignment_generation: int) -> str:
        return _digest(
            {
                **self.identity(),
                "schema_version": 1,
                "universe_id": universe_id,
                "assignment_generation": assignment_generation,
                "binding_generation": self.binding_generation,
                "binding_digest": self.binding_digest,
            }
        )


def manifest_digest(root_provider: str, candidates: tuple[AssignmentCandidate, ...]) -> str:
    providers = [candidate.provider for candidate in candidates]
    if not providers or len(providers) != len(set(providers)) or root_provider not in providers:
        raise ValueError("manifest requires unique members and its root provider")
    return _digest(
        {
            "schema_version": 1,
            "root_provider": root_provider,
            "members": [
                candidate.identity() for candidate in sorted(candidates, key=lambda c: c.provider)
            ],
        }
    )


def ensure_manifest_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS provider_assignment_candidates (
            universe_id TEXT NOT NULL,
            assignment_generation INTEGER NOT NULL CHECK (assignment_generation >= 1),
            provider TEXT NOT NULL,
            binding_id TEXT NOT NULL,
            binding_generation INTEGER NOT NULL CHECK (binding_generation >= 1),
            binding_digest TEXT NOT NULL,
            credential_reference_id TEXT NOT NULL,
            credential_reference_generation INTEGER NOT NULL
                CHECK (credential_reference_generation >= 1),
            credential_reference_digest TEXT NOT NULL,
            constraints_json TEXT NOT NULL,
            candidate_digest TEXT NOT NULL,
            PRIMARY KEY (universe_id, assignment_generation, provider)
        )
    """)


def store_candidates(
    conn: sqlite3.Connection,
    universe_id: str,
    generation: int,
    candidates: tuple[AssignmentCandidate, ...],
) -> None:
    if not conn.in_transaction:
        raise ValueError("candidate write requires an active transaction")
    conn.execute(
        "DELETE FROM provider_assignment_candidates "
        "WHERE universe_id = ? AND assignment_generation = ?",
        (universe_id, generation),
    )
    conn.executemany(
        """
        INSERT INTO provider_assignment_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        [
            (
                universe_id,
                generation,
                candidate.provider,
                candidate.binding_id,
                candidate.binding_generation,
                candidate.binding_digest,
                candidate.credential_reference_id,
                candidate.credential_reference_generation,
                candidate.credential_reference_digest,
                json.dumps(candidate.access.document(), sort_keys=True, separators=(",", ":")),
                candidate.digest(universe_id, generation),
            )
            for candidate in candidates
        ],
    )


def load_candidates(
    conn: sqlite3.Connection,
    universe_id: str,
    generation: int,
) -> tuple[AssignmentCandidate, ...]:
    rows = conn.execute(
        """
        SELECT provider, binding_id, binding_generation, binding_digest,
               credential_reference_id, credential_reference_generation,
               credential_reference_digest, constraints_json, candidate_digest
          FROM provider_assignment_candidates
         WHERE universe_id = ? AND assignment_generation = ? ORDER BY provider
    """,
        (universe_id, generation),
    ).fetchall()
    candidates = []
    for row in rows:
        candidate = AssignmentCandidate(*tuple(row)[:7], access=ModelAccess.from_json(row[7]))
        if candidate.digest(universe_id, generation) != row[8]:
            raise ValueError("candidate digest is invalid")
        candidates.append(candidate)
    return tuple(candidates)
