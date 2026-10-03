"""Exception hierarchy for TinyAssets.

Every exception in the system inherits from FantasyAuthorError so callers
can catch broadly when appropriate.
"""


class FantasyAuthorError(Exception):
    """Base exception for all TinyAssets errors."""


# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------

class ProviderError(FantasyAuthorError):
    """A provider call failed for a non-transient reason."""

    #: Structured attempt classification (streamed-attempt taxonomy). ``None``
    #: on the base class; specific subclasses below pin a concrete value so the
    #: router's cooldown map and ``app_ingress._failure_notice`` can branch on
    #: the *class* of failure instead of substring-matching the message.
    failure_class: str | None = None

    #: Streamed-attempt telemetry snapshot attached at raise time by the
    #: streaming reader (Slice 1 blocker K): ``side_effect_state`` /
    #: ``tool_phase`` / ``ttft_ms`` / ``last_progress_age_ms`` / ``exit_code`` /
    #: ``terminal`` / ``provider``. The success path carries these on
    #: :class:`~tinyassets.providers.base.ProviderResponse`; attaching them to the
    #: raised exception lets the router + the honest user notice reason about a
    #: FAILED attempt (e.g. whether a tool may have run) instead of only the
    #: message string. ``None`` on the base class and on non-streaming raises.
    attempt_telemetry: dict | None = None


class ProviderTimeoutError(ProviderError):
    """A provider subprocess exceeded the activity timeout."""


class ProviderAuthenticationError(ProviderError):
    """Closed adapter-reported sign-in failure, NOT evidence of no side effects."""

    failure_class = "auth_invalid"


class ProviderIdleTimeoutError(ProviderTimeoutError):
    """A streamed served attempt stopped emitting real protocol events.

    The idle watchdog fired: no assistant text delta, tool event, provider
    retry event, or terminal result arrived within the phase's idle interval.
    A subclass of :class:`ProviderTimeoutError` so legacy ``except
    ProviderTimeoutError`` callers keep working, but the router treats it as a
    transient ATTEMPT outcome and does NOT place the sole served writer on a
    provider-wide cooldown — the next turn stays eligible.
    """

    failure_class = "provider_idle_timeout"


class InteractiveDeadlineError(ProviderTimeoutError):
    """A streamed served attempt reached the absolute interactive safety cap.

    The turn kept making progress but ran past the absolute cap (a fairness /
    resource backstop, not evidence of an unhealthy provider). Like
    :class:`ProviderIdleTimeoutError`, this does NOT cool the provider.
    """

    failure_class = "interactive_deadline"


class ProviderReplyTimeoutError(ProviderTimeoutError):
    """The model did not finish answering inside the reply budget.

    The request WAS sent and the model may have generated, so its usage is
    unknown and nothing about the source's health follows from it: no cooldown.
    Live 2026-09-29, turn b804819f: a free model writing an app ran past the
    broker's old 30s total and the owner was told "we could not identify why".
    """

    failure_class = "provider_reply_timeout"


class ProviderUnavailableError(ProviderError):
    """Provider returned a signal that it is temporarily unreachable
    (e.g. exit code 1 within <5 s, rate-limit header, auth failure).
    Triggers a sticky cooldown on the provider.
    """


class SelectedModelCapacityError(ProviderUnavailableError):
    """Confirmed pre-generation HTTP refusal with protocol-scoped evidence.

    ``detail`` is the SOURCE's own scrubbed, bounded words about the refusal.
    Without one the message degrades to the class name, which is what the owner
    used to be shown as the provider's explanation (live 2026-09-25). It is
    diagnostic text only: the typed ``signal`` remains the sole evidence.
    """

    def __init__(self, signal, *, detail: str = ""):
        from tinyassets.providers.model_capacity import CapacitySignal

        if type(signal) is not CapacitySignal:
            raise TypeError("capacity error requires normalized evidence")
        super().__init__(detail if type(detail) is str and detail else signal.failure_class)
        self.signal = signal
        self.failure_class = signal.failure_class
        self.retry_after = signal.retry_after_s


class SelectedModelContextError(PermissionError):
    """OUR refusal: the request does not fit the selected model's published window.

    Measured before anything is sent, so nothing ran and nothing was spent.
    ``required_tokens`` is the smallest window that would have admitted the
    request, which is what lets a turn move to an accepted model that fits
    instead of dying (live 2026-09-26, a 1.27 MB tool result on a 262k model
    while a 1M-token free model sat in the same owner's order).

    A ``PermissionError`` because that is what every existing reader catches.
    """

    def __init__(self, message: str, *, required_tokens: int):
        super().__init__(message)
        self.required_tokens = required_tokens


class ProviderRateLimitedError(ProviderUnavailableError):
    """The provider reported a genuine rate limit (documented retry event).

    Carries ``retry_after`` (seconds) when the provider supplied one so the
    router can cool the provider for exactly that window instead of a fixed
    default. A subclass of :class:`ProviderUnavailableError` so existing
    unavailable-handling still applies where the precise handler is absent.
    """

    failure_class = "provider_rate_limited"

    def __init__(self, *args, retry_after: float | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class ProviderOverloadedError(ProviderUnavailableError):
    """The provider reported a transient overload (documented retry event)."""

    failure_class = "provider_overloaded"

    def __init__(self, *args, retry_after: float | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class ProviderProtocolError(ProviderError):
    """The provider emitted an unparseable / malformed event stream.

    Raised by the streaming reader when a non-whitespace stdout line is not a
    valid stream-json event object (fail loud per hard rule #8 rather than
    silently discarding garbage).
    """

    failure_class = "provider_protocol_error"


class ProviderReplyError(ProviderProtocolError):
    """The source answered HTTP 200 and REPORTED an error instead of a reply.

    An OpenAI-compatible body (or its one choice) carrying an ``error`` object:
    the model behind the source failed partway through generating. Live
    2026-09-30 and 2026-10-02 on the free-only account, nemotron did this after
    12-19 good tool rounds, and the owner read "the connected model replied in
    a format this command center could not read". Nothing was unreadable; the
    source said what went wrong and we threw its words away.

    The message is the source's own scrubbed words. A subclass of the protocol
    error so every existing handler still applies; a turn treats it as
    transient (same model once more, then the next accepted model).
    """

    failure_class = "provider_reply_error"


class ProviderStalledError(ProviderProtocolError):
    """A streamed reply STOPPED arriving partway (inactivity, not slowness).

    The model sent part of its answer and then nothing for the source's
    inactivity window. A reply that keeps arriving is never cut, however long
    it takes (founder, 2026-10-02). ``partial_text`` is the assistant text that
    did arrive, kept for the owner's notice rather than silently dropped; it is
    never logged.
    """

    failure_class = "provider_stalled"

    def __init__(self, *args, partial_text: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.partial_text = partial_text


class ProviderUnreadableReplyError(ProviderProtocolError):
    """The source answered HTTP 2xx with a reply we could not decode.

    No choice, an incomplete stream, a cut-off tool batch, a tool that is not
    enabled: a weaker model's slip, which usually does not repeat. Distinct
    from an unrecognized HTTP status (still a plain protocol error), which is
    the source rejecting the REQUEST and would only be rejected again.
    """

    failure_class = "provider_unreadable_reply"


class ProviderModelRefusedError(ProviderUnavailableError):
    """The source refused to serve THIS model before generating anything.

    HTTP 403 (access refused) or 404/410 (no such model, or none it will serve
    here) on an inference request. Live 2026-09-28, free-only universe: a free
    model OpenRouter answered with 403 reached the owner as "the connected model
    replied in a format this universe could not read" -- nothing had replied --
    and the router cooled the whole connection, so the sibling free models the
    owner had accepted were skipped too.

    The message is the source's OWN scrubbed status and body, the only place it
    explains which refusal this was. The scope is the model: the turn may move
    to the next model the owner accepted; the connection is not cooled.

    A :class:`ProviderUnavailableError` because, like a capacity refusal, it is
    a confirmed pre-generation answer: the reservation is released, not charged.
    """

    failure_class = "provider_refused"


class ProviderAuthorityHeldError(ProviderError):
    """Provider execution has no requester- or platform-owned authority.

    Optionally carries the redacted ``attempts`` / ``chain_state`` of an ARMED
    attempt that was actually made and failed without proving a side-effect-free
    capacity failure (the captured-prompt loop's held branch). Both default to
    ``None``: a refusal that invoked nothing has no attempt evidence. The
    compiler's event and error readers take them off the OUTER exception, so
    leaving them on ``__cause__`` alone erased the provider's failure class from
    the run record (live 2026-09-21, run 07c1611916cc4eb4).
    """

    failure_class = "authority_held"
    #: Prefix of every held-ATTEMPT message. Only the string survives the async
    #: runner, so the stored-error classifiers key on it -- the same contract
    #: as ``WorkModelExhaustedError.MESSAGE``. A held attempt is not "no
    #: authority": the owner's source was bound, admitted, and invoked.
    ATTEMPT_MESSAGE = "work model attempt is held"

    def __init__(self, *args, attempts=None, chain_state=None, **kwargs):
        super().__init__(*args, **kwargs)
        # list[ProviderAttemptDiagnostic] | None -- redacted at the router.
        self.attempts = attempts
        # dict | None -- typically built via diagnostics.build_chain_state
        self.chain_state = chain_state


class PlatformLLMCallRefusedError(ProviderAuthorityHeldError):
    """A model call was not bound to one universe's owner-connected credentials.

    The platform has no LLM (AGENTS.md Hard Rule 15). Raised by
    ``tinyassets.providers.owner_binding`` -- the single enforcement point in
    the provider router -- before any provider is probed or launched. A
    SUBCLASS of ``ProviderAuthorityHeldError`` so every caller that already
    refuses to swallow held authority propagates this refusal too, and it keeps
    the inherited ``authority_held`` failure class the run taxonomy keys on.
    """


class WorkModelExhaustedError(ProviderAuthorityHeldError):
    """Every model in the run's captured order is exhausted, not unbound.

    A SUBCLASS on purpose: every existing ``except ProviderAuthorityHeldError``
    keeps catching this, so the generic held-authority contract is unchanged.
    It exists only so the run taxonomy can tell "your capacity ran out" apart
    from "connect a provider" by TYPE rather than by matching message text --
    the live 2026-09-20 checklist reported exhausted work models to the owner as
    an unconnected provider, which is a different action entirely.
    """

    failure_class = "work_model_exhausted"
    # Every raise site starts its message with this. The async runner keeps
    # only the string, so the stored-error classifiers key on it, not on TYPE.
    MESSAGE = "no eligible work model remains"


class AllProvidersExhaustedError(ProviderError):
    """Every provider in the fallback chain failed or is in cooldown.

    ``NO_WIDENING_MESSAGE`` is the suffix of the single-source raises (served
    turn, armed run carrier): one authorized provider was asked and failed, and
    authority forbids trying another. Stored-error classifiers key on it to
    read the attempt's own classified cause instead of "connect your provider".

    FEAT-006: optionally carries a structured ``attempts`` list of
    :class:`tinyassets.providers.diagnostics.ProviderAttemptDiagnostic`
    and a ``chain_state`` dict so callers can diagnose *why* each
    provider was skipped (auth_invalid / quota_or_cooldown /
    endpoint_unreachable / etc) rather than parse the human-readable
    message. Both fields default to ``None`` for backward compatibility
    with pre-FEAT-006 raise sites that pass only a message.
    """

    NO_WIDENING_MESSAGE = "authority forbids fallback widening"

    def __init__(
        self,
        *args,
        attempts=None,
        chain_state=None,
        failure_class=None,
        retry_after=None,
        capacity_scope=None,
        native_evidence=(),
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        # list[ProviderAttemptDiagnostic] | None
        self.attempts = attempts
        # dict | None — typically built via diagnostics.build_chain_state
        self.chain_state = chain_state
        # str | None — the dominant streamed-attempt failure_class of the last
        # failed attempt, so a single-provider (served / pinned / armed) chain
        # can surface an honest, class-specific notice (timeout != capacity).
        self.failure_class = failure_class
        # float | None — provider-supplied retry-after (seconds) for a rate-limit
        # / overload outcome, carried through for the user-facing notice.
        self.retry_after = retry_after
        self.capacity_scope = capacity_scope
        # Private local executor evidence aligned with attempts; not a public
        # provider diagnostic field, credential, or instruction to retry.
        self.native_evidence = native_evidence


# ---------------------------------------------------------------------------
# Graph / checkpoint errors
# ---------------------------------------------------------------------------

class CheckpointError(FantasyAuthorError):
    """Failed to save or load a LangGraph checkpoint."""


class GraphCompilationError(FantasyAuthorError):
    """A StateGraph could not be compiled (topology issue)."""


# ---------------------------------------------------------------------------
# State / validation errors
# ---------------------------------------------------------------------------

class StateValidationError(FantasyAuthorError):
    """State dict is missing required keys or has invalid types."""


class ConstraintViolationError(FantasyAuthorError):
    """ASP solver reported an unsatisfiable model (world rule breach)."""


class ContextBundleOverflowError(FantasyAuthorError):
    """MemoryManager could not trim a ContextBundle under the token budget.

    Raised when iterative trim + string-body truncation both fail to bring
    the bundle under ``MAX_CONTEXT_TOKENS``. Surfaces loudly so callers know
    the bundle is unsafe for LLM dispatch (rather than silently passing
    an over-budget payload that the model will truncate mid-stream).
    """


class StorageCapExceeded(FantasyAuthorError):
    """Per-subsystem storage hard cap reached; new writes refused.

    Raised by ``tinyassets.storage.caps.enforce_write_cap`` when a
    subsystem's on-disk size meets or exceeds its configured hard cap.
    Surfaces loudly (Hard Rule #8) so the 2026-04-23 silent-fill class
    cannot recur under the Phase-3 cap regime: write-site callers must
    either handle the raise (e.g. rotate older artifacts first) or
    propagate it to operator paging.
    """
