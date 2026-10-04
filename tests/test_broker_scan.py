"""The broker's incremental scan: nothing of a held value is ever forwarded."""

from __future__ import annotations

import random

import pytest

from tinyassets.broker.scan import SensitiveValueInResponse, StreamScanner, contains_sensitive


def _stream(values, chunks):
    scanner = StreamScanner(values)
    forwarded = bytearray()
    for chunk in chunks:
        forwarded += scanner.feed(chunk)
    forwarded += scanner.finish()
    return bytes(forwarded)


def _splits(data: bytes, rng: random.Random):
    position, out = 0, []
    while position < len(data):
        step = rng.randint(1, 7)
        out.append(data[position:position + step])
        position += step
    return out


@pytest.mark.parametrize("seed", range(40))
def test_a_value_split_anywhere_is_caught_and_nothing_of_it_was_forwarded(seed):
    rng = random.Random(seed)
    secret = "sk-" + "".join(rng.choice("abcdefXYZ0123") for _ in range(rng.randint(6, 30)))
    other = "tok" * rng.randint(1, 4)
    body = (b"data: hello\n" * rng.randint(0, 20) + secret.encode()
            + b" trailing" * rng.randint(0, 5))
    scanner = StreamScanner([secret, other])
    forwarded = bytearray()
    with pytest.raises(SensitiveValueInResponse):
        for chunk in _splits(body, rng):
            forwarded += scanner.feed(chunk)
        forwarded += scanner.finish()
    # No byte belonging to the occurrence reached the caller.
    start = body.index(secret.encode())
    assert len(forwarded) <= start


@pytest.mark.parametrize("seed", range(20))
def test_a_clean_stream_is_forwarded_whole_and_in_order(seed):
    rng = random.Random(seed)
    body = rng.randbytes(rng.randint(0, 3000))
    secret = "never-in-here-☃"
    assert secret.encode() not in body, "the seeded clean fixture must contain no secret"
    assert _stream([secret], _splits(body, rng)) == body


def test_a_clean_response_ending_in_a_prefix_is_delivered_whole():
    assert _stream(["abcdef"], [b"xx", b"abc"]) == b"xxabc"


def test_a_value_matched_only_after_decoding_is_caught():
    # A username containing U+FFFD matches invalid bytes once decoded with
    # replacement, though its own UTF-8 encoding is not in the raw bytes.
    value = "a�b"
    with pytest.raises(SensitiveValueInResponse):
        _stream([value], [b"xx a", b"\xff", b"b yy"])


def test_overlapping_and_nested_values_of_different_lengths():
    values = ["abc", "bcdefgh", "h"]
    with pytest.raises(SensitiveValueInResponse):
        _stream(values, [b"zzzh"])
    with pytest.raises(SensitiveValueInResponse):
        _stream(values, [b"zzbcdef", b"gh"])


def test_no_values_means_no_hold_back():
    scanner = StreamScanner([])
    assert scanner.hold == 0
    assert scanner.feed(b"abc") == b"abc"


def test_the_hold_back_covers_the_longest_value_and_a_pending_sequence():
    scanner = StreamScanner(["short", "a-much-longer-value"])
    assert scanner.hold == len("a-much-longer-value") - 1 + 3


@pytest.mark.parametrize("document", [
    {"x-echo": "Bearer sk-live-1"},
    {"sk-live-1": "as a header name"},
    "reason phrase sk-live-1",
    b"bytes sk-live-1",
])
def test_whole_documents_are_checked_keys_and_values(document):
    assert contains_sensitive(document, ["sk-live-1"])
    assert not contains_sensitive(document, ["other"])
