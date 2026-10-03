"""Structured, non-authorizing failure evidence for retained conversation.

A failed served turn is recorded as FIELDS, and every notice -- the live one
and the one history re-renders -- is composed from them, so a new failure never
needs new copy (founder, 2026-09-24):

* ``code``: the class, from a small closed set derived from transport facts;
* ``stage``: the fixed pipeline position where the turn stopped;
* ``effects``: ``none`` / ``some`` / ``unknown``, read from the turn's own
  effects ledger, never guessed. "Actions may already have occurred" is said
  only when effects are not ``none``;
* ``provider_detail``: the source's own error text, secret- and path-scrubbed
  and bounded -- the owner reading their own universe's failure;
* ``ref``: the turn (or run) id, which the server log line carries too.

Nothing here grants authority or is replayed as an instruction.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace

STAGES = ("before_send", "connection", "model_request", "model_reply", "tool", "platform")
EFFECTS = ("none", "some", "unknown")

_STAGE_WORDS = {
    "before_send": "Before anything reached your model",
    "connection": "Connecting to your model's provider",
    "model_request": "Waiting on your model's provider",
    "model_reply": "Reading your model's reply",
    "tool": "Running one of your command center's tools",
    "platform": "Inside TinyAssets",
}
_NO_STAGE = "Your agent's turn stopped"

#: The class in words: one clause per class, composed -- never a whole notice.
_CLASS_WORDS = {
    "provider_idle_timeout": (
        "your model went quiet mid-turn, so the turn was ended and whatever it "
        "finished before that stands; asking it to continue is usually better "
        "than sending the whole request again"
    ),
    "interactive_deadline": (
        "the turn ran past the interactive time limit, so it was ended and "
        "whatever it finished before that stands; asking it to continue is "
        "usually better than sending the whole request again"
    ),
    "provider_rate_limited": (
        "your model provider is rate-limiting it right now; try another connected "
        "source or check the provider's limits and send again when capacity is available"
    ),
    "provider_daily_quota": (
        "this model source has reached its daily quota; connect another free "
        "source, or add credit at that provider"
    ),
    "provider_credit_exhausted": (
        "this model source has run out of credit; connect another free source, "
        "or add credit at that provider"
    ),
    "provider_overloaded": (
        "your model provider is overloaded right now; nothing is wrong with your "
        "setup, so send again in a minute"
    ),
    "auth_invalid": (
        "your model provider reported a sign-in problem; check this command center's "
        "connection and reconnect the provider if needed. This is not evidence "
        "of a usage or billing limit"
    ),
    "endpoint_unreachable": (
        "your command center could not reach its model provider; that is a network or "
        "service problem rather than anything you set up wrong, so send again shortly"
    ),
    "platform_fault": (
        "something broke on our side; this is not a problem with your account, "
        "your credentials or your usage limits, and sending again may well work"
    ),
    "quota_or_cooldown": (
        "this model source is in a cooldown window after a provider refusal; "
        "try another connected source or check the provider's limits"
    ),
    "timed_out": "the model attempt timed out",
    "native_auth_clue": (
        "your model provider reported a sign-in problem; check this command center's "
        "provider connection and reconnect if needed, though we have not "
        "confirmed that was the only cause"
    ),
    "setup_required": (
        "this command center has no model it can use right now; connect a model, or "
        "reconnect one that stopped working, from the request under “Waiting on "
        "you”, then send your request again"
    ),
    "provider_protocol_error": (
        "the connected model replied in a format this command center could not read; "
        "try again, or choose another model"
    ),
    "provider_reply_error": (
        "your model's provider reported an error partway through its reply (its "
        "own words are below), and trying it again and your other accepted "
        "models did not get past it. Whatever the turn finished before that "
        "stands, so asking it to continue usually works; choosing another "
        "model also helps"
    ),
    "provider_unreadable_reply": (
        "the connected model sent a reply this command center could not read, "
        "and trying it again and your other accepted models did not get past "
        "it. Whatever the turn finished before that stands, so asking it to "
        "continue usually works; choosing another model also helps"
    ),
    "provider_stalled": (
        "your model stopped sending partway through its reply, and asking it "
        "again did not get past it. A slow reply is never cut off; this one went "
        "silent. Whatever the turn finished before that stands, so asking it to "
        "continue usually works; choosing another model also helps"
    ),
    "provider_reply_timeout": (
        "your model took longer to answer than this command center waits for one "
        "reply, so that request was ended and whatever the turn finished before "
        "it stands. Asking it to continue, or to do the rest in smaller steps, "
        "usually works; a faster model also helps"
    ),
    "provider_refused": (
        "your model provider refused to serve this model to this command center, "
        "before it produced anything; that is an access decision on the "
        "provider's side, not a reply we failed to read. Its own words are below. "
        "Choose another model, or check that model's access and privacy settings "
        "in your account with that provider"
    ),
    "context_window_exceeded": (
        "the conversation plus what its tools read back grew larger than the "
        "selected model's context window, so the request was refused before it "
        "was sent; connecting a model with a larger context window is the fix, "
        "and starting a fresh conversation for this request also works"
    ),
    # The owner pressed Stop (``tinyassets/turn_interrupt``). Not a failure of
    # anything, so it names no cause to fix; the effects clause still says
    # whether actions ran, from the turn's own ledger.
    "interrupted": (
        "you stopped this turn, so it ended there and whatever it finished "
        "before the stop stands"
    ),
    "unknown": (
        "we could not identify why; we cannot tell whether this is a connection, "
        "usage, billing, or platform problem, so rather than guess we have "
        "recorded the details"
    ),
}
FAILURE_CODES = frozenset(_CLASS_WORDS)

#: Where each class stops the pipeline -- a transport fact, not a guess.
#: ``unknown`` has no position; the turn's own ledger may still supply one.
STAGE_OF_CLASS = {
    "setup_required": "before_send",
    # Measured by us, against the selected model's own published window, before
    # anything was sent -- so it is a before_send fact, not a provider verdict.
    "context_window_exceeded": "before_send",
    "auth_invalid": "connection",
    "native_auth_clue": "connection",
    "endpoint_unreachable": "connection",
    "provider_rate_limited": "model_request",
    "provider_daily_quota": "model_request",
    "provider_credit_exhausted": "model_request",
    "provider_overloaded": "model_request",
    "quota_or_cooldown": "model_request",
    "timed_out": "model_request",
    "provider_idle_timeout": "model_reply",
    "interactive_deadline": "model_reply",
    "provider_protocol_error": "model_reply",
    "provider_reply_error": "model_reply",
    "provider_unreadable_reply": "model_reply",
    "provider_stalled": "model_reply",
    "provider_refused": "model_request",
    "provider_reply_timeout": "model_request",
    "platform_fault": "platform",
}

_CHECK = "Check progress before sending again; sending again repeats the whole request."
_EFFECT_WORDS = {
    "none": "Nothing ran.",
    "some": "Some actions ran before it stopped. " + _CHECK,
    "unknown": (
        "We can't tell whether actions ran, so actions may already have occurred. " + _CHECK
    ),
}

DETAIL_LIMIT = 200
#: The owner's own model output a stalled reply had written, kept in their
#: notice rather than lost; bounded so the whole record stays readable.
PARTIAL_LIMIT = 1500
_REF = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
_OPTIONAL = frozenset({
    "stage", "effects", "provider_detail", "ref", "retry_after_s", "requests", "partial_text",
})
_REQUIRED = frozenset({"version", "kind", "code"})

#: A wait longer than this is not a wait, it is a different answer ("reconnect",
#: "your daily cap resets tomorrow"), so it is dropped rather than rendered as a
#: number nobody will sit through. One day, the longest window any source's
#: Retry-After has meant here.
MAX_WAIT_S = 86_400


def wait_seconds(value: object) -> int | None:
    """A whole positive bounded second count, or None. ``bool`` is not a wait."""
    if type(value) is not int or value <= 0 or value > MAX_WAIT_S:
        return None
    return value


@dataclass(frozen=True, slots=True)
class TurnFailure:
    version: int
    kind: str
    code: str
    stage: str | None = None
    effects: str = "unknown"
    provider_detail: str = ""
    ref: str = ""
    retry_after_s: int | None = None
    """Measured seconds until this source is eligible again -- the source's own
    ``Retry-After`` or the remaining window of our own cooldown gate. Never a
    guess and never a deadline we invent for a class that has no window; absent
    stays absent. Live 2026-09-25 the gate that refused the turn knew it had 120
    seconds left and the founder was told "we could not identify why"."""
    requests: int | None = None
    """Model requests the turn sent, failed ones included, from its own ledger.
    On a free tier each counts toward the daily allowance (founder, 2026-10-02:
    a retry is not free, so say what it cost)."""
    partial_text: str = ""
    """What a stalled reply had written before it stopped, bounded."""


def failure_code(value: object) -> str:
    return value if isinstance(value, str) and value in FAILURE_CODES else "unknown"


def clean_detail(value: object, limit: int = DETAIL_LIMIT) -> str:
    """One bounded, printable line; callers scrub secrets before this."""
    if not isinstance(value, str):
        return ""
    line = " ".join("".join(ch if ch.isprintable() else " " for ch in value).split())
    return line if len(line) <= limit else line[: limit - 3] + "..."


def _request_count(value: object) -> int | None:
    return value if type(value) is int and 0 < value <= 10_000 else None


def clean_partial(value: object) -> str:
    """The tail of a partial reply -- the most recent words -- on one line.

    Bounded as STORED JSON, not as characters: ``\\uXXXX`` escapes make a
    non-ASCII reply six times longer, and a record past ``read_turn_failure``'s
    4096 would be dropped whole.
    """
    line = clean_detail(value, limit=10**9)
    while len(json.dumps(line)) > PARTIAL_LIMIT + 2:
        keep = max(len(line) * PARTIAL_LIMIT // len(json.dumps(line)) - 3, 1)
        line = "..." + line[-keep:]
    return line


#: ``read_turn_failure`` drops a stored record longer than this, so a record is
#: never built longer: the partial text gives way first.
RECORD_LIMIT = 4096


def turn_failure(
    code: object, *, stage: object = None, effects: object = "unknown",
    provider_detail: object = "", ref: object = "", retry_after_s: object = None,
    requests: object = None, partial_text: object = "",
) -> TurnFailure:
    """Build a record; any field outside its closed set degrades, never raises."""
    record = _turn_failure(
        code, stage=stage, effects=effects, provider_detail=provider_detail, ref=ref,
        retry_after_s=retry_after_s, requests=requests, partial_text=partial_text,
    )
    # Measured as the store writes it (``json.dumps`` of the normalized record).
    while record.partial_text and len(json.dumps(normalize_turn_failure(record))) > RECORD_LIMIT:
        text = record.partial_text
        record = replace(record, partial_text=(
            "" if len(text) <= 64 else "..." + text[len(text) // 4 + 3:]
        ))
    return record


def _turn_failure(
    code: object, *, stage: object, effects: object, provider_detail: object,
    ref: object, retry_after_s: object, requests: object, partial_text: object,
) -> TurnFailure:
    return TurnFailure(
        version=1, kind="turn_failed", code=failure_code(code),
        stage=stage if stage in STAGES else None,
        # A universe with no model connected cannot have acted.
        effects="none" if failure_code(code) == "setup_required"
        else effects if effects in EFFECTS else "unknown",
        provider_detail=clean_detail(provider_detail),
        ref=ref if isinstance(ref, str) and _REF.fullmatch(ref) else "",
        retry_after_s=wait_seconds(retry_after_s),
        requests=_request_count(requests),
        partial_text=clean_partial(partial_text),
    )


def _coerce(value: object) -> TurnFailure:
    if isinstance(value, TurnFailure):
        return value
    normalized = normalize_turn_failure(value) if isinstance(value, dict) else None
    if normalized is not None:
        return turn_failure(**{k: v for k, v in normalized.items()
                               if k not in {"version", "kind"}})
    return turn_failure(value)


def _wait_words(seconds: int) -> str:
    """The measured wait as one sentence. Rounded up: never say "send again now"."""
    if seconds < 60:
        return f"You can send again in about {seconds} seconds."
    minutes = -(-seconds // 60)
    unit = "minute" if minutes == 1 else "minutes"
    return f"You can send again in about {minutes} {unit}."


def failure_notice(value: object) -> str:
    """Compose the notice from the record's fields; no per-failure copy."""
    failure = _coerce(value)
    # A stop is led by what happened, not by where: "Running one of your
    # universe's tools" would read as the tool failing.
    if failure.code == "interrupted":
        lead = "Interrupted"
    elif failure.code == "provider_daily_quota":
        lead = "Your model source is unavailable"
    else:
        lead = _STAGE_WORDS.get(failure.stage, _NO_STAGE)
    parts = [
        f"{lead} — {_CLASS_WORDS[failure.code]}.",
        _EFFECT_WORDS[failure.effects],
    ]
    if failure.retry_after_s is not None and failure.code != "provider_daily_quota":
        parts.append(_wait_words(failure.retry_after_s))
    if failure.requests is not None and failure.code != "interrupted":
        unit = "request" if failure.requests == 1 else "requests"
        parts.append(
            f"This turn sent {failure.requests} {unit} to your model; on a free tier "
            "each one counts toward its daily limit."
        )
    if failure.partial_text:
        parts.append(f'Before it stopped, your model had written: "{failure.partial_text}"')
    if failure.provider_detail:
        parts.append(f'Detail: "{failure.provider_detail}"')
    if failure.ref:
        parts.append(f"Ref: {failure.ref}")
    return " ".join(parts)


def normalize_turn_failure(value: object) -> dict | None:
    """The closed value: required fields exact, optional fields in their sets."""
    if isinstance(value, TurnFailure):
        value = {k: v for k, v in asdict(value).items() if v not in (None, "")}
    if not isinstance(value, dict):
        return None
    keys = set(value)
    if not _REQUIRED <= keys or keys - _REQUIRED - _OPTIONAL:
        return None
    if type(value["version"]) is not int or value["version"] != 1:
        return None
    if value["kind"] != "turn_failed":
        return None
    code = value["code"]
    if not isinstance(code, str) or code not in FAILURE_CODES:
        return None
    result = {"version": 1, "kind": "turn_failed", "code": code}
    if "stage" in value:
        if value["stage"] not in STAGES:
            return None
        result["stage"] = value["stage"]
    if "effects" in value:
        if value["effects"] not in EFFECTS:
            return None
        result["effects"] = value["effects"]
    if "provider_detail" in value:
        detail = value["provider_detail"]
        if not isinstance(detail, str) or clean_detail(detail) != detail:
            return None
        result["provider_detail"] = detail
    if "ref" in value:
        if not isinstance(value["ref"], str) or not _REF.fullmatch(value["ref"]):
            return None
        result["ref"] = value["ref"]
    if "retry_after_s" in value:
        # DROP just this field, never the record. It is a convenience -- "you can
        # send again in about N seconds" -- and rejecting the whole row over it
        # cost the owner the entire notice: stage, class, effects and ref, all
        # readable, all discarded because one number was out of range (review of
        # #3988). Every other optional field already degrades this way.
        wait = wait_seconds(value["retry_after_s"])
        if wait is not None:
            result["retry_after_s"] = wait
    # Both degrade like the wait: dropped alone, never costing the record.
    if _request_count(value.get("requests")) is not None:
        result["requests"] = value["requests"]
    partial = value.get("partial_text")
    if isinstance(partial, str) and partial and clean_partial(partial) == partial:
        result["partial_text"] = partial
    return result


def read_turn_failure(speaker: str, raw: object) -> TurnFailure | None:
    """Optional metadata is read-only and cannot change a row's speaker."""
    if speaker != "platform" or not isinstance(raw, str) or not 0 < len(raw) <= 4096:
        return None
    try:
        value = normalize_turn_failure(json.loads(raw))
    except (ValueError, RecursionError):
        return None
    return TurnFailure(**value) if value is not None else None


def failure_column_sql(conn) -> str:
    """Fixed optional-column expression for pure readers; no schema writes."""
    columns = conn.execute("PRAGMA table_info(conversation_turns)")
    return "failure_json" if any(row[1] == "failure_json" for row in columns) else "''"


def project_failure_row(row) -> dict:
    """Drop storage representation; expose only validated platform-owned detail."""
    value = dict(row)
    raw = value.pop("failure_json", "")
    failure = normalize_turn_failure(read_turn_failure(value.get("speaker", ""), raw))
    if failure is not None:
        value["failure"] = failure
    return value
