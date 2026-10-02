"""Per-universe config.yaml reader.

Each universe can have an optional ``config.yaml`` at its root with
overrides for provider preferences, temperature, timeout, and
structural limits.  Missing file or missing keys use defaults.

See AGENTS.md Input Files table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tinyassets.provider_assignment_manifest import AssignmentCandidate

logger = logging.getLogger(__name__)


@dataclass
class UniverseConfig:
    """Per-universe configuration with defaults for all fields.

    Loaded from ``{universe_path}/config.yaml``.  Any field not
    specified in the YAML file uses the default value.
    """

    # Provider preferences
    preferred_writer: str = ""
    """Preferred writer provider name (e.g. 'claude-code'). Empty = use
    default fallback chain."""

    preferred_judge: str = ""
    """Preferred judge provider. Empty = use all available."""

    allowed_providers: list[str] | None = None
    """Per-universe provider allowlist (Q6.3 privacy primitive).

    None = no stored allowlist. Legacy/contextless routing preserves the full
    fallback chain, while an explicit requester-universe call derives a strict
    ceiling from that command center's selected writer/judge so it cannot borrow the
    process-global chain. A list = strict allowlist; the
    router filters every fallback chain (writer/judge/extract) and the
    judge ensemble down to providers whose name appears here. If the
    filter empties a chain, the call hard-fails with
    ``AllProvidersExhaustedError`` rather than silently leaking to a
    disallowed third-party provider.

    Composes with ``TINYASSETS_PIN_WRITER``: the pin sets the chain to a
    single provider first, then the allowlist filter applies; if the
    pinned provider is not in the allowlist the call hard-fails.

    See ``docs/design-notes/2026-04-27-q63-third-party-provider-privacy.md``
    and ``.claude/agent-memory/navigator/q63_section4_dispositions.md``
    for the design rationale."""

    engine_assignment_state: str = "unassigned"
    """Server-owned assignment lifecycle: unassigned/pending/ready/failed."""

    engine_assignment_generation: int = 0
    """Monotonic requester-local assignment generation (zero when unassigned)."""

    provider_authority_bindings: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Secret-free provider-to-binding projection; never request authority."""

    # Engine source (how this universe's intelligence is powered) — set by
    # `universe action=set_engine`. The founder chooses at onboard.
    engine_source: str = "byo_api_key"
    """How this command center sources its engine: ``byo_api_key`` (default; a BYO API
    key in the vault) / ``requester_local`` / ``self_hosted_endpoint`` /
    ``market_rented`` / ``host_daemon``. The BYO-API-key path is fully wired end-to-end; the others
    persist the founder's choice (deeper market-matching / endpoint-routing
    runtime is post-M1 hardening)."""

    engine_endpoint: str = ""
    """Self-hosted engine endpoint (e.g. an ``OLLAMA_HOST`` / ``ANTHROPIC_BASE_URL``
    URL) when ``engine_source=self_hosted_endpoint``."""

    market_model: str = ""
    """Model to rent from the market (e.g. ``glm-5.2``) when
    ``engine_source=market_rented``."""

    market_rate: float = 0.0
    """Per-unit market rate the founder accepts for a rented engine."""

    spending_cap: float = 0.0
    """Spending cap for a market-rented engine (0 = unset)."""

    # Model parameters
    temperature: float = 0.7
    """LLM temperature for creative generation."""

    timeout: int = 300
    """Subprocess / HTTP timeout in seconds."""

    max_tokens: int | None = None
    """Optional token cap for provider calls."""

    # Structural limits
    chapters_target: int = 1
    """Target number of chapters per book."""

    scenes_target: int = 3
    """Target number of scenes per chapter."""

    revision_limit: int = 1
    """Maximum second-draft revisions per scene (0 = no revisions)."""

    # Word count bounds
    min_words_per_scene: int = 200
    """Minimum word count for scene acceptance."""

    max_words_per_scene: int = 3000
    """Maximum word count for scene acceptance."""

    # Evaluation
    judge_count: int = 0
    """Number of judges for ensemble evaluation.  0 = all available."""

    debate_enabled: bool = True
    """Whether Tier 3 debate escalation is enabled."""

    # Custom overrides (catch-all for future extensions)
    extra: dict[str, Any] = field(default_factory=dict)
    """Any additional key-value pairs from config.yaml not mapped to
    a named field."""


#: Returned for "no config.yaml at all", where ``None`` would be ambiguous: an
#: EMPTY config.yaml also parses to ``None``, and a caller that must not erase
#: an existing file has to tell those apart.
CONFIG_ABSENT = object()


def _read_config_document(
    universe_path: str | Path, *, absent: object = None,
) -> object:
    """The parsed ``config.yaml``, *absent* when there is none, or ``OSError``.

    Through the one safe reader (:mod:`tinyassets.universe_files`): no link is
    followed, at most ``MAX_CONFIG_BYTES`` is read, and YAML anchors/aliases
    are refused before anything is expanded. The universe folder is untrusted
    input to the daemon since its agent can write it.

    Callers that only need defaults leave *absent* as ``None``. Pass
    :data:`CONFIG_ABSENT` to distinguish a missing file from an empty one.
    """
    from tinyassets.universe_files import (
        MAX_CONFIG_BYTES,
        load_untrusted_yaml,
        read_universe_text,
    )

    try:
        raw = read_universe_text(universe_path, "config.yaml", max_bytes=MAX_CONFIG_BYTES)
    except FileNotFoundError:
        return absent
    return load_untrusted_yaml(raw, max_bytes=MAX_CONFIG_BYTES)


#: ``config.yaml`` exists but could not be parsed (see `_load_preferences`).
UNREADABLE = object()


def load_universe_config(universe_path: str | Path) -> UniverseConfig:
    """Load config.yaml, with authority read only from the platform record.

    The preference fields come from ``config.yaml``. The authority fields
    (``tinyassets.provider_authority.AUTHORITY_FIELDS``) come only from the
    platform record, whatever ``config.yaml`` says (command-center-cutover E6).
    """
    from dataclasses import replace

    from tinyassets.provider_authority import AUTHORITY_FIELDS, authority_for

    preferences, data = _load_preferences(universe_path)
    authority = authority_for(universe_path, data)
    return replace(preferences, **{name: authority[name] for name in AUTHORITY_FIELDS})


def _load_preferences(universe_path: str | Path) -> tuple[UniverseConfig, Any]:
    """The preference half of ``config.yaml`` (authority stripped), and its raw data.

    Returns ``(config, data)``: ``config`` has defaults for missing fields (and is
    all defaults when the file is absent or cannot be parsed); ``data`` is the
    parsed mapping for the one-time authority migration, ``None`` when there is no
    config.yaml, or ``UNREADABLE`` when one exists but cannot be parsed (the
    migration then waits rather than record defaults over a real assignment).
    """
    try:
        data = _read_config_document(universe_path)
    except ImportError:
        logger.warning(
            "PyYAML not installed; cannot read config.yaml. "
            "Install with: pip install pyyaml"
        )
        return UniverseConfig(), UNREADABLE
    except (OSError, UnicodeDecodeError) as e:
        # A linked, oversized, alias-bearing or malformed config.yaml is never
        # parsed into the shared daemon: defaults, and a note -- never an
        # exception that breaks the turn (harness S1 review round 2).
        logger.warning("config.yaml in %s refused (%s); using defaults", universe_path, e)
        return UniverseConfig(), UNREADABLE
    if data is None:
        logger.debug("No config.yaml in %s; using defaults", universe_path)
        return UniverseConfig(), None

    if not isinstance(data, dict):
        logger.warning("config.yaml is not a mapping; using defaults")
        return UniverseConfig(), UNREADABLE

    from tinyassets.provider_authority import AUTHORITY_FIELDS

    preferences = {key: value for key, value in data.items() if key not in AUTHORITY_FIELDS}
    return _build_config(preferences), data


def _build_config(data: dict[str, Any]) -> UniverseConfig:
    """Build a UniverseConfig from parsed YAML data.

    Known keys are mapped to typed fields; unknown keys go into
    ``extra``.
    """
    known_fields = {f.name for f in UniverseConfig.__dataclass_fields__.values()}
    known_fields.discard("extra")

    kwargs: dict[str, Any] = {}
    extra: dict[str, Any] = {}

    for key, value in data.items():
        if key in known_fields:
            kwargs[key] = value
        else:
            extra[key] = value

    if extra:
        kwargs["extra"] = extra

    try:
        return UniverseConfig(**kwargs)
    except (TypeError, ValueError) as e:
        logger.warning("Invalid config.yaml values: %s; using defaults", e)
        return UniverseConfig()


def write_universe_config_fields(
    universe_path: str | Path, **fields: Any
) -> None:
    """Merge *fields* into ``{universe_path}/config.yaml`` (atomic).

    Loads the existing config.yaml (if any), updates the given top-level keys,
    and writes the merged mapping back atomically (temp file + rename). Existing
    keys not named in *fields* are preserved. This is the write path for
    per-universe engine assignment (``preferred_writer`` /
    ``allow_api_key_providers`` set by ``universe action=set_engine``).

    Fails loudly (raises) if PyYAML is unavailable or the write fails — a
    silently-dropped engine assignment would leave the universe on the wrong
    engine (Hard Rule #8).
    """
    import os
    import tempfile

    import yaml

    from tinyassets.provider_authority import AUTHORITY_FIELDS, authority_for, record_path

    refused = sorted(set(fields) & set(AUTHORITY_FIELDS))
    if refused:
        # Authority never goes into the agent-editable file (provider_authority).
        raise ValueError(f"authority fields belong in the platform record: {refused}")
    config_file = Path(universe_path) / "config.yaml"
    data: dict[str, Any] = {}
    try:
        loaded = _read_config_document(universe_path)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, UnicodeDecodeError) as e:
        logger.warning(
            "Existing config.yaml at %s unreadable (%s); rewriting fresh",
            config_file, e,
        )
    authority_for(universe_path, data)
    if any(name in data for name in AUTHORITY_FIELDS) and not record_path(universe_path).is_file():
        raise OSError("cannot strip legacy authority before its platform record exists")
    for name in AUTHORITY_FIELDS:
        data.pop(name, None)
    data.update(fields)

    config_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(config_file.parent), prefix=".config.", suffix=".yaml.tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=True)
        os.replace(tmp_path, config_file)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_provider_assignment_projection(
    universe_path: str | Path,
    *,
    state: str,
    generation: int,
    provider: str = "",
    binding: dict[str, Any] | None = None,
    assignment_candidates: tuple[AssignmentCandidate, ...] | None = None,
) -> None:
    """Strictly publish the non-authorizing requester-local config projection.

    Unlike the legacy merge helper, an unreadable existing config is a hard
    failure: assignment must never erase unrelated keys by falling back to an
    empty document.

    Manifest projections consume resolved assignment records, not another list
    of supported provider brands. These records are data, not launch authority;
    the publisher owns their resolution and admission still re-reads the ledger.
    """

    import os
    import tempfile

    import yaml

    normalized_state = state.strip()
    if normalized_state not in {"unassigned", "pending", "ready", "failed"}:
        raise ValueError("provider assignment state is invalid")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise ValueError("provider assignment generation is invalid")
    selected = provider.strip()
    if assignment_candidates is not None and normalized_state != "ready":
        raise ValueError("candidate projections require a ready assignment")
    if normalized_state == "ready":
        if generation < 1:
            raise ValueError("ready assignment requires one canonical provider")
        if assignment_candidates is None:
            # Preserve the legacy single-source input contract. Manifest members
            # are validated/resolved upstream, not guessed from names here.
            _is_canonical = selected in {"claude-code", "codex"} or selected.startswith(
                "api_key_http:"
            )
            if not _is_canonical:
                raise ValueError("ready assignment requires one canonical provider")
        if not isinstance(binding, dict) or not binding.get("binding_id"):
            raise ValueError("ready assignment requires a binding projection")
        allowed = [selected]
        bindings = {selected: dict(binding)}
        if assignment_candidates is not None:
            from tinyassets.provider_assignment_manifest import AssignmentCandidate

            if (
                type(assignment_candidates) is not tuple or not assignment_candidates
                or any(type(member) is not AssignmentCandidate for member in assignment_candidates)
                or type(binding.get("generation")) is not int
                or not isinstance(binding.get("assignment_digest"), str)
                or not binding["assignment_digest"]
            ):
                raise ValueError("invalid accepted candidate projections")
            members = {member.provider: member for member in assignment_candidates}
            if len(members) != len(assignment_candidates) or selected not in members:
                raise ValueError("invalid accepted candidate projections")
            allowed = sorted(members)
            bindings = {
                name: {
                    "binding_id": members[name].binding_id,
                    "generation": members[name].binding_generation,
                    "binding_digest": members[name].binding_digest,
                    "assignment_digest": binding["assignment_digest"],
                }
                for name in allowed
            }
            if bindings[selected] != binding:
                raise ValueError("root projection does not match its assignment candidate")
        preferred = selected
        engine_source = "requester_local"
    else:
        allowed = []
        bindings = {}
        preferred = ""
        engine_source = "requester_local" if generation else "unassigned"

    config_file = Path(universe_path) / "config.yaml"
    data: dict[str, Any] = {}
    try:
        loaded = _read_config_document(universe_path, absent=CONFIG_ABSENT)
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(
            "existing config.yaml is unreadable; refusing to erase it"
        ) from exc
    if loaded is not CONFIG_ABSENT:
        # A config.yaml that EXISTS but parses to nothing is not a mapping
        # either, and erasing it is exactly what this function refuses to do.
        if not isinstance(loaded, dict):
            raise ValueError("existing config.yaml must be a mapping")
        data = loaded
    # Authority goes to the platform record only (tinyassets.provider_authority);
    # config.yaml keeps the preferences and loses any authority it still held.
    from tinyassets.provider_authority import AUTHORITY_FIELDS, write_record

    write_record(universe_path, {
        "allowed_providers": allowed,
        "engine_assignment_generation": generation,
        "engine_assignment_state": normalized_state,
        "provider_authority_bindings": bindings,
    })
    for name in AUTHORITY_FIELDS:
        data.pop(name, None)
    data.update({
        "engine_source": engine_source,
        "preferred_writer": preferred,
    })
    config_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(config_file.parent), prefix=".config.", suffix=".yaml.tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, sort_keys=True)
        os.replace(tmp_path, config_file)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
