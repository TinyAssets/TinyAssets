"""Normalized native metadata. No account authority or provider wire names."""

from dataclasses import dataclass
from datetime import datetime

from tinyassets.providers.native_model_selection import validate_model_id

#: Ceiling on reported effort levels. A transport guard, not a vocabulary: the
#: platform never decides which level names an executor may advertise.
MAX_EFFORT_LEVELS = 32


@dataclass(frozen=True, slots=True)
class NativeModel:
    model_id: str
    input_modalities: frozenset[str]
    hidden: bool = False
    #: Whether the EXECUTOR said this model takes an effort/reasoning setting.
    #: Per-model, never per-provider: a live Claude Code catalogue reports effort
    #: on Opus/Sonnet/Fable and NOT on Haiku, and reports xhigh on 5.x while
    #: 4.6 stops at high. A provider-wide flag would invent levels for a model
    #: that rejects them, so the executor's own answer is the only authority.
    supports_effort: bool = False
    #: The executor's advertised level names IN ITS OWN ORDER, which is display
    #: order too. Deliberately not validated against a platform enum: Claude
    #: Code offers max and no minimal, Codex offers minimal and no max, and a
    #: shared enum would have to refuse one of them.
    effort_levels: tuple[str, ...] = ()

    def __post_init__(self):
        if not validate_model_id(self.model_id):
            raise ValueError("native catalogue needs a nonempty model ID")
        if (type(self.input_modalities) is not frozenset
                or any(type(item) is not str or not item or len(item) > 100
                       for item in self.input_modalities)
                or type(self.hidden) is not bool):
            raise ValueError("invalid native model metadata")
        if (type(self.supports_effort) is not bool or type(self.effort_levels) is not tuple
                or len(self.effort_levels) > MAX_EFFORT_LEVELS
                or any(type(level) is not str or not level or len(level) > 100
                       or level != level.strip() or not level.isprintable()
                       for level in self.effort_levels)
                or len(set(self.effort_levels)) != len(self.effort_levels)):
            raise ValueError("invalid native model effort metadata")
        # Levels without support, or support without levels, would both let a
        # caller offer a control the executor never promised to honour.
        if bool(self.effort_levels) != self.supports_effort:
            raise ValueError("native effort support must name its levels")


@dataclass(frozen=True, slots=True)
class NativeCatalogue:
    models: tuple[NativeModel, ...]
    default_model_id: str | None
    observed_at: datetime

    def __post_init__(self):
        if (type(self.models) is not tuple
                or any(type(model) is not NativeModel for model in self.models)):
            raise ValueError("invalid native catalogue models")
        ids = {model.model_id for model in self.models}
        if len(ids) != len(self.models):
            raise ValueError("duplicate native catalogue model")
        if self.default_model_id is not None and self.default_model_id not in ids:
            raise ValueError("native default must occur in the catalogue")
        if (not isinstance(self.observed_at, datetime)
                or self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None):
            raise ValueError("native catalogue needs an aware observation time")
