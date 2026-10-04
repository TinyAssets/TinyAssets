"""Pure recipient update planning; no publication, storage, execution or authority.

Inputs must come from authenticated server adapters and immutable platform
records. Content hashes detect changes, not authorship. An executor must bind
owner consent to the plan digest, recheck public availability and every expected
recipient digest under its mutation lock, and persist per-component progress.
Never accept these records (or a client 'verified' flag) as permission to write.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

KINDS = frozenset(
    {
        "tinyassets.app-ui.v1",
        "tinyassets.branch-ref.v1",
        "tinyassets.automation-spec.v1",
        "tinyassets.agent-ref.v1",
    }
)
AGENT_KIND = "tinyassets.agent-ref.v1"


def digest(value: Any) -> str:
    """Canonical JSON digest, rejecting non-JSON/NaN data instead of coercing it."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _sha(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected a SHA-256 digest")


def _unique(rows: tuple, field: str) -> dict:
    result = {getattr(row, field): row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate {field}")
    return result


@dataclass(frozen=True)
class Component:
    key: str
    kind: str
    source_digest: str  # Adapter hashes the full normalized public declaration/contract.
    dependencies: tuple[tuple[str, str], ...] = ()
    capabilities: tuple[str, ...] = ()
    privileged: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key or self.kind not in KINDS:
            raise ValueError("component needs a stable key and supported kind")
        _sha(self.source_digest)
        if not isinstance(self.dependencies, tuple) or not isinstance(self.capabilities, tuple):
            raise ValueError("component contracts must be immutable tuples")
        if any(not isinstance(pair, tuple) or len(pair) != 2 for pair in self.dependencies):
            raise ValueError("dependency pairs must be immutable")
        if len(dict(self.dependencies)) != len(self.dependencies):
            raise ValueError("duplicate dependency")
        for key, required in self.dependencies:
            if not key or key == self.key:
                raise ValueError("invalid dependency")
            _sha(required)
        if any(not isinstance(c, str) or not c for c in self.capabilities):
            raise ValueError("invalid capability")
        if (
            len(set(self.capabilities)) != len(self.capabilities)
            or type(self.privileged) is not bool
        ):
            raise ValueError("invalid capability contract")


@dataclass(frozen=True)
class Release:
    publication_id: str
    author_id: str
    definition_id: str
    definition_digest: str
    sequence: int
    parent_id: str
    summary: str
    components: tuple[Component, ...]

    def __post_init__(self) -> None:
        if not all(
            isinstance(v, str) and v
            for v in (self.publication_id, self.author_id, self.definition_id)
        ):
            raise ValueError("release needs author-bound publication and exact definition")
        _sha(self.definition_digest)
        if not isinstance(self.summary, str):
            raise ValueError("release summary must be text")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("invalid release sequence")
        if (self.sequence == 1) != (self.parent_id == ""):
            raise ValueError("release ancestry is missing or unexpected")
        if self.parent_id:
            _sha(self.parent_id)
        if not isinstance(self.components, tuple):
            raise ValueError("release components must be immutable")
        components = _unique(self.components, "key")
        for component in self.components:
            for key, required in component.dependencies:
                if key not in components or components[key].source_digest != required:
                    raise ValueError("release dependency is absent or mismatched")

    @property
    def release_id(self) -> str:
        return digest(asdict(self))


def verify_chain(releases: tuple[Release, ...]) -> dict[str, Release]:
    """Check a complete immutable chain loaded from the owner's publication registry."""
    if not releases or releases[0].sequence != 1:
        raise ValueError("complete release ancestry required")
    for previous, current in zip(releases, releases[1:]):
        if (
            (previous.author_id, previous.publication_id)
            != (current.author_id, current.publication_id)
            or current.sequence != previous.sequence + 1
            or current.parent_id != previous.release_id
        ):
            raise ValueError("release ancestry or author changed")
    return {release.release_id: release for release in releases}


@dataclass(frozen=True)
class InstalledComponent:
    source: Component
    release_id: str
    target_id: str
    installed_digest: str

    def __post_init__(self) -> None:
        _sha(self.release_id)
        _sha(self.installed_digest)
        if not self.target_id:
            raise ValueError("recipient target is required")


@dataclass(frozen=True)
class Adoption:
    adoption_id: str
    universe_id: str
    publication_id: str
    author_id: str
    components: tuple[InstalledComponent, ...]
    accepted_capabilities: tuple[str, ...] = ()
    auto_update: bool = False

    def __post_init__(self) -> None:
        if not all(
            isinstance(v, str) and v
            for v in (self.adoption_id, self.universe_id, self.publication_id, self.author_id)
        ):
            raise ValueError("recipient and publication identity required")
        if not isinstance(self.components, tuple) or not isinstance(
            self.accepted_capabilities, tuple
        ):
            raise ValueError("adoption records must be immutable")
        if any(not isinstance(c, str) or not c for c in self.accepted_capabilities):
            raise ValueError("accepted capabilities must be text identifiers")
        _unique(self.components, "target_id")
        if len({c.source.key for c in self.components}) != len(self.components):
            raise ValueError("duplicate installed source key")
        if type(self.auto_update) is not bool:
            raise ValueError("automatic updates require an explicit boolean policy")


@dataclass(frozen=True)
class Observation:
    key: str
    target_id: str
    current_digest: str | None  # None is an observed deletion, never an empty baseline.

    def __post_init__(self) -> None:
        if self.current_digest is not None:
            _sha(self.current_digest)


@dataclass(frozen=True)
class Candidate:
    key: str
    target_id: str
    installed_digest: str  # Trusted adapter hashes content AFTER recipient remapping.

    def __post_init__(self) -> None:
        if not self.key or not self.target_id:
            raise ValueError("candidate destination is required")
        _sha(self.installed_digest)


@dataclass(frozen=True)
class Change:
    key: str
    target_id: str
    expected_digest: str | None
    installed_digest: str
    source: Component
    release_id: str
    preserve_runtime_state: bool = True


@dataclass(frozen=True)
class UpdatePlan:
    adoption_id: str
    universe_id: str
    release_id: str
    adoption_digest: str
    preconditions: tuple[Observation, ...]
    changes: tuple[Change, ...]
    conflicts: tuple[tuple[str, str], ...]
    decisions: tuple[tuple[str, str], ...]
    installed_versions: tuple[tuple[str, str], ...]
    proposed_versions: tuple[tuple[str, str], ...]
    automatic: bool

    @property
    def plan_digest(self) -> str:
        return digest(asdict(self))

    @property
    def ready(self) -> bool:
        return not self.conflicts and not self.decisions


def plan_update(
    *,
    adoption: Adoption,
    releases: tuple[Release, ...],
    selected: tuple[str, ...],
    observations: tuple[Observation, ...],
    candidates: tuple[Candidate, ...],
    public_release_ids: frozenset[str],
) -> UpdatePlan:
    """Plan a subset of the final release; never expand selection or apply effects.

    `public_release_ids` is a fresh server public-read result, not a publisher's
    claim. Empty/unknown source fails closed. `observations` includes every
    installed target and every proposed new destination, including absent ones.
    """
    chain = verify_chain(releases)
    target = releases[-1]
    if (adoption.author_id, adoption.publication_id) != (target.author_id, target.publication_id):
        raise ValueError("adoption belongs to a different publication")
    installed = {c.source.key: c for c in adoption.components}
    for item in installed.values():
        release = chain.get(item.release_id)
        if release is None or item.source not in release.components:
            raise ValueError("installed component provenance is not in this release chain")
    observed = _unique(observations, "key")
    proposed = _unique(candidates, "key")
    source = {c.key: c for c in target.components}
    if len(set(selected)) != len(selected) or any(
        k not in installed and k not in source for k in selected
    ):
        raise ValueError("invalid component selection")
    changes, conflicts, decisions = [], [], []
    effective = {k: c.source for k, c in installed.items()}
    versions = {k: c.release_id for k, c in installed.items()}
    if target.release_id not in public_release_ids:
        conflicts.append(("", "source_unavailable"))
    occupied = {c.target_id: k for k, c in installed.items()}
    for key in sorted(selected):
        old, new, observation, candidate = (
            installed.get(key),
            source.get(key),
            observed.get(key),
            proposed.get(key),
        )
        if new is None:
            conflicts.append((key, "source_removed_keep_recipient"))
            continue
        if old and new == old.source:
            # Local-only edits/deletions are retained; there is no proposed write.
            continue
        if old and old.source.kind != new.kind:
            conflicts.append((key, "component_kind_changed"))
            continue
        if not observation or not candidate or observation.target_id != candidate.target_id:
            conflicts.append((key, "missing_destination_observation"))
            continue
        if old and candidate.target_id != old.target_id:
            conflicts.append((key, "recipient_target_changed"))
            continue
        if candidate.target_id in occupied and occupied[candidate.target_id] != key:
            conflicts.append((key, "destination_collision"))
            continue
        if old and observation.current_digest != old.installed_digest:
            conflicts.append(
                (
                    key,
                    "recipient_deleted"
                    if observation.current_digest is None
                    else "recipient_edited",
                )
            )
            continue
        if not old and observation.current_digest is not None:
            conflicts.append((key, "destination_collision"))
            continue
        occupied[candidate.target_id] = key
        if set(new.capabilities) - set(adoption.accepted_capabilities):
            decisions.append((key, "new_capabilities"))
        if new.privileged or new.kind == AGENT_KIND:
            decisions.append((key, "privileged_or_agent_change"))
        if not old:
            decisions.append((key, "new_component"))
        changes.append(
            Change(
                key,
                candidate.target_id,
                observation.current_digest,
                candidate.installed_digest,
                new,
                target.release_id,
            )
        )
        effective[key], versions[key] = new, target.release_id
    for key, component in sorted(effective.items()):
        for dependency, required in component.dependencies:
            if dependency not in effective or effective[dependency].source_digest != required:
                conflicts.append((key, f"dependency_mismatch:{dependency}"))
            elif dependency not in {c.key for c in changes}:
                observation = observed.get(dependency)
                original = installed.get(dependency)
                if (
                    not observation
                    or not original
                    or observation.target_id != original.target_id
                    or observation.current_digest != original.installed_digest
                ):
                    conflicts.append((key, f"dependency_changed:{dependency}"))
    return UpdatePlan(
        adoption.adoption_id,
        adoption.universe_id,
        target.release_id,
        digest(asdict(adoption)),
        tuple(observed[key] for key in sorted(observed)),
        tuple(changes),
        tuple(sorted(set(conflicts))),
        tuple(sorted(set(decisions))),
        tuple(sorted((k, c.release_id) for k, c in installed.items())),
        tuple(sorted(versions.items())),
        bool(adoption.auto_update and changes and not conflicts and not decisions),
    )


def recheck_plan(
    *,
    plan: UpdatePlan,
    adoption: Adoption,
    observations: tuple[Observation, ...],
    public_release_ids: frozenset[str],
) -> None:
    """Reject stale consent/read sets; call under the executor's recipient lock.

    This comparison is not itself atomic and does not authorize execution. The
    caller must keep the lock through materialization and durable progress writes.
    Retained dependency observations are fenced as well as proposed destinations.
    """
    if not plan.ready:
        raise ValueError("update still needs recipient decisions or conflict resolution")
    if digest(asdict(adoption)) != plan.adoption_digest:
        raise ValueError("recipient adoption or update policy changed")
    if plan.release_id not in public_release_ids:
        raise ValueError("source is no longer public")
    observed = _unique(observations, "key")
    if any(observed.get(expected.key) != expected for expected in plan.preconditions):
        raise ValueError("recipient content changed since update planning")


@dataclass(frozen=True)
class LegacyProvenance:
    """Exact prior source only: deliberately no publication ID or auto-update policy."""

    pin_id: str
    author_id: str
    definition_id: str
    definition_digest: str
    source_plan_digest: str
    targets: tuple[tuple[str, str], ...]


def legacy_provenance(*, pin: dict, definition: dict) -> LegacyProvenance:
    """Validate an activated, owner-scoped platform pin against its immutable source.

    Fetch `pin` through pin_for_request using the authenticated owner's universe,
    and `definition` by its exact immutable ID. This pure helper cannot authorize
    caller-supplied dictionaries. Missing legacy evidence is an error, not a
    name/content-match migration. Installed hashes require the remapping adapter.
    """
    if pin.get("state") != "activated" or pin.get("kind") != "install":
        raise ValueError("an activated installation pin is required")
    action = pin.get("record", {}).get("action", {})
    plan = action.get("plan", {})
    if (
        plan.get("publication_kind") != "system"
        or definition.get("agent_definition_id") != plan.get("definition_id")
        or action.get("agent_definition_id") != plan.get("definition_id")
        or not plan.get("author")
        or definition.get("author_id") != plan.get("author")
    ):
        raise ValueError("exact immutable system source is required")
    unhashed = {key: value for key, value in plan.items() if key != "digest"}
    # Existing system_copy_requests._plan uses the default JSON separators/ASCII.
    expected = hashlib.sha256(
        json.dumps({"definition": definition, "plan": unhashed}, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if not pin.get("pin_id") or any(
        value != expected
        for value in (pin.get("digest"), plan.get("digest"), action.get("snapshot_digest"))
    ):
        raise ValueError("installation source plan digest mismatch")
    progress = pin.get("progress", {})
    if progress.get("installed") is not True or progress.get("publication_kind") != "system":
        raise ValueError("completed component-copy receipt required")
    targets = {"ui": progress.get("ui")}
    for group in ("workflows", "automations", "agents"):
        plan_group = "agent_templates" if group == "agents" else group
        expected_keys = {item["key"] for item in plan.get(plan_group, [])}
        actual = progress.get(group, {})
        if not isinstance(actual, dict) or set(actual) != expected_keys:
            raise ValueError("installation target mapping is incomplete")
        for key, target_id in actual.items():
            if key in targets:
                raise ValueError("ambiguous component key")
            targets[key] = target_id
    if any(not isinstance(t, str) or not t for t in targets.values()):
        raise ValueError("installation target mapping is incomplete")
    if len(set(targets.values())) != len(targets):
        raise ValueError("installation targets overlap")
    return LegacyProvenance(
        pin["pin_id"],
        plan["author"],
        plan["definition_id"],
        digest(definition),
        expected,
        tuple(sorted(targets.items())),
    )
