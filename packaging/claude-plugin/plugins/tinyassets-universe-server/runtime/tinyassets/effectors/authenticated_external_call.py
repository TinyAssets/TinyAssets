"""The ONE generic outbound channel primitive (Pipedream model).

The PLATFORM knows NOTHING about any specific channel. There is no per-service
code here — no hard-coded hostname, no per-channel auth-header assembly, no
per-service branch. A universe adds ANY outbound integration — even an API this
platform has never heard of — with ZERO platform changes, by:

  1. Creating an ``http`` outbound connection (the Pipedream "connected
     account"): a vault ``credential_ref`` + an ``auth_scheme``
     (bearer/basic/header/oauth1a/none) + a per-endpoint egress allowlist
     (host + path_template + methods, including the ``{name+}`` multi-segment
     tail). See ``storage/outbound_connections.ConnectionLedger``.
  2. Granting that connection to the universe (``grant_effector_consent`` /
     ``grant_connection``).
  3. Emitting an ``authenticated_external_call`` packet from a node that
     DESCRIBES the actual API request.

The packet is fully user-specified; the platform validates SHAPE, not channel:

    {
      "sink": "authenticated_external_call",
      "connection_id": "<the http connection to use>",
      "grant_id":      "<the grant binding that connection to THIS universe>",
      "verb":          "<the connection scope string — for http, the HTTP method>",
      "request": {
        "method":  "POST",           # optional; must equal ``verb`` if present
        "host":    "api.example.com", # optional; falls back to the connection's
                                      #   single allowlisted host
        "path":    "/v1/messages",    # request path (matched by the allowlist)
        "query":   {"k": "v"},        # optional; validated by the allowlist
        "headers": {"X-Extra": "1"},  # optional NON-auth headers
        "header_name": "X-Api-Key",   # optional; only for the "header" auth scheme
        "body":    {...} | "..."      # optional; dict/list json-encoded by worker
      }
    }

BODY TRANSFORMS. Besides shape, this effector knows exactly two more things:
one encoding, and the run it belongs to. Anywhere in ``request.body`` a
one-key object whose key is one of the five names below is applied before the
request leaves - so a node writes TEXT and references BYTES, and never
hand-produces base64 or re-types a file (live 2026-08-29: model-generated
base64 came back ``422 not valid Base64``, then as valid base64 of a
transcribed file that lost 87 lines, then a "repair" that re-typed the file
with 36 differences). The ``$ta.`` namespace is RESERVED: a user's own API
keys (``$ref`` of JSON Schema, ``$set``) are never interpreted, and an unknown
``$ta.*`` spelling is refused rather than sent as payload.

    {"$ta.base64": X}         the base64 of X's UTF-8 bytes
    {"$ta.from_base64": X}    the UTF-8 text of base64 X (whitespace-tolerant;
                              bytes that are not UTF-8 text are refused - this
                              is for text files)
    {"$ta.ref": "key.a.0.b"}  a value from the run's state; the root ``key``
                              MUST be one of the emitting node's declared
                              ``input_keys`` or a state_schema-defaulted key
                              (narrower than the compiler's render view on
                              purpose: an effect reads less, never more);
                              JSON-encoded strings are traversed, lists by index
    {"$ta.effect": "node.response.body.x"}
                              from the evidence of an EARLIER node's effect in
                              the SAME run - only ``response.body`` (the
                              sanitized text the worker returned, traversed
                              as JSON) and ``response.status``; never headers.
                              "Earlier" is the order nodes are STORED in the
                              branch (``write_graph`` appends in the order
                              given), which is the order effects fire in
    {"$ta.concat": [X, ...]}  the texts joined

X may itself be a transform. Bounds, all refused as ``invalid_body_transform``
before anything is sent: nesting deeper than 32 anywhere in the body; a
cumulative working set over ``_MAX_TRANSFORM_WORK_BYTES`` (charged in bytes
as each value is produced - text as UTF-8, referenced objects as their JSON -
so a reference repeated a hundred times is refused as soon as the charges
cross the budget, not after allocating them all); a transformed body over
``_MAX_TRANSFORMED_BODY_BYTES``. So "append one line to a fetched text file"
is a two-node branch: ``fetch`` (stored first) emits a GET packet; ``write``
emits

    {"sha":     {"$ta.effect": "fetch.response.body.sha"},
     "content": {"$ta.base64": {"$ta.concat": [
                    {"$ta.from_base64": {"$ta.effect": "fetch.response.body.content"}},
                    "the new line\n"]}}}

and the model authored only the line - in ONE run, so the fetched bytes never
pass through a model or a second ``run_graph``. Refusals are whole: a wrong
type, an unfenced or unresolvable path, or any bound above returns the
secret-free ``invalid_body_transform`` error and nothing is sent. A body with
no ``$ta.*`` transform is sent byte-for-byte as before (its own ``$``-keys
included).

CREDENTIAL-BLINDNESS. This effector NEVER resolves or sees the credential. It
resolves an exact scoped proxy under the universe's own authority and hands the
wire request to ``proxy.request(verb, request)``. The credential is applied
INSIDE the spawned broker worker (``_run_proxy_worker`` +
``_SsrfHardenedHttpDriver``); the secret never exists in this process. The
effector returns the worker's sanitized ``{status, reason, headers, body}`` as
evidence, or a secret-free error dict — it NEVER raises to the run-completion
path.

ISOLATION. ``universe_id`` is derived from server-owned run context
(``base_path``), never the packet. A packet may only name a grant that is bound
to the universe RUNNING the graph: the effector refuses any grant whose
``universe_id`` does not match, so a copied/remixed graph cannot borrow another
universe's connections. The authenticated principal is the grant's OWN stored
owner — read from the trusted grant row, gated by that universe match — never a
payload-supplied identity.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import urllib.parse
from pathlib import Path
from typing import Any

from tinyassets.effectors.authority import DENIED as SOUL_AUTHORITY_DENIED
from tinyassets.effectors.authority import resolve_soul_effect_authority

logger = logging.getLogger(__name__)

#: The one generic sink. A node that declares ``effects=[…this…]`` and emits a
#: matching packet routes here regardless of which external service it targets.
EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL = "authenticated_external_call"


# --------------------------------------------------------------------------- #
# Packet reading (shape only — no channel knowledge)
# --------------------------------------------------------------------------- #
def _parse_packet(value: Any) -> dict[str, Any] | None:
    """Return the packet dict iff ``value`` is a matching packet, else None."""
    if isinstance(value, dict):
        packet = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or not stripped.startswith("{"):
            return None
        try:
            packet = json.loads(stripped)
        except (TypeError, ValueError):
            return None
        if not isinstance(packet, dict):
            return None
    else:
        return None
    if packet.get("sink") != EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL:
        return None
    return packet


def _find_packet(
    *,
    output_keys: list[str],
    run_state: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None]:
    for key in output_keys or []:
        if not isinstance(key, str) or key not in run_state:
            continue
        packet = _parse_packet(run_state.get(key))
        if packet is not None:
            return key, packet
    return None, None


def packet_verb(*, output_keys: list[str], run_state: dict[str, Any]) -> str | None:
    """The verb a node's packet declares (``verb`` or ``request.method``), or
    None when there is no parseable packet, no verb, or the two disagree. Used
    by the dispatcher to classify an effect that was refused before the wire."""
    _key, packet = _find_packet(output_keys=output_keys, run_state=run_state)
    if packet is None:
        return None
    verb = _str_field(packet, "verb")
    request = packet.get("request")
    method = _str_field(request, "method") if isinstance(request, dict) else ""
    if verb and method and verb.upper() != method.upper():
        return None
    return (verb or method or "").strip() or None


def packet_accept_statuses(*, output_keys: list[str], run_state: dict[str, Any]) -> set[int]:
    """The far-side statuses (>= 400) a node's packet declared as acceptable
    data rather than failure: ``"accept_statuses": [404]`` at the packet's top
    level (design D1: probe-then-branch). Anything else >= 400 fails the node.
    Non-integers are ignored; an absent field is the empty set."""
    _key, packet = _find_packet(output_keys=output_keys, run_state=run_state)
    if packet is None:
        return set()
    raw = packet.get("accept_statuses")
    if not isinstance(raw, list):
        return set()
    return {int(v) for v in raw if isinstance(v, int) and not isinstance(v, bool)}


def _str_field(source: dict[str, Any], key: str) -> str:
    value = source.get(key)
    return value.strip() if isinstance(value, str) else ""


# --------------------------------------------------------------------------- #
# Body transforms - one encoding and the run's own data, applied before the wire
# --------------------------------------------------------------------------- #
_OP_PREFIX = "$ta."
_OP_BASE64 = "$ta.base64"
_OP_FROM_BASE64 = "$ta.from_base64"
_OP_REF = "$ta.ref"
_OP_EFFECT = "$ta.effect"
_OP_CONCAT = "$ta.concat"
# Replace ONE exact occurrence inside fetched text - the edit shape. Live
# 2026-08-30: asked to change one line of README, the universe re-typed the
# whole file through a model and the result dropped every blank line and ate
# a backslash (PR #2714, closed). With replace, the model authors only the old
# and the new line; the bytes around them never pass through it.
_OP_REPLACE = "$ta.replace"
_TRANSFORM_OPS = (_OP_BASE64, _OP_FROM_BASE64, _OP_REF, _OP_EFFECT, _OP_CONCAT, _OP_REPLACE)
_MAX_TRANSFORM_DEPTH = 32
#: The transformed body is serialized in-process before the worker frames it
#: (the frame itself is bounded at 16 MiB downstream).
_MAX_TRANSFORMED_BODY_BYTES = 8 * 1024 * 1024
#: Every value a transform PRODUCES is charged (in characters) against this
#: working-set budget BEFORE the next allocation, so a body that references a
#: large fetched blob many times is refused at the second copy instead of
#: after materialising them all (Codex round 2, P0). A legitimate append to a
#: 3.6 MiB text file (the largest a 5 MiB wrapped response can carry) costs
#: about 13 MiB of working set: reference + decode + join + encode.
_MAX_TRANSFORM_WORK_BYTES = 32 * 1024 * 1024
#: The evidence that is PERSISTED (and later shown to a model through
#: ``read_graph target="run"``) keeps only this much of a response body and
#: no header values; the full response is still available to later nodes'
#: transforms in the same run.
_EVIDENCE_BODY_PREVIEW_CHARS = 4096
_EFFECT_READABLE = ("response.body", "response.status")


class _TransformError(ValueError):
    """A malformed transform. The message names paths and types, never values."""


def _is_transform(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and len(value) == 1
        and next(iter(value)) in _TRANSFORM_OPS
    )


def _reserved_misuse(value: Any) -> str | None:
    """The offending key when a dict uses the reserved ``$ta.`` namespace in
    any way other than a one-key object naming an operator: an unknown name,
    or an operator key sitting beside other keys (Codex round 3, P2)."""
    if not isinstance(value, dict):
        return None
    for key in value:
        if isinstance(key, str) and key.startswith(_OP_PREFIX):
            if key not in _TRANSFORM_OPS or len(value) != 1:
                return key
    return None


def _scan_body(body: Any) -> bool:
    """True when a transform is present anywhere. Iterative (no recursion
    limit to trip) and depth-bounded: a body nested deeper than
    ``_MAX_TRANSFORM_DEPTH`` is refused whether or not it holds a transform,
    and a reserved-but-unknown ``$ta.*`` key is refused here too."""
    found = False
    stack: list[tuple[Any, int]] = [(body, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > _MAX_TRANSFORM_DEPTH:
            raise _TransformError(f"body nested deeper than {_MAX_TRANSFORM_DEPTH}")
        misuse = _reserved_misuse(value)
        if misuse is not None:
            raise _TransformError(
                f"unknown transform {misuse!r}"
                if misuse not in _TRANSFORM_OPS
                else f"{misuse!r} must be the only key of its object"
            )
        if _is_transform(value):
            found = True
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
    return found


def _walk(path: str, root: Any, *, label: str) -> Any:
    """Descend a dotted path; JSON-encoded strings are traversed, lists by index."""
    node: Any = root
    walked: list[str] = []
    for part in path.split("."):
        here = ".".join(walked) or "<root>"
        if isinstance(node, str):
            stripped = node.strip()
            if stripped[:1] not in ("{", "["):
                raise _TransformError(f"{label} {path!r}: value at {here} is text, not JSON")
            try:
                node = json.loads(stripped)
            except ValueError as exc:
                raise _TransformError(f"{label} {path!r}: value at {here} is not JSON") from exc
        if isinstance(node, dict):
            if part not in node:
                raise _TransformError(f"{label} {path!r}: no key {part!r} at {here}")
            node = node[part]
        elif isinstance(node, list):
            try:
                index = int(part)
            except ValueError as exc:
                raise _TransformError(
                    f"{label} {path!r}: {part!r} is not a list index at {here}"
                ) from exc
            if not -len(node) <= index < len(node):
                raise _TransformError(f"{label} {path!r}: index {index} out of range at {here}")
            node = node[index]
        else:
            raise _TransformError(
                f"{label} {path!r}: cannot descend into {type(node).__name__} at {here}"
            )
        walked.append(part)
    return node


def _split_root(path: Any, label: str) -> tuple[str, str]:
    if not isinstance(path, str) or not path.strip():
        raise _TransformError(f"{label} must be a non-empty dotted path")
    root, _, rest = path.partition(".")
    return root, rest


class _TransformContext:
    """What a packet may read, and how much work it may cause."""

    __slots__ = ("run_state", "allowed_state_keys", "prior_effects", "work")

    def __init__(self, run_state, allowed_state_keys, prior_effects):
        self.run_state = run_state or {}
        self.allowed_state_keys = None if allowed_state_keys is None else set(allowed_state_keys)
        self.prior_effects = prior_effects or {}
        self.work = 0

    def charge(self, nbytes: int) -> None:
        self.work += max(int(nbytes), 0)
        if self.work > _MAX_TRANSFORM_WORK_BYTES:
            raise _TransformError(
                f"transforms would produce more than {_MAX_TRANSFORM_WORK_BYTES} "
                "bytes in total"
            )

    def produced(self, value: Any) -> Any:
        """Charge a produced value by its size in BYTES: text as UTF-8, anything
        else as its JSON encoding (a referenced object is serialized into the
        body later, so it counts now). A value JSON cannot carry is refused
        here, not at the final dump (Codex round 3, P0/P1)."""
        self.charge(_byte_size(value))
        return value

    def ref(self, path: Any) -> Any:
        root, rest = _split_root(path, _OP_REF)
        if self.allowed_state_keys is None:
            raise _TransformError(
                f"{_OP_REF} {path!r}: this node declares no readable state keys"
            )
        if root not in self.allowed_state_keys:
            raise _TransformError(
                f"{_OP_REF} {path!r}: {root!r} is not among the node's declared "
                "input_keys (or schema-defaulted keys)"
            )
        if root not in self.run_state:
            raise _TransformError(f"{_OP_REF} {path!r}: {root!r} is not in the run's state")
        value = self.run_state[root]
        return self.produced(_walk(rest, value, label=_OP_REF) if rest else value)

    def effect(self, path: Any) -> Any:
        root, rest = _split_root(path, _OP_EFFECT)
        # A node id may itself contain dots (`fetch.v1` is a valid id): the
        # root is the LONGEST earlier node id that prefixes the path, not the
        # text before the first dot (Codex on the evidence hint).
        if isinstance(path, str):
            for nid in sorted(self.prior_effects, key=len, reverse=True):
                if path == nid or path.startswith(nid + "."):
                    root, rest = nid, path[len(nid) + 1:]
                    break
        if root not in self.prior_effects:
            raise _TransformError(
                f"{_OP_EFFECT} {path!r}: no earlier node {root!r} with an "
                f"{EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL} effect in this run"
            )
        readable = rest in ("response.status", "response.body") or rest.startswith("response.body.")
        if not readable:
            raise _TransformError(
                f"{_OP_EFFECT} {path!r}: only {' and '.join(_EFFECT_READABLE)} of an "
                "earlier effect may be referenced"
            )
        return self.produced(_walk(rest, self.prior_effects[root], label=_OP_EFFECT))


def _nearest_window(text: str, old: str, *, before: int = 60, after: int = 100) -> str | None:
    """The input around the longest prefix of ``old`` that occurs in it, so a
    refusal shows what the text really looks like where ``old`` stops
    matching. Binary search on the prefix length (a prefix that occurs implies
    every shorter one does): ~12 finds over the input, never a scan per
    character. ``None`` when too little of ``old`` occurs to point anywhere."""
    lo, hi = 0, min(len(old), 4096)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if text.find(old[:mid]) >= 0:
            lo = mid
        else:
            hi = mid - 1
    if lo < 8:
        return None
    idx = text.find(old[:lo])
    return text[max(0, idx - before) : idx + lo + after]


def _byte_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if value is None or isinstance(value, (bool, int, float)):
        return 32
    try:
        return len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise _TransformError(
            f"a referenced value of type {type(value).__name__} cannot be sent as JSON"
        ) from exc


def _as_text(value: Any, what: str) -> str:
    if isinstance(value, str):
        return value
    raise _TransformError(f"{what} must resolve to text, got {type(value).__name__}")


def _apply_transforms(value: Any, ctx: _TransformContext, depth: int = 0) -> Any:
    if depth > _MAX_TRANSFORM_DEPTH:
        raise _TransformError(f"body transforms nested deeper than {_MAX_TRANSFORM_DEPTH}")
    if _is_transform(value):
        op, arg = next(iter(value.items()))
        if op == _OP_REF:
            return ctx.ref(arg)
        if op == _OP_EFFECT:
            return ctx.effect(arg)
        if op == _OP_REPLACE:
            if not isinstance(arg, dict) or not {"in", "old", "new"} <= set(arg):
                raise _TransformError(
                    f'{_OP_REPLACE} takes {{"in": <text>, "old": <text>, "new": <text>}} '
                    '(optional "count": how many occurrences, default 1)'
                )
            extra = sorted(set(arg) - {"in", "old", "new", "count"})
            if extra:
                # A typo'd key ("countt") must not run with the default and
                # change a different number of places than the author meant
                # (Codex round 1, P1).
                raise _TransformError(
                    f"{_OP_REPLACE} does not take {', '.join(extra)}; "
                    'keys are "in", "old", "new" and optional "count"'
                )
            text = _as_text(_apply_transforms(arg["in"], ctx, depth + 1), f"{_OP_REPLACE} in")
            old = _as_text(_apply_transforms(arg["old"], ctx, depth + 1), f"{_OP_REPLACE} old")
            new = _as_text(_apply_transforms(arg["new"], ctx, depth + 1), f"{_OP_REPLACE} new")
            # count is an operand like the other three: it may come from state.
            count = _apply_transforms(arg.get("count", 1), ctx, depth + 1)
            if isinstance(count, str) and count.isdigit():
                count = int(count)
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise _TransformError(f"{_OP_REPLACE} count must be a positive integer")
            if not old:
                raise _TransformError(f"{_OP_REPLACE} old must not be empty")
            found = text.count(old)
            if found == 0:
                # The author cannot see the input (a fetched file reaches it
                # base64-encoded and truncated), so the refusal has to carry
                # the local truth: what the input really says where it
                # diverges from `old`. Live 2026-08-30: two runs guessed at
                # a newline, the file had a space there.
                near = _nearest_window(text, old)
                raise _TransformError(
                    f"{_OP_REPLACE}: old text not found in the input - it must match "
                    "exactly (including whitespace)"
                    + (
                        f"; the input near the closest partial match reads {near!r} "
                        "(newlines and spaces shown as written) - copy old from there"
                        if near
                        else "; fetch the file and copy the line"
                    )
                )
            if found != count:
                raise _TransformError(
                    f"{_OP_REPLACE}: old text occurs {found} times, count is {count}; "
                    "make old more specific (include the surrounding text) or set count"
                )
            grown = max(_byte_size(new) - _byte_size(old), 0)
            ctx.charge(_byte_size(text) + count * grown)   # bytes, not characters
            return text.replace(old, new, count)
        if op == _OP_CONCAT:
            if not isinstance(arg, list):
                raise _TransformError(f"{_OP_CONCAT} takes a list")
            parts = [
                _as_text(_apply_transforms(part, ctx, depth + 1), f"{_OP_CONCAT} part")
                for part in arg
            ]
            ctx.charge(sum(_byte_size(part) for part in parts))   # the join, before it exists
            return "".join(parts)
        if op == _OP_FROM_BASE64:
            text = _as_text(_apply_transforms(arg, ctx, depth + 1), _OP_FROM_BASE64)
            ctx.charge(len(text))                           # decoded bytes <= input chars
            try:
                raw = base64.b64decode("".join(text.split()), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise _TransformError(f"{_OP_FROM_BASE64}: not valid base64") from exc
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise _TransformError(
                    f"{_OP_FROM_BASE64}: bytes are not UTF-8 text (text files only)"
                ) from exc
        text = _as_text(_apply_transforms(arg, ctx, depth + 1), _OP_BASE64)
        raw = text.encode("utf-8")
        ctx.charge(len(raw) + (len(raw) * 4 + 2) // 3)     # the bytes + their encoding
        return base64.b64encode(raw).decode("ascii")
    if isinstance(value, dict):
        return {key: _apply_transforms(item, ctx, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_apply_transforms(item, ctx, depth + 1) for item in value]
    return value


def apply_body_transforms(
    body: Any,
    run_state: dict[str, Any] | None = None,
    *,
    allowed_state_keys: list[str] | set[str] | None = None,
    prior_effects: dict[str, Any] | None = None,
) -> tuple[Any, str | None]:
    """Return ``(body, None)`` with every transform applied, or ``(None, why)``.

    A body without transforms is returned as the SAME object, untouched.
    ``allowed_state_keys`` fences ``$ta.ref`` (``None`` = nothing readable);
    ``prior_effects`` maps earlier node ids to their effect evidence.
    """
    try:
        if not _scan_body(body):
            return body, None
        ctx = _TransformContext(run_state, allowed_state_keys, prior_effects)
        transformed = _apply_transforms(body, ctx)
        try:
            serialized = (
                transformed if isinstance(transformed, str) else json.dumps(transformed)
            )
        except (TypeError, ValueError) as exc:
            raise _TransformError("transformed body is not JSON-serializable") from exc
        size = len(serialized.encode("utf-8"))
        if size > _MAX_TRANSFORMED_BODY_BYTES:
            raise _TransformError(
                f"transformed body is {size} bytes; the bound is "
                f"{_MAX_TRANSFORMED_BODY_BYTES}"
            )
        return transformed, None
    except _TransformError as exc:
        return None, str(exc)


def bounded_evidence(result: dict[str, Any], *, node_id: str = "") -> dict[str, Any]:
    """The copy of an effect result that is PERSISTED and later shown to a
    model: a response body longer than ``_EVIDENCE_BODY_PREVIEW_CHARS`` is
    replaced by a preview plus its size and digest. The full result stays
    in memory for later nodes' ``$ta.effect`` in the same run (Codex round
    2, P1: a fetched file must not re-enter a model through read_graph).

    The preview says HOW to use the whole body, at the moment a model sees
    it is cut: live 2026-08-30 the founder's universe fetched a README in
    one run, met the preview, tried to push the text back through run_graph
    inputs and reported a "truncation loop" - the two-node fetch->write shape
    is in write_graph's docs, but the hint has to be where the failure is."""
    response = result.get("response") if isinstance(result, dict) else None
    if not isinstance(response, dict):
        return result
    body = response.get("body")
    long_body = isinstance(body, str) and len(body) > _EVIDENCE_BODY_PREVIEW_CHARS
    headers = response.get("headers")
    if not long_body and not isinstance(headers, dict):
        return result
    import hashlib

    bounded = dict(result)
    persisted = dict(response)
    if isinstance(headers, dict):
        # The worker returns every response header and only strips exact
        # credential echoes; a rotated cookie would otherwise sit in the run
        # record forever (Codex round 3, P1). Names are kept, values are not.
        persisted.pop("headers", None)
        persisted["header_names"] = sorted(str(k) for k in headers)
    if long_body:
        ref = f"{node_id or '<this node id>'}.response.body"
        persisted.update({
            "body": body[:_EVIDENCE_BODY_PREVIEW_CHARS],
            "body_truncated": True,
            "body_chars": len(body),
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "body_hint": (
                "Preview only - the full body never reaches a model. To use ALL of "
                "it, add a LATER node to the SAME branch whose packet references it "
                f"in place, e.g. {{\"$ta.effect\": \"{ref}\"}} (or "
                f"\"{ref}.<field>\", wrapped in {{\"$ta.from_base64\": ...}} / "
                "{\"$ta.base64\": ...} as the target API needs; to change one line use "
                "{\"$ta.replace\": {\"in\": ..., \"old\": ..., \"new\": ...}}), then "
                "run the branch ONCE: fetch and write happen in one run. Never copy, "
                "re-type or pass the text through run_graph inputs."
            ),
        })
    bounded["response"] = persisted
    return bounded


# --------------------------------------------------------------------------- #
# Run-context resolution (server-owned; never from the packet)
# --------------------------------------------------------------------------- #
def _universe_id(base_path: str | Path | None) -> str:
    """The universe whose authority this run binds to — from ``base_path`` only.

    ``runs.py`` passes the universe DIR as ``base_path`` (``data_root/<uid>``), so
    the trailing component IS the universe id. Never taken from the packet.
    """
    if base_path is None:
        return ""
    try:
        return Path(base_path).name.strip()
    except (TypeError, ValueError):
        return ""


def _ledger_db_path(base_path: str | Path | None) -> Path | None:
    """The outbound ledger DB lives at the DATA ROOT (``base_path.parent``)."""
    if base_path is None:
        return None
    try:
        return Path(base_path).parent / "outbound.db"
    except (TypeError, ValueError):
        return None


def _request_path(request: dict[str, Any]) -> str:
    """The path a request addresses, for classifying it (never for sending)."""
    absolute = _str_field(request, "url")
    if absolute:
        try:
            return urllib.parse.urlsplit(absolute).path or "/"
        except ValueError:
            return "/"
    path = request.get("path")
    if not isinstance(path, str) or not path.startswith("/"):
        return "/"
    # Parsed the way the transport rebuilds the URL: no query, no fragment, so
    # "/v1/charges#" classifies as the "/v1/charges" it is sent as
    # (gpt-6-astra on #4199).
    try:
        return urllib.parse.urlsplit(path).path or "/"
    except ValueError:
        return "/"


def _review_evidence(request: dict[str, Any]) -> str:
    """The request's own body, bounded, for the auto-review (untrusted there)."""
    body = request.get("body", request.get("json"))
    try:
        text = body if isinstance(body, str) else json.dumps(body, default=str)
    except (TypeError, ValueError):
        text = ""
    return (text or "")[:2000]


def _initiating_agent(universe_dir: Path) -> str | None:
    """Resolve server-owned turn/launch context; never trust packet or state ids.

    Run workers copy the request context. Native agent launches carry their
    addressed thread on the engine route instead of an in-process live turn.
    Background/legacy paths without either use the strictest stored settings.
    """
    from tinyassets import turn_interrupt
    from tinyassets.addressed_agents import agent_of_session
    from tinyassets.auth.middleware import current_identity_or_none
    from tinyassets.engine_steering import STEERED_PREFIX, _session_key

    live = turn_interrupt.current()
    if live is not None:
        return live.agent_id if live.universe_id == universe_dir.name else None
    session = _session_key()
    identity = current_identity_or_none()
    if identity is not None and session.startswith(STEERED_PREFIX):
        return agent_of_session(session[len(STEERED_PREFIX):], identity.user_id)
    return None


def _rule_refusal(universe_dir: Path, connection_id: str, verb: str,
                  path: str = "/", *, evidence: str = "",
                  agent: str | None = None, preapproved: bool = False) -> dict[str, Any] | None:
    """``None`` when the owner's rules let this call proceed, else a refusal.

    What the call MEANS comes from the owner's declarations for this connection
    (harness D1b); an undeclared operation is decided as a write.
    """
    from tinyassets import agent_review, agent_rules

    try:
        action_class, operation = agent_rules.classify(
            universe_dir, connection_id, verb, path)
        agents = [agent] if agent else sorted(
            {"main"} | agent_rules.configured_agents(universe_dir)
            | agent_review.configured_agents(universe_dir))
        decisions = [agent_rules.decide(
            universe_dir, action_class, connection=connection_id, operation=operation,
            agent=candidate,
        ) for candidate in agents]
        decision = max(decisions, key=lambda d: agent_rules.BEHAVIOURS.index(d.behaviour))
    except Exception:
        logger.exception("authenticated_external_call rule lookup crashed")
        return {
            "dry_run": True,
            "reason": "rules_unreadable",
            "error_kind": "rules_unreadable",
            "hint": "Your rules could not be read, so nothing was sent.",
        }
    if decision.proceeds or (preapproved and decision.behaviour in (
            agent_rules.ASK_FIRST, agent_rules.DO_IF_PREAPPROVED)):
        # Only an explicit owner-configured review adds a model check. Grants,
        # consent and cross-user isolation remain independent mandatory checks.
        from tinyassets.agent_review import review_refusal

        for candidate in agents:
            refusal = review_refusal(
                universe_dir,
                action={"action_class": action_class, "connection": connection_id,
                        "operation": operation, "path": path},
                rule=decision.reason, evidence=evidence, agent=candidate,
            )
            if refusal is not None:
                return refusal
        return None
    if decision.behaviour == agent_rules.HAND_OFF:
        return {
            "dry_run": True,
            "reason": "rule_hand_off",
            "error_kind": "rule_hand_off",
            "rule": decision.reason,
            "hint": ("Your owner's rules hand this action to them: ask them to do it "
                     "themselves; do not perform it."),
        }
    # Ask first, and "if pre-approved" until approvals are bound to the exact
    # action (D1c): the owner is asked before anything is sent.
    return {
        "dry_run": True,
        "reason": "rule_ask_first",
        "error_kind": "rule_ask_first",
        "rule": decision.reason,
        "hint": ("Your owner's rules ask first for this action: raise one request "
                 "describing it and continue other work until they answer."),
    }


def _check_consent(universe_dir: Path, destination: str) -> bool:
    """Whether an active effector-consent grant exists for this destination.

    A connection grant proves the universe MAY use the connection; a live
    external effect additionally requires the owner's explicit effector consent
    for the destination. Fail closed (return False) on empty destination or any
    lookup failure — a live call must never proceed on a crashed consent check.
    """
    if not destination:
        return False
    try:
        from tinyassets.storage.effector_consents import is_consent_active

        return is_consent_active(
            universe_dir,
            sink=EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
            destination=destination,
        )
    except Exception:
        logger.exception("authenticated_external_call consent lookup crashed")
        return False


# --------------------------------------------------------------------------- #
# Wire-request assembly (generic; the allowlist in the worker is the boundary)
# --------------------------------------------------------------------------- #
def _resolve_host(request: dict[str, Any], connection_view: Any) -> tuple[str, str]:
    """Return ``(host, error_kind)``; ``error_kind`` empty on success.

    Host precedence: an explicit ``request.host`` (the user fully specifies the
    call), else the connection's single allowlisted host. A connection spanning
    multiple hosts with no explicit ``host`` is ambiguous and refused — the user
    must say which. The worker's allowlist re-validates whatever host is used.
    """
    explicit = _str_field(request, "host")
    if explicit:
        return explicit, ""
    hosts = {
        ep.host
        for ep in getattr(connection_view, "allowed_endpoints", ()) or ()
        if getattr(ep, "host", "")
    }
    if len(hosts) == 1:
        return next(iter(hosts)), ""
    if not hosts:
        return "", "no_allowlisted_host"
    return "", "host_ambiguous"


def _declared_user_agent_error(headers: Any) -> str:
    """Refuse a per-CALL ``User-Agent``; it belongs on the connection.

    Every outbound call now carries the platform's own honest client string
    (``OUTBOUND_USER_AGENT``), because a UA-less request is answered by a CDN
    before the destination sees it. A service that wants a different client
    string is a property of the CONNECTION, not of one call: the owner declares
    it once through the connection's constant headers, where it is visible in
    the grant and the same for every node.

    Refused rather than dropped, and refused rather than accepted. Dropping it
    silently would leave an author believing they had set something. Accepting
    it would let a node choose what to claim to be, per call, which is how a
    platform ends up impersonating a browser to get past a bot policy on a
    node's say-so — and this platform says who it is.
    """
    from tinyassets.storage.outbound_connections import OUTBOUND_USER_AGENT_HEADER

    if not isinstance(headers, dict):
        return ""
    named = [
        str(name)
        for name in headers
        if str(name).strip().lower() == OUTBOUND_USER_AGENT_HEADER
    ]
    if not named:
        return ""
    return (
        f"request.headers may not set {named[0]!r}: every outbound call already "
        "carries this platform's own client string. If this service needs a "
        "particular one, it is a property of the connection rather than of one "
        "call — declare it in the connection's constant headers, where the "
        "owner can see it in the grant and every node sends the same thing."
    )


def _capability_url_shape_error(url: str, connection_view: Any) -> str:
    """Refuse a ``url_secret`` request that does not address the placeholder.

    The credential of a capability URL — the common incoming-webhook shape,
    where the secret is a path segment rather than a header — is substituted by
    the broker child. A node therefore writes ``…/{secret}`` and NEVER the code,
    so this effector, the run state, the evidence and the receipt only ever hold
    the placeholder form (design.md D7: redaction is structural). No channel is
    named here or anywhere in this module: the shape is what is recognised.

    Returns a secret-free message, or ``""``. The message never echoes the
    offending url: if the author did hardcode a secret, repeating it in the
    error would put it in the very run record this exists to keep clean.
    """
    from tinyassets.storage.outbound_connections import (
        _URL_SECRET_REST_TOKEN,
        _URL_SECRET_SCHEME,
        _URL_SECRET_TOKEN,
    )

    scheme = str(getattr(connection_view, "auth_scheme", "") or "").strip().lower()
    if scheme != _URL_SECRET_SCHEME:
        return ""
    path = url.partition("?")[0]
    if _URL_SECRET_TOKEN in path or _URL_SECRET_REST_TOKEN in path:
        return ""
    return (
        "this connection's secret is a path segment held in the vault, so the "
        f"request path must address it as {_URL_SECRET_TOKEN} (or "
        f"{_URL_SECRET_REST_TOKEN}) exactly as the endpoint declares it — never "
        "the code itself, which would be stored with this run"
    )


def _build_url(request: dict[str, Any], host: str) -> tuple[str, str]:
    """Return ``(url, error_kind)``. Accepts an absolute ``url`` or ``host``+``path``.

    Query params come from a ``query`` mapping (never smuggled inside ``path`` when
    a ``query`` is also supplied). The URL is built here but the real egress
    boundary is the per-connection allowlist enforced inside the worker.
    """
    absolute = _str_field(request, "url")
    if absolute:
        return absolute, ""
    path = request.get("path")
    if not isinstance(path, str) or not path:
        return "", "missing_path"
    if not path.startswith("/"):
        return "", "invalid_path"
    query = request.get("query")
    query_string = ""
    if query is not None:
        if not isinstance(query, dict):
            return "", "invalid_query"
        if "?" in path:
            return "", "query_in_path_conflict"
        query_string = urllib.parse.urlencode(
            {str(k): str(v) for k, v in query.items()}
        )
    url = f"https://{host}{path}"
    if query_string:
        url = f"{url}?{query_string}"
    return url, ""


# --------------------------------------------------------------------------- #
# Proxy seam — the ONLY place the ledger is touched. Kept small + named so the
# credential-blind spawned-worker path is the default and tests can substitute
# an in-process loopback broker for wire-request assertions (the child re-imports
# with production SSRF seams, so a monkeypatch cannot cross the spawn boundary —
# exactly why the project splits "credential-blindness through the real worker"
# from "successful wire-request assertion via an injected driver").
# --------------------------------------------------------------------------- #
def _read_connection_context(
    *, db_path: Path, grant_id: str, connection_id: str, universe_id: str,
    principal: str = "",
) -> tuple[Any, Any, str]:
    """Return ``(grant, connection_view, error_kind)`` for the admitted scope.

    Enforces the isolation gate: the grant must exist, be active, and be bound to
    the RUNNING universe. ``error_kind`` empty on success.
    """
    from tinyassets.broker.supervisor import broker_selected
    from tinyassets.storage.outbound_connections import ConnectionLedger, GrantResolutionError

    if broker_selected():
        from tinyassets.broker.ledger_queries import authorized_connection

        if not principal:
            return None, None, "no_universe_authority"
        try:
            grant, resource, _incarnation = authorized_connection(
                Path(db_path).parent, principal=principal, command_center=universe_id,
                grant_id=grant_id, connection_id=connection_id)
        except GrantResolutionError:
            return None, None, "connection_authority_unavailable"
        return grant, resource.to_view(), ""

    ledger = ConnectionLedger(db_path)
    grant = ledger.get_grant(grant_id)
    if grant is None:
        return None, None, "unknown_grant"
    if getattr(grant, "revoked_at", None) is not None:
        return None, None, "revoked_grant"
    if getattr(grant, "universe_id", "") != universe_id:
        # The isolation boundary: a packet cannot select a grant from a
        # different universe than the one running this graph.
        return None, None, "grant_not_for_universe"
    if getattr(grant, "connection_id", "") != connection_id:
        return None, None, "grant_connection_mismatch"
    view = ledger.get_connection_view(connection_id)
    if view is None:
        return None, None, "unknown_connection"
    return grant, view, ""


def _open_connection_proxy(
    *,
    db_path: Path,
    universe_id: str,
    grant_id: str,
    connection_id: str,
    owner_user_id: str,
) -> Any:
    """Resolve the exact scoped, credential-blind proxy under universe authority.

    The authenticated principal is the grant's OWN stored owner (trusted grant
    row), gated upstream by the universe match. ``resolve_exact_scoped_proxy``
    spawns the broker worker; the credential is resolved and applied inside it.
    """
    from tinyassets.broker.supervisor import broker_selected
    from tinyassets.storage.outbound_connections import ConnectionLedger

    if broker_selected():
        from tinyassets.broker.ledger_queries import authorized_connection
        from tinyassets.storage.outbound_connections import (
            ProxyRequestError,
            ScopedConnectionProxy,
            _broker_channel,
        )

        grant, resource, _incarnation = authorized_connection(
            Path(db_path).parent, principal=owner_user_id, command_center=universe_id,
            grant_id=grant_id, connection_id=connection_id)
        if resource.connection_type != "http":
            raise ProxyRequestError("credential broker requires an HTTP connection")
        channel = _broker_channel(
            Path(db_path).parent, principal=owner_user_id, command_center=universe_id,
            grant_id=grant_id, connection_id=connection_id)
        if channel is None:
            raise ProxyRequestError("credential broker channel unavailable")
        return ScopedConnectionProxy(
            grant_id=grant.grant_id, provider=resource.provider,
            destination=resource.destination, scopes=resource.scopes, _channel=channel)

    ledger = ConnectionLedger(
        db_path,
        verify_authenticated_principal=lambda: owner_user_id,
    )
    return ledger.resolve_exact_scoped_proxy(
        universe_id=universe_id,
        grant_id=grant_id,
        connection_id=connection_id,
    )


# --------------------------------------------------------------------------- #
# The effector
# --------------------------------------------------------------------------- #
def run_authenticated_external_call_effector(
    *,
    node_id: str,
    output_keys: list[str],
    run_state: dict[str, Any],
    base_path: str | Path | None = None,
    run_id: str = "",
    dry_run: bool | None = None,
    allowed_state_keys: list[str] | set[str] | None = None,
    prior_effects: dict[str, Any] | None = None,
    execution_context=None,
) -> dict[str, Any]:
    """Dispatch one ``authenticated_external_call`` packet. NEVER raises.

    Every refusal or failure is returned as a secret-free evidence dict with an
    ``error_kind``. Success returns the worker's sanitized response plus a small
    amount of non-secret metadata.
    """
    del dry_run  # gate orchestration owns the dry-run decision; kept for compat
    try:
        return _run(
            node_id=node_id,
            output_keys=output_keys,
            run_state=run_state,
            base_path=base_path,
            run_id=run_id,
            allowed_state_keys=allowed_state_keys,
            prior_effects=prior_effects,
            execution_context=execution_context,
        )
    except Exception as exc:  # defensive — never raise from the completion path
        logger.exception(
            "authenticated_external_call effector crashed for node %s", node_id
        )
        return {
            "error": f"effector crashed: {exc}",
            "error_kind": "effector_crashed",
        }


def _run(
    *,
    node_id: str,
    output_keys: list[str],
    run_state: dict[str, Any],
    base_path: str | Path | None,
    run_id: str,
    allowed_state_keys: list[str] | set[str] | None = None,
    prior_effects: dict[str, Any] | None = None,
    execution_context=None,
) -> dict[str, Any]:
    matched_key, packet = _find_packet(output_keys=output_keys, run_state=run_state)
    if packet is None:
        return {
            "error": (
                f"node '{node_id}' declared effects=["
                f"{EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL}] but no output_key held "
                "a parseable authenticated_external_call packet"
            ),
            "error_kind": "no_matching_packet",
        }

    connection_id = _str_field(packet, "connection_id")
    grant_id = _str_field(packet, "grant_id")
    if not connection_id:
        return {"error": "packet.connection_id is required", "error_kind": "invalid_packet"}
    if not grant_id:
        return {"error": "packet.grant_id is required", "error_kind": "invalid_packet"}

    request = packet.get("request")
    if not isinstance(request, dict):
        return {"error": "packet.request must be a mapping", "error_kind": "invalid_packet"}

    # verb = the connection scope. For http connections this IS the HTTP method
    # (the worker uses it as the method). Accept either the top-level verb or
    # request.method; if both are present they must agree.
    verb = _str_field(packet, "verb")
    request_method = _str_field(request, "method")
    if not verb:
        verb = request_method
    if not verb:
        return {"error": "packet.verb is required", "error_kind": "invalid_packet"}
    if request_method and request_method.upper() != verb.upper():
        return {
            "error": "request.method does not match verb",
            "error_kind": "method_mismatch",
            "verb": verb,
            "requested_method": request_method,
        }

    universe_id = _universe_id(base_path)
    db_path = _ledger_db_path(base_path)
    if execution_context is not None:
        if (execution_context.universe != universe_id or not execution_context.owner
                or not execution_context.initiating_agent):
            return {"error_kind": "execution_context_mismatch"}
        if execution_context.research:
            return {"error_kind": "research_is_read_only"}
    if not universe_id or db_path is None:
        # No trusted universe context ⇒ fail closed (never borrow a default).
        return {
            "error": "no command center authority is bound to this run",
            "error_kind": "no_universe_authority",
            "matched_output_key": matched_key,
        }

    from tinyassets.auth.middleware import current_identity_or_none

    identity = current_identity_or_none()
    principal = (execution_context.owner if execution_context is not None
                 else identity.user_id if identity is not None else "")
    grant, view, gate_error = _read_connection_context(
        db_path=db_path,
        grant_id=grant_id,
        connection_id=connection_id,
        universe_id=universe_id,
        principal=principal,
    )
    if gate_error:
        return {
            "error": f"connection authority refused: {gate_error}",
            "error_kind": gate_error,
            "matched_output_key": matched_key,
            "connection_id": connection_id,
            "grant_id": grant_id,
            "universe_id": universe_id,
        }
    if execution_context is not None and (
        grant.owner_user_id != execution_context.owner
        or view.owner_user_id != execution_context.owner
    ):
        return {"error_kind": "connection_owner_mismatch"}

    # Authorization gates — parity with every prior per-channel effector. The
    # connection grant above proves the universe MAY use this connection, but a
    # LIVE external effect additionally requires: (1) the running universe's soul
    # to not DENY the (sink, destination), and (2) an active effector-consent
    # grant for the destination. A connection grant ALONE is not sufficient to
    # fire an effect — fail closed on either gate. The destination is the
    # connection's own configured destination (a stable, server-owned value,
    # never taken from the packet).
    universe_dir = Path(base_path)
    destination = str(getattr(view, "destination", "") or "").strip()
    authority = resolve_soul_effect_authority(
        universe_dir,
        EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        destination,
    )
    if authority == SOUL_AUTHORITY_DENIED:
        return {
            "dry_run": True,
            "reason": "soul_authority_denied",
            "error_kind": "soul_authority_denied",
            "destination": destination,
            "connection_id": connection_id,
            "matched_output_key": matched_key,
        }
    # The owner's Custom Rules (harness D1a) decide before the standing grant is
    # consulted, and can only tighten it: "ask first" and "hand off" stop a call
    # its grant would allow. Every call here counts as a write until connections
    # declare their operation kinds (D1b). A rule store that cannot be read
    # refuses the call; it never falls back to allowing it.
    from tinyassets.bound_requests import dispatch_agent

    approved_agent = dispatch_agent(universe_dir, packet)
    context_agent = (execution_context.initiating_agent if execution_context is not None
                     else _initiating_agent(universe_dir))
    if approved_agent and execution_context is not None and approved_agent != context_agent:
        return {"error_kind": "execution_context_mismatch", "dry_run": True}
    rule_agent = approved_agent or context_agent
    rule_refusal = _rule_refusal(
        universe_dir, connection_id, verb, _request_path(request),
        evidence=_review_evidence(request), agent=rule_agent,
        preapproved=approved_agent is not None,
    )
    if rule_refusal is not None:
        # Pin the actual attempted packet at the point of need. A continuation
        # never has to reconstruct it from the agent's description of a refusal.
        if rule_refusal.get("error_kind") == "rule_ask_first" and not approved_agent:
            from tinyassets.auth.middleware import current_identity_or_none
            from tinyassets.bound_requests import RequestRefused, capture

            # capture binds the ambient turn. A signed ta launch must not
            # create a card attributed to another agent's ambient conversation.
            ambient_agent = _initiating_agent(universe_dir)
            if (current_identity_or_none() is not None and ambient_agent
                    and ambient_agent == rule_agent):
                try:
                    pending = capture(universe_dir, {
                        "executor": EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
                        "arguments": {k: v for k, v in packet.items() if k != "sink"},
                    })
                    rule_refusal = {
                        **rule_refusal, "request_id": pending["request_id"],
                        "hint": "The exact action is waiting in the owner's inline card.",
                    }
                except (RequestRefused, ValueError):
                    # Unsupported transforms/headers need an explicit safe ask.
                    # Nothing is sent, and the existing refusal remains visible.
                    pass
        return {
            **rule_refusal,
            "destination": destination,
            "connection_id": connection_id,
            "matched_output_key": matched_key,
        }
    if not _check_consent(universe_dir, destination):
        return {
            "dry_run": True,
            "reason": "missing_consent",
            "error_kind": "missing_consent",
            "destination": destination,
            "connection_id": connection_id,
            "matched_output_key": matched_key,
            "hint": (
                "A live authenticated_external_call requires an active effector-"
                "consent grant for this connection's destination; grant it through "
                "the internal consent surface before the effect can fire."
            ),
        }

    host, host_error = _resolve_host(request, view)
    if host_error:
        return {
            "error": f"could not resolve request host: {host_error}",
            "error_kind": host_error,
            "matched_output_key": matched_key,
            "connection_id": connection_id,
        }

    url, url_error = _build_url(request, host)
    if url_error:
        return {
            "error": f"could not build request url: {url_error}",
            "error_kind": url_error,
            "matched_output_key": matched_key,
            "connection_id": connection_id,
        }

    capability_error = _capability_url_shape_error(url, view)
    if capability_error:
        # A capability-URL connection addresses the PLACEHOLDER, never the code.
        # The allowlist in the child would refuse a real secret here anyway (its
        # reserved pattern matches only the literal token), but that refusal
        # arrives AFTER this url has been recorded on the returned evidence and
        # persisted with the run. Refusing it here keeps a node-authored secret
        # out of the run record entirely, and tells the author the one thing
        # they need to change.
        return {
            "error": capability_error,
            "error_kind": "capability_url_not_addressed_by_placeholder",
            "matched_output_key": matched_key,
            "connection_id": connection_id,
            "destination": destination,
        }

    wire_request: dict[str, Any] = {"url": url}
    headers = request.get("headers")
    user_agent_error = _declared_user_agent_error(headers)
    if user_agent_error:
        return {
            "error": user_agent_error,
            "error_kind": "user_agent_is_declared_on_the_connection",
            "matched_output_key": matched_key,
            "connection_id": connection_id,
            "destination": destination,
        }
    if headers is not None:
        wire_request["headers"] = headers
    if "body" in request:
        body, transform_error = apply_body_transforms(
            request.get("body"), run_state,
            allowed_state_keys=allowed_state_keys, prior_effects=prior_effects,
        )
        if transform_error:
            # Refused whole: a half-transformed body must never reach the wire.
            return {
                "error": f"body transform refused: {transform_error}",
                "error_kind": "invalid_body_transform",
                "matched_output_key": matched_key,
                "connection_id": connection_id,
            }
        wire_request["body"] = body
    header_name = _str_field(request, "header_name")
    if header_name:
        wire_request["header_name"] = header_name

    proxy = None
    try:
        proxy = _open_connection_proxy(
            db_path=db_path,
            universe_id=universe_id,
            grant_id=grant_id,
            connection_id=connection_id,
            owner_user_id=getattr(grant, "owner_user_id", ""),
        )
        response = proxy.request(verb, wire_request)
    except Exception as exc:
        # Secret-free by construction: the proxy/broker raise only sanitized,
        # credential-free errors across the governed boundary.
        failure = getattr(exc, "failure", None)
        if isinstance(failure, dict):
            # A connection whose authorization could not be made current (an
            # oauth2 refresh failed): the structured record, not a message.
            return {
                "error": str(exc),
                "error_kind": "connection_authorization_failed",
                "failure": dict(failure),
                "matched_output_key": matched_key,
                "connection_id": connection_id,
                "grant_id": grant_id,
                "verb": verb,
                "url": url,
            }
        return {
            "error": f"outbound request failed: {type(exc).__name__}",
            "error_kind": "outbound_request_failed",
            "detail": str(exc),
            "matched_output_key": matched_key,
            "connection_id": connection_id,
            "grant_id": grant_id,
            "verb": verb,
            "url": url,
        }
    finally:
        if proxy is not None:
            try:
                proxy.close()
            except Exception:  # pragma: no cover — best-effort teardown
                logger.debug("proxy close failed for node %s", node_id, exc_info=True)

    # Usage budgets (change `run-usage-budgets`): what this call moved, so
    # the per-run and per-hour byte budgets charge the real size.
    try:
        _sent_body = wire_request.get("body")
        request_bytes = _byte_size(_sent_body) if _sent_body is not None else 0
    except Exception:  # noqa: BLE001 - a non-encodable body was refused earlier
        request_bytes = 0
    try:
        response_bytes = _byte_size(response.get("body")) if isinstance(response, dict) else 0
    except Exception:  # noqa: BLE001
        response_bytes = 0
    return {
        "delivered": True,
        "response": response,
        "request_bytes": int(request_bytes),
        "response_bytes": int(response_bytes),
        "matched_output_key": matched_key,
        "connection_id": connection_id,
        # Which CONNECTION this call used, by the name its owner gave it. A
        # delivered 401 means the stored key for this destination is finished,
        # and the card that replaces it needs the destination -- so the row the
        # agent reads has to name it rather than leave it to be inferred from
        # the url (which is the API host, not the connection's label).
        "destination": destination,
        "grant_id": grant_id,
        "verb": verb,
        "url": url,
    }


__all__ = [
    "EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL",
    "apply_body_transforms",
    "bounded_evidence",
    "run_authenticated_external_call_effector",
]
