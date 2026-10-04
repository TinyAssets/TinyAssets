"""Keep every held sensitive value out of a streamed response (I14 decision 5).

The request/close broker refuses a response if any held value appears anywhere
in it. A stream cannot wait for the end, so it forwards bytes as they arrive
and holds back only what a later byte could still turn into an occurrence.

Two matchers run over the same bytes, as the request/close path effectively
does today (it decodes the body as UTF-8 with replacement before scanning):

* raw: each value's UTF-8 encoding, as a byte substring;
* decoded: each value as a substring of the incremental UTF-8-with-replacement
  decoding. A value containing U+FFFD can match invalid bytes this way without
  matching the raw encoding.

Each matcher scans the new bytes together with the tail of what came before
(one byte or character short of the longest value), so an occurrence split
across reads is found. The hold-back is sized in raw bytes to cover both: the
longest encoded value, minus one, plus the up-to-3-byte incomplete sequence the
decoder may be holding. No forwarded byte can belong to an occurrence a later
byte completes, because such an occurrence starts inside the held bytes.

The set of values never leaves this object; callers forward what it returns.
"""

from __future__ import annotations

import codecs
from collections.abc import Iterable


class SensitiveValueInResponse(Exception):
    """A held value appeared in the response; the stream must end, unforwarded."""


class StreamScanner:
    """One response stream's scanner. ``feed`` returns the bytes safe to forward."""

    def __init__(self, values: Iterable[str]) -> None:
        texts = tuple(dict.fromkeys(v for v in values if isinstance(v, str) and v))
        self._texts = texts
        self._raws = tuple(t.encode("utf-8") for t in texts)
        longest_raw = max((len(r) for r in self._raws), default=0)
        longest_text = max((len(t) for t in texts), default=0)
        self._raw_tail_keep = max(longest_raw - 1, 0)
        self._text_tail_keep = max(longest_text - 1, 0)
        #: Bytes withheld from the caller (the hold-back).
        self.hold = (longest_raw - 1 + 3) if texts else 0
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._raw_tail = b""
        self._text_tail = ""
        self._pending = bytearray()
        self._finished = False

    def _check(self, raw: bytes, text: str) -> None:
        raw_window = self._raw_tail + raw
        text_window = self._text_tail + text
        if any(r in raw_window for r in self._raws) or any(
            t in text_window for t in self._texts
        ):
            raise SensitiveValueInResponse("a held value appeared in the response")
        self._raw_tail = raw_window[-self._raw_tail_keep:] if self._raw_tail_keep else b""
        self._text_tail = text_window[-self._text_tail_keep:] if self._text_tail_keep else ""

    def feed(self, chunk: bytes) -> bytes:
        """Scan ``chunk``; return the bytes that can no longer be part of a value."""
        if self._finished:
            raise RuntimeError("scanner already finished")
        chunk = bytes(chunk)
        if not self._texts:
            return chunk
        self._check(chunk, self._decoder.decode(chunk))
        self._pending += chunk
        if len(self._pending) <= self.hold:
            return b""
        release = bytes(self._pending[:len(self._pending) - self.hold])
        del self._pending[:len(release)]
        return release

    def finish(self) -> bytes:
        """At a clean end: finalize the decoder, scan, and release the rest."""
        if self._finished:
            return b""
        self._finished = True
        if not self._texts:
            return b""
        self._check(b"", self._decoder.decode(b"", final=True))
        rest = bytes(self._pending)
        self._pending.clear()
        return rest


def contains_sensitive(value: object, values: Iterable[str]) -> bool:
    """Whole-value check for headers, a reason phrase or any small document.

    The same rule as the request/close path's matcher: a substring of any
    string or bytes anywhere inside, keys included.
    """
    held = tuple(v for v in values if isinstance(v, str) and v)

    def walk(item: object) -> bool:
        if isinstance(item, str):
            return any(v in item for v in held)
        if isinstance(item, bytes):
            return any(v.encode("utf-8") in item for v in held) or walk(
                item.decode("utf-8", errors="replace"))
        if isinstance(item, dict):
            return any(walk(k) or walk(v) for k, v in item.items())
        if isinstance(item, (list, tuple, set, frozenset)):
            return any(walk(v) for v in item)
        return False

    return bool(held) and walk(value)
