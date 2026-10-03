"""One ceiling for every tool result handed to a served agent.

Live 2026-09-26 (turn ``8dc8ada56b8e4d1cbfd2e4f37a111e7d``, a free-model
universe): the agent was asked to build a custom UI, called
``read_graph target="model_options"``, and the server handed back **1,274,067
bytes** -- the entire provider model catalogue. That one result did not fit the
selected model's context, the turn was abandoned after five rounds, and the
owner was told "we could not identify why". ``read_graph target="status"``
(32.6 KB) and a ``read`` (17.6 KB) rode along in the same turn.

The platform chooses how big a tool result is, so the platform owns the bound.
This module is the bound: pure string arithmetic, no I/O and no provider
knowledge, applied in ONE middleware (``engine_mcp_server``) so every served
tool -- including one added tomorrow -- passes through it.

**Never silent.** Over the ceiling the agent gets a JSON envelope carrying
``truncated: true``, the original byte count, and one line naming the parameters
that narrow *that* tool's read. An agent that can see it was truncated can
narrow its next call; one handed a quietly clipped catalogue cannot, and will
report the clipped view as the whole truth.
"""

from __future__ import annotations

import json
import os

#: Bytes a single tool result may occupy when the selected model's context
#: window is not known at dispatch. Sized to leave a small-context free model
#: room for the conversation plus several more tool calls in the same turn: the
#: turn above had made five rounds before it died, so a ceiling that only fits
#: one result is not a working turn either.
DEFAULT_CEILING_BYTES = 24_576

#: Floor and cap on a context-derived ceiling. The floor keeps a tiny-context
#: model from being handed results too small to carry a useful row; the cap keeps
#: a huge-context model from re-opening the same failure with a megabyte result,
#: because the context window is shared with everything else in the turn.
MIN_CEILING_BYTES = 4_096
MAX_CEILING_BYTES = 262_144

#: Share of the selected model's context window one tool result may claim. The
#: rest belongs to the system prompt, the conversation, the agent's own output
#: and the other tool calls of the same turn.
CONTEXT_SHARE = 0.15

#: Conservative bytes-per-token for the JSON these results are made of. Used
#: only to turn a token-denominated context window into a byte ceiling; an
#: under-estimate here makes the ceiling smaller, which is the safe direction.
BYTES_PER_TOKEN = 3

#: ``(tool, target)`` reads whose contract IS exact bytes, where a ceiling
#: corrupts the answer rather than bounding it.
#:
#: ``read_graph target="run_file"`` returns base64 of an owned run-bound file plus
#: the ``next_offset`` cursor to continue. Truncating it destroys the bytes AND the
#: cursor, so the agent cannot even page to recover -- measured 2026-09-26 on the
#: real dispatch path: a 256 KB chunk (a 349,681-byte reply) came back as a
#: 24,576-byte marker carrying neither ``bytes_base64`` nor ``next_offset``. The
#: caller already bounds this read with ``file_max_bytes`` (default 524288,
#: maximum 1048576); that parameter is the contract governing its size, and a
#: second ceiling layered over it only breaks the first one.
#:
#: This is about a CONTRACT, not about size. Nothing belongs here because it is
#: merely big -- being big is what the ceiling is for. A read earns a place here
#: only by being unusable when partial.
EXACT_BYTE_READS = frozenset({("read_graph", "run_file")})

#: Env override for the ceiling, in bytes. A deploy-level escape hatch.
CEILING_ENV = "TINYASSETS_ENGINE_RESULT_CEILING_BYTES"
#: The selected model's context window in tokens, when the caller that spawned
#: this server knew it. Absent on the persistent HTTP transport, where the
#: server outlives any one turn's model choice -- hence the safe default.
CONTEXT_TOKENS_ENV = "TINYASSETS_ENGINE_MODEL_CONTEXT_TOKENS"

#: How to ask each tool for less, in its OWN parameters. The hint is the whole
#: value of the marker: "too big" tells the agent to give up, while "pass
#: query=... and read the next page" tells it what to do next.
NARROWING_HINTS = {
    "read_graph": (
        "use this target's documented selectors (query=... where supported); "
        "pass a returned next_offset as output_offset only where documented; "
        "a target without a cursor does not promise offset paging"
    ),
    "read": (
        "read less of this file at a time: pass offset and limit to page "
        "through it instead of reading the whole file"
    ),
    "bash": (
        "narrow the command's own output (a filter, a head/tail, a count) "
        "rather than asking for everything and reading it here"
    ),
    "browse_commons": (
        'narrow with query and limit; agents/packages support returned next_offset'
    ),
}
_GENERIC_HINT = (
    "ask this tool for less: use its query/filter parameters, or its "
    "offset/limit parameters to page through the result"
)

#: Corrections allowed while fitting the envelope to the ceiling. One is enough
#: by construction; the spares exist so a pathological escape ratio degrades to a
#: smaller head rather than to an over-ceiling result.
_SHRINK_ATTEMPTS = 4

_MARKER_NOTE = (
    "This tool result was larger than the ceiling on a single result, so only "
    "the first bytes are below. It is NOT the whole answer -- do not report it "
    "as complete."
)


def narrowing_hint(tool: str) -> str:
    """The one line telling this tool's caller how to ask for less."""
    return NARROWING_HINTS.get(tool, _GENERIC_HINT)


def ceiling_exempt(tool: str, arguments: object, exempt=EXACT_BYTE_READS) -> bool:
    """True when this exact ``(tool, target)`` read must not be bounded.

    ``exempt`` is a parameter because the two model-door surfaces owe different
    sets: the engine exempts only the exact-byte reads, while the connector also
    exempts its caller-bounded conversation chunk and the universe's committed
    reply (``universe_server._connector_ceiling_exempt``). Neither set is a place to
    put something for being big, nor for an owner's screen: the owner's app reads
    through the owner door (``tinyassets/owner_door``), which has no ceiling.

    A read with no ``target`` argument, or an unreadable one, is NOT exempt:
    defaulting to exempt would mean any call the middleware cannot parse escapes
    the ceiling, which is the failure this whole module exists to prevent.
    """
    if not isinstance(arguments, dict):
        return False
    target = arguments.get("target")
    if not isinstance(target, str):
        return False
    return (tool, target.strip().lower()) in exempt


def ceiling_for_context(context_tokens: object) -> int:
    """Bytes one result may claim of a context window, or the safe default.

    A window we cannot read is not a licence for an unbounded result, so an
    absent, malformed or non-positive token count returns the default rather
    than no ceiling at all.
    """
    if type(context_tokens) is not int or context_tokens <= 0:
        return DEFAULT_CEILING_BYTES
    scaled = int(context_tokens * BYTES_PER_TOKEN * CONTEXT_SHARE)
    return max(MIN_CEILING_BYTES, min(MAX_CEILING_BYTES, scaled))


def resolve_ceiling(env: dict[str, str] | None = None) -> int:
    """The ceiling for this dispatch: explicit override, else context, else default.

    Reads the environment on every call rather than caching, so a redeploy that
    changes the override takes effect without restarting the engine server.
    """
    source = os.environ if env is None else env
    raw = (source.get(CEILING_ENV) or "").strip()
    if raw:
        try:
            override = int(raw)
        except ValueError:
            override = 0
        if override > 0:
            return max(MIN_CEILING_BYTES, min(MAX_CEILING_BYTES, override))
    tokens = (source.get(CONTEXT_TOKENS_ENV) or "").strip()
    try:
        return ceiling_for_context(int(tokens) if tokens else None)
    except ValueError:
        return DEFAULT_CEILING_BYTES


def page_to_fit(rows, *, start, budget, build, render, max_rows=None):
    """The longest page ``rows[start:end]`` whose rendering fits ``budget`` bytes.

    The other half of the ceiling: a read that pages ITSELF to fit is never cut,
    so its cursor survives. ``build(page, next_offset)`` makes the document
    (``next_offset`` is ``None`` on the last page) and ``render`` turns it into
    the exact text the surface returns, so "fits" is measured on real bytes.

    A page always carries at least one row when one remains: a single row over
    the budget is returned alone (the ceiling's marker then says so) rather than
    skipped, because skipping it would hide it behind a cursor that never stops
    on it.
    """
    start = max(0, int(start or 0))
    end = min(start, len(rows))
    cap = len(rows) if max_rows is None else start + max(1, int(max_rows))
    document = build(rows[start:end], end if end < len(rows) else None)
    while end < min(len(rows), cap):
        nxt = end + 1
        trial = build(rows[start:nxt], nxt if nxt < len(rows) else None)
        if end > start and len(render(trial).encode("utf-8")) > budget:
            break
        document, end = trial, nxt
    if end == start + 1 and len(render(document).encode("utf-8")) > budget:
        # One row bigger than the whole budget: clip its long strings so the
        # page, and the cursor past it, still arrive intact.
        nxt = end if end < len(rows) else None
        document = build(
            [clip_to_fit(rows[start], budget=budget,
                         render=lambda row: render(build([row], nxt)))], nxt,
        )
    return document


#: Key a clipped row carries: dotted path -> the string's original length.
CLIPPED_KEY = "clipped_chars"


def _clip(value, limit: int, path: str, clipped: dict):
    if isinstance(value, str):
        if len(value) > limit:
            clipped[path] = len(value)
            return value[:limit]
        return value
    if isinstance(value, dict):
        return {k: _clip(v, limit, f"{path}.{k}" if path else str(k), clipped)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, limit, f"{path}[{i}]", clipped) for i, v in enumerate(value)]
    return value


def clip_to_fit(row, *, budget: int, render):
    """``row`` with every string cut to the longest length that fits ``budget``.

    Never silent: a dict row gains ``clipped_chars`` naming each cut string and
    its full length, so the reader knows exactly what is partial (the owner's
    app reads the complete row). A row that fits is returned unchanged.
    """
    if len(render(row).encode("utf-8")) <= budget:
        return row
    low, high, best = 0, max(_longest(row), 1), None
    while low <= high:
        mid = (low + high) // 2
        clipped: dict = {}
        trial = _clip(row, mid, "", clipped)
        if isinstance(trial, dict):
            trial = {**trial, CLIPPED_KEY: clipped}
        if len(render(trial).encode("utf-8")) <= budget:
            best, low = trial, mid + 1
        else:
            high = mid - 1
    if best is None:
        clipped = {}
        best = _clip(row, 0, "", clipped)
        if isinstance(best, dict):
            best = {**best, CLIPPED_KEY: clipped}
    return best


def _longest(value) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return max((_longest(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return max((_longest(v) for v in value), default=0)
    return 0


def _head(text: str, budget: int) -> str:
    """The first ``budget`` bytes of ``text`` as UTF-8, cut on a character."""
    if budget <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text
    return encoded[:budget].decode("utf-8", errors="ignore")


def bound_tool_text(text: str, *, tool: str, limit: int) -> str | None:
    """Return the truncation envelope for an oversized result, else ``None``.

    ``None`` means the result was within the ceiling and must be passed through
    byte-for-byte -- bounding is not reformatting, and a result that fits is
    never rewritten.

    The envelope is JSON, and its verbatim head is budgeted so the whole thing
    fits ``limit``. A head of zero bytes still returns the marker, because "your
    result did not fit" is information the agent needs even when none of the
    content survives.

    **The marker fields themselves are a floor, so a ``limit`` smaller than they
    are yields an envelope larger than ``limit``** — roughly 430 bytes with no
    content at all. That is deliberate: reporting the truncation matters more than
    honouring an impossible budget, and silently returning nothing would be the one
    outcome worse than either. It is unreachable in practice because
    ``resolve_ceiling`` clamps to ``MIN_CEILING_BYTES`` (4096); callers passing
    ``limit`` directly should stay above the floor.
    """
    if not isinstance(text, str):
        return None
    original = len(text.encode("utf-8"))
    if limit <= 0 or original <= limit:
        return None
    marker = {
        "truncated": True,
        "tool": tool,
        "original_bytes": original,
        "ceiling_bytes": limit,
        "returned_bytes": 0,
        "note": _MARKER_NOTE,
        "hint": narrowing_hint(tool),
        "content": "",
    }
    # Every marker field is measured, ``returned_bytes`` included -- leaving one
    # out means the first render overshoots and the head is cut far smaller than
    # the ceiling allowed. Verified against the loopback HTTP route: the omission
    # returned 12,875 of an allowed 24,576 bytes.
    overhead = len(json.dumps(marker, ensure_ascii=False).encode("utf-8"))
    budget = limit - overhead
    # JSON escaping grows the head past its raw byte budget, so shrink by the
    # measured overflow. Removing N raw bytes removes at least N rendered bytes,
    # so one correction suffices; the rest of the attempts absorb a digit-count
    # change in ``returned_bytes`` itself.
    for _ in range(_SHRINK_ATTEMPTS):
        if budget <= 0:
            break
        marker["content"] = _head(text, budget)
        marker["returned_bytes"] = len(marker["content"].encode("utf-8"))
        rendered = json.dumps(marker, ensure_ascii=False)
        overflow = len(rendered.encode("utf-8")) - limit
        if overflow <= 0:
            return rendered
        budget -= max(1, overflow)
    marker["content"] = ""
    marker["returned_bytes"] = 0
    return json.dumps(marker, ensure_ascii=False)
