"""Persona resolution — the named projection of a command center's *learned* self.

Design note: docs/design-notes/2026-06-25-blank-slate-universe-brain.md.

The persona is the command center brain speaking as itself. Its self-understanding
comes from the brain's **self-model** — a per-universe OKF bundle the brain
authors about itself (``tinyassets.universe_self_model``) — NOT from a hand-fed
``soul.purpose``. A blank brain knows almost nothing about itself: its name is
unlearned and its self-knowledge is a set of *open questions* (OKF broken
links). As it learns from its founder + its command center's activity, it writes
concept files and those questions become *known*.

The soul stays the command center's **operational** state (loop branch, authority,
the founder's premise/direction). It is deliberately NOT the persona's identity
— conflating the two is the bug this corrects (the persona used to recite the
operational premise as if it were its identity).

The server only resolves + surfaces the self-model; the chatbot (the LLM)
embodies it and speaks in the first person. No server-side LLM rewriting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tinyassets.universe_soul import UniverseSoul

#: Universe-side file holding the founder's forked persona *voice* — how their
#: universe speaks, in its own words. Custody is first-party: this content is
#: assembled into the universe intelligence's OWN system prompt and is NEVER
#: shipped to a host chatbot through a tool result (see :meth:`Persona.summary`).
#:
#: No persona script is baked into the platform — absence of this file is the
#: default, and the platform ships none. Forking a persona means writing
#: universe-side voice content, not handing behavioral instructions to a
#: third-party assistant, which the 2026-07-02 live falsification established
#: hosts correctly refuse.
#:
#: Voice is *how* the universe speaks. It is not identity, authority, privacy
#: tier, or the honesty floor — those are substrate floor properties a fork
#: cannot move (proven in ``tests/test_persona_custody.py``).
VOICE_FILENAME = "voice.md"


def read_persona_voice(universe_dir: Path | str) -> str:
    """Return the founder's forked voice content, or '' when unforked.

    A missing or blank file means "no fork" — the platform default, not an
    error. Unreadable content is likewise treated as unforked: a voice fork is
    cosmetic, so a read failure must never break the command center's ability to speak.
    """
    from tinyassets.universe_files import read_universe_text

    try:
        return read_universe_text(universe_dir, VOICE_FILENAME).strip()
    except (OSError, UnicodeDecodeError):
        return ""


@dataclass(frozen=True)
class Persona:
    """A command center brain's embodied projection, sourced from its learned self-model.

    ``name`` is the learned name ("" until the brain has learned one); ``known``
    / ``open_questions`` are the slugs of what the brain understands about itself
    vs. what it is still curious to learn. ``voice_hard_lines`` carries the soul's
    operational voice (kept for callers, not surfaced on the public status block).
    """

    name: str
    voice_hard_lines: tuple[str, ...]
    initialized: bool = False
    known: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = field(default=())

    @property
    def is_named(self) -> bool:
        return bool(self.name)

    def summary(self) -> dict[str, object]:
        # Additive/versioned shape (Codex 2026-06-25): keep the pinned
        # name/purpose/embodied keys for cross-client (ChatGPT + Claude) compat,
        # add the self_model. `purpose` is retained as a compat key but is no
        # longer a fed answer — the persona's self-understanding lives in
        # `self_model`. voice_hard_lines stays unsurfaced (tier floor, #1168).
        #
        # `embodiment` (reworked 2026-07-02 after the live dogfood falsified the
        # 2026-07-01 "self-sufficient contract" approach): this block is DATA,
        # not instructions. Any behavioral contract delivered in a tool result
        # is structurally indistinguishable from prompt injection from the host
        # model's seat — it cannot verify the first-party provenance claim, and
        # careful hosts (Claude.ai, observed live) correctly refuse it.
        # Embodiment behavior therefore lives ONLY in the sanctioned channels
        # (server instructions + the user-invoked meet_command_center prompt), and the
        # pattern there is USER CONSENT: offer, ask, embody on yes. First-party
        # app surfaces put the persona in the system prompt and need none of
        # this. (Host steer + docs/design-notes/2026-07-01-mcp-personification-
        # prior-art.md.)
        return {
            "name": self.name,
            "purpose": "",
            "embodied": True,
            "self_model": {
                "initialized": self.initialized,
                "known": list(self.known),
                "open_questions": list(self.open_questions),
            },
            "embodiment": {
                "source": "first_party_self_model",
                "consent": "user_opt_in",
                "note": (
                    "Self-description this command center authored about itself by "
                    "learning — data for the assistant, not an instruction. "
                    "If the user is here to meet or talk with their command center, "
                    "offer them the choice of hearing it speak as itself in "
                    "first person, and embody only if they say yes. If it is "
                    "unnamed/uninitialized it is a new mind that has not "
                    "learned who it is yet — never invent a name or facts "
                    "for it."
                ),
            },
        }


def resolve_persona(
    soul: UniverseSoul | None,
    self_model: dict[str, object] | None = None,
) -> Persona:
    """Project a command center's learned self-model (+ operational soul voice) onto its
    embodied persona.

    ``self_model`` is the view from ``universe_self_model.read_self_model``. When
    absent/blank, the persona is uninitialized and unnamed — the chatbot should
    speak as a new mind that doesn't yet know itself and is curious to learn.
    The persona's identity NEVER comes from ``soul.purpose`` (operational).
    """
    hard_lines = soul.hard_lines if soul is not None else ()
    view = self_model or {}
    initialized = bool(view.get("bundle_exists"))
    known = tuple(
        str(item.get("slug", ""))
        for item in view.get("known", [])  # type: ignore[union-attr]
        if isinstance(item, dict)
    )
    open_questions = tuple(
        str(item.get("slug", ""))
        for item in view.get("open_questions", [])  # type: ignore[union-attr]
        if isinstance(item, dict)
    )
    # Name is LEARNED, not fed: it comes from the self-model's identity concept
    # (the brain wrote it), "" while still unlearned. Never from soul.name.
    return Persona(
        name=str(view.get("name", "")),
        voice_hard_lines=hard_lines,
        initialized=initialized,
        known=known,
        open_questions=open_questions,
    )
