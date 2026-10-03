"""A truthful sentence must not be refused as a credential.

Live 2026-09-30, free account, ``write_graph target=pending_request
operation=withdraw``: a universe was refused TWICE for explaining, in plain
words, that there was nothing to paste --

    "that reason looks like it contains a credential; it is stored in the clear,
    so say it in words instead"

Neither refused reason held a credential. The screen was one flat pattern,
``[A-Za-z0-9_\\-]{16,}``, and because ``-`` was inside its character class the
hyphenated compound ``self-authenticating`` was a "16+ character unbroken run".

The two exact strings the founder reported are pinned below and must pass. So is
the other half of the contract: every real secret shape named in the report
(``sk-…``, long high-entropy tokens, URLs with secret path/query segments) must
still be refused, and the assertion is on the shape being FOUND, never on "the
answer changed" -- an assertion that a refusal happened passes on a detector
that refuses everything, which is the bug being fixed.
"""

from __future__ import annotations

import random
import re
import string

import pytest

from tinyassets.credential_shape import credential_shape, looks_like_credential

# --------------------------------------------------------------------------- #
# The exact strings from the report. These are the regression.
# --------------------------------------------------------------------------- #

WITHDRAW_REASON_ONE = (
    "No token exists for this destination: your friend's link is a "
    "self-authenticating webhook URL (the token is embedded in the path), so "
    "there is nothing to paste. Raising a corrected ask that only…"
)
WITHDRAW_REASON_TWO = (
    "That link is a self-authenticating webhook URL with the secret embedded in "
    "the path, so there is no separate token to paste. Raising a corrected ask "
    "that only approves the endpoint."
)


@pytest.mark.parametrize("reason", [WITHDRAW_REASON_ONE, WITHDRAW_REASON_TWO])
def test_the_two_refused_withdraw_reasons_pass(reason):
    assert credential_shape(reason) is None, (
        "the exact reason a universe was refused on 2026-09-30 is still refused"
    )


def test_the_compound_word_that_caused_it_is_not_a_credential():
    """``self-authenticating`` is 19 characters of two English words."""
    assert credential_shape("self-authenticating") is None
    assert re.compile(r"[A-Za-z0-9_\-]{16,}").search("self-authenticating"), (
        "the old pattern no longer matches, so this test no longer pins anything"
    )


# --------------------------------------------------------------------------- #
# Prose the rail actually carries.
# --------------------------------------------------------------------------- #

PROSE = [
    "do we still need this? you said people can use it through the commons now",
    "Access Token Secret",
    "Consumer key and consumer secret from the developer portal",
    "password authentication succeeded for the read-only mirror",
    "This is an internationalization/localization concern, nothing more.",
    "self-documenting, context-independent, forward-compatible, non-self-referential",
    "semi-autonomous, well-intentioned, over-engineered",
    "non-transferable machine-to-machine client-credentials flow",
    "I withdrew it because the user's pre-authorization already covers it.",
    "It is a pre-authenticated, self-describing, non-revocable webhook.",
    "antidisestablishmentarianism",
    "electroencephalographically",
    "pseudopseudohypoparathyroidism",
    "understandability, interchangeability, straightforwardness, notwithstanding",
    # Paths, identifiers and addresses an agent names when explaining itself.
    "The key is only used by ReportEngine/agent.py, which no longer runs.",
    "Superseded by docs/design-notes/2026-04-25-session-additions.md",
    "See index.html, styles.css, app.js and schema.sql in the repository root.",
    "Reply to jonathan.m.farnsworth@gmail.com if the branch gate_investigation_v1 reappears.",
    "Look at path/to/file.py:LINE and tests/test_path.py::test_case",
    # Links to the pages a credential actually comes from.
    "Get it from https://tinyassets.io/account",
    "https://api.example.com/v1/chat/completions",
    "https://developer.twitter.com/en/portal/projects-and-apps",
    "https://console.cloud.google.com/apis/credentials",
    "https://github.com/settings/tokens/new?scopes=repo&description=TinyAssets",
    "https://docs.github.com/en/authentication/keeping-your-account-and-data-secure"
    "/managing-your-personal-access-tokens",
    "https://www.postgresql.org/docs/current/runtime-config-connection.html",
    "https://en.wikipedia.org/wiki/Public-key_cryptography",
]


@pytest.mark.parametrize("text", PROSE)
def test_ordinary_prose_and_links_pass(text):
    assert credential_shape(text) is None, f"prose refused as a credential: {text!r}"


# --------------------------------------------------------------------------- #
# Real secret shapes. Each names the shape it must be caught BY, so a change
# that catches it for an accidental reason fails here.
# --------------------------------------------------------------------------- #


def _shape(*parts: str) -> str:
    """One credential-shaped string, joined from its pieces at run time.

    Every vector below matches a provider's published pattern by design, which
    is the whole point -- and GitHub push protection refuses a commit carrying
    one as a literal. It rejected this file's first draft over three of them, an
    independent confirmation that the shapes are the real thing. Joining here
    keeps the vector exact without committing a scannable literal, and leaving
    the prefix as its own argument makes the part that identifies the provider
    the part you read first.
    """
    return "".join(parts)


SECRETS = [
    (_shape("sk-ant-", "api03-abcdefghijklmnopqrstuvwxyz0123456789"), "opaque_high_entropy"),
    ("the key is " + _shape("sk-", "proj-9dKq3fZmRvT8yXaLpQwE2nBcHjUiOs"),
     "opaque_high_entropy"),
    (_shape("ghp", "_16C7e42F292c6912E7710c838347Ae178B4a"), "opaque_high_entropy"),
    (_shape("github_pat", "_11ABCDEFG0aBcDeFgHiJkL_mNoPqRsTuVwXyZ0123456789"),
     "opaque_high_entropy"),
    (_shape("xoxb", "-2345678901-2345678901234-AbCdEfGhIjKlMnOpQrStUvWx"),
     "opaque_high_entropy"),
    (_shape("sk_live", "_51H8ZqKLmNoPqRsTuVwXyZaBcDeFgHi"), "opaque_high_entropy"),
    (_shape("glpat", "-AbCdEfGhIjKlMnOpQrSt"), "opaque_high_entropy"),
    (_shape("hf", "_QwErTyUiOpAsDfGhJkLzXcVbNm123456"), "opaque_high_entropy"),
    (_shape("whsec", "_8H3jKl9MnOpQrStUvWxYz012345"), "opaque_high_entropy"),
    (_shape("AKIA", "IOSFODNN7EXAMPLE"), "opaque_high_entropy"),
    (_shape("AIza", "SyD-abcdefghijklmnopqrstuvwxyz012345"), "opaque_high_entropy"),
    (_shape("ya29.", "a0AfH6SMBx9Qp2LmVnRtZwKdHs4Fb7Yx"), "opaque_high_entropy"),
    (_shape("eyJ", "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            ".eyJzdWIiOiIxMjM0NTY3ODkwIn0",
            ".dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"), "jwt"),
    (_shape("-----BEGIN RSA PRIVATE KEY-----\n", "MIIEow==\n",
            "-----END RSA PRIVATE KEY-----"), "private_key_block"),
    ("Authorization: " + _shape("Bearer ", "abcdefghijklmnopqrstuvwxyz012345"),
     "auth_header"),
    ("d41d8cd98f00b204e9800998ecf8427e", "hex_key_material"),
    ("token: c3VwZXJzZWNyZXRwYXNzd29yZDEyMzQ1", "base64_key_material"),
    ("the value is 7Hq2Lp9XvB4nZm8KdRtW3Ysa", "base64_key_material"),
    ("xJ8kQ2mNpR5vT7wYzA4bC6dE9fG1hI3jK", "base64_key_material"),
    (_shape("wJalrXUtnFEMI", "/K7MDENG/bPxRfiCYEXAMPLEKEY"), "base64_key_material"),
    ("AQVN0rPuQG_Xk9mLp2Zt8WsYbCdEfGhIjKlM", "opaque_high_entropy"),
    ("0x4fA2b91Cd83e7B6a15D0c2E94f8A73bC6d05E1f2", "base64_key_material"),
    # URLs: the founder named path and query segments explicitly.
    ("https://tinyassets.io/mcp/hooks/girbY_SuCx9Qp2LmVnRtZw", "url_path_secret"),
    (_shape("https://hooks.", "slack.com/services",
            "/T0A1B2C3D4/B5E6F7G8H9/zQ8mNvRtYw3LpXk2JdHs9Fb4"), "url_path_secret"),
    ("https://api.example.com/v1/data?api_key=abcd1234efgh", "url_secret_parameter"),
    ("https://example.com/reset?token=Zm9vYmFyYmF6cXV1eA", "url_secret_parameter"),
    ("https://user:s3cr3tpassw0rd@api.example.com/v1", "url_userinfo"),
    ("https://example.com/callback#access_token=" + _shape("ya29.", "a0ARrdaM9xQ"),
     "url_fragment_secret"),
]


@pytest.mark.parametrize("text,shape", SECRETS)
def test_real_secret_shapes_are_still_refused(text, shape):
    assert credential_shape(text) == shape, (
        f"{text[:40]!r} was expected to be caught as {shape}"
    )


def test_looks_like_credential_agrees_with_credential_shape():
    assert looks_like_credential(_shape("ghp", "_16C7e42F292c6912E7710c838347Ae178B4a")) is True
    assert looks_like_credential(WITHDRAW_REASON_ONE) is False
    assert looks_like_credential("") is False
    assert looks_like_credential(None) is False


def test_a_secret_hidden_inside_a_sentence_is_found():
    """The screen is per token, so position in the sentence is irrelevant."""
    assert credential_shape(
        "sure, here it is: " + _shape("ghp", "_16C7e42F292c6912E7710c838347Ae178B4a")
        + " thanks"
    ) == "opaque_high_entropy"


def test_a_bare_prefix_with_no_body_is_just_a_word():
    """``sk-`` in a sentence about key shapes is not a key."""
    assert credential_shape("its prefix is sk- and the rest is entropy") is None


# --------------------------------------------------------------------------- #
# The measured claim in the module docstring, asserted rather than asserted-to.
# --------------------------------------------------------------------------- #

ALPHABETS = {
    "base64url": string.ascii_letters + string.digits + "-_",
    "base64": string.ascii_letters + string.digits + "+/",
    "alphanumeric": string.ascii_letters + string.digits,
    "hex": "0123456789abcdef",
    "base32": "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
    "base58": "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz",
    "mixed letters": string.ascii_letters,
}


@pytest.mark.parametrize("name,alphabet", sorted(ALPHABETS.items()))
@pytest.mark.parametrize("length", [24, 32, 40])
def test_no_random_token_carrying_a_non_letter_escapes(name, alphabet, length):
    """Every alphabet a provider issues keys in, at key lengths.

    The property asserted is the one that can be asserted: a random token with
    ANY non-letter in it -- a digit, ``-``, ``_``, ``+``, ``/`` -- never escapes.
    That covers every shape a provider actually mints. Seeded, so a failure is
    reproducible rather than a flake.

    Letters-only draws are excluded here and measured in the test below, because
    a run of nothing but letters is the one case no structural test separates
    from a long word -- see this module's counterpart in
    :mod:`tinyassets.credential_shape`, which documents the same limit.
    """
    rng = random.Random(f"{name}:{length}")
    tokens = ["".join(rng.choices(alphabet, k=length)) for _ in range(400)]
    escaped = [t for t in tokens if t.isalpha() is False and credential_shape(t) is None]
    assert escaped == [], f"{name} at {length} chars escaped: {escaped[:3]}"


# --------------------------------------------------------------------------- #
# Cross-family review round, 2026-09-30 (`codex exec -m gpt-6-astra`, read-only,
# asked only whether a real secret shape slips through). Six false negatives,
# every one of which the pattern being replaced DID catch -- which is the bar:
# nothing the old screen caught may now pass.
# --------------------------------------------------------------------------- #

REFUTED = [
    # A TOTP base32 seed. Google's own published example key, and 16 characters
    # -- under the 20-char bar this module first shipped with.
    ("JBSWY3DPEHPK3PXP", "opaque_high_entropy"),
    ('{"key":"JBSWY3DPEHPK3PXP"}', "opaque_high_entropy"),
    (_shape("otpauth://totp/Example:alice?secret=", "JBSWY3DPEHPK3PXP",
            "&issuer=Example"), "url_secret_parameter"),
    # A 64-bit device key in hex. 16 characters, where _HEX_RE wanted an md5's 32.
    ("9a7c2e4f8b1d6a03", "hex_key_material"),
    # A 20-digit numeric secret: 66 bits, and pure digits were unconditionally
    # "a written number".
    ("72948361502874619350", "opaque_high_entropy"),
    # `;` was a TOKEN boundary, so one 25-character secret arrived as two short
    # ones, each under the length bar...
    ("7Hq2Lp9XvB4nZm8K;dRtW3Ysa", "opaque_high_entropy"),
    # ...and a URL was cut off before its own query string.
    ("https://example.com/reset?token=1234;7Hq2Lp9XvB4nZm8K",
     "url_secret_parameter"),
    # A bare `?<token>` has no `=`, so the whole secret is a parameter NAME with
    # an empty value, and only values were judged.
    ("https://example.com/reset?7Hq2Lp9XvB4nZm8KdRtW3Ysa", "url_query_secret"),
    # 24 characters of base64url whose only letters are "Qx": too few letters to
    # measure, so the word test was SKIPPED and the run passed on the strength of
    # a test that never ran. A configured webhook id is exactly this shape.
    ("Qx2345_98762345_98374612", "opaque_high_entropy"),
    ("https://ha.example/api/webhook/Qx2345_98762345_98374612", "url_path_secret"),
    # 40 characters of uppercase gibberish with a slash -- an AWS-secret shape.
    # It scored 0.50 on bigram plausibility, against a bar then set at 0.45.
    ("FRMEDBKESYTOIHCSVWO/WFIRLPSEPKPBSOBEBGHF", "opaque_high_entropy"),
]


@pytest.mark.parametrize("text,shape", REFUTED)
def test_nothing_the_old_pattern_caught_slips_through(text, shape):
    assert credential_shape(text) == shape
    assert re.compile(r"[A-Za-z0-9_\-]{16,}").search(text), (
        "this vector no longer demonstrates a regression against the old pattern"
    )


def test_truncation_cannot_flip_the_verdict_permissively():
    """Callers slice to 2000 chars BEFORE screening, so the screened value IS
    the stored value -- there is no window where a secret survives a clean
    verdict. Pinned because the order is what makes it true."""
    from tinyassets.api import pending_requests

    secret = _shape("Qx2345", "_98762345_98374612")
    payload = "a " * 988 + secret + "X"
    kept = payload.strip()[:pending_requests._MAX_ANSWER_CHARS]
    assert secret in kept, "this payload no longer retains the secret after slicing"
    assert credential_shape(kept) is not None, (
        "the truncated value that would be STORED screens clean"
    )


def test_the_letters_only_gap_is_bounded_and_stays_bounded():
    """The documented limit, pinned so a regression widening it is visible.

    A secret drawn only from letters is prose-shaped by construction. The old
    pattern "caught" this class only by refusing every hyphenated compound word,
    which is the bug. No provider issues keys in a letters-only alphabet, so the
    residual is bounded rather than closed -- but it is bounded, and a change
    that pushes it past the measured ceiling has broken something.
    """
    rng = random.Random("letters-only")
    for alphabet in (string.ascii_lowercase, string.ascii_uppercase):
        for length, ceiling in ((16, 0.04), (20, 0.03), (32, 0.01)):
            tokens = ["".join(rng.choices(alphabet, k=length))
                      for _ in range(600)]
            rate = sum(1 for t in tokens if credential_shape(t) is None) / len(tokens)
            assert rate <= ceiling, (
                f"single-case letter escapes at {length} chars rose to {rate:.1%}, "
                f"over the {ceiling:.0%} measured on 2026-09-30"
            )


# ---------------------------------------------------------------------------
# ISO-8601 date-times are writing, not key material (2026-10-01)
# ---------------------------------------------------------------------------
# Live: every timestamp in a published workflow row, and every dated line of an
# agent's board, read as "opaque_high_entropy" -- the ``T`` glues ``01`` to
# ``12`` into a part that is neither a number nor a word. A stamp is now taken
# out of the run before it is judged; what sits beside it is judged on its own.

TIMESTAMPS = [
    "2026-10-01T12:00:00Z",
    "2026-10-01T12:00:00+00:00",
    "2026-10-02T01:35:29.863058+00:00",
    "2026-10-01T12:00Z",
    "2026-10-01 12:00:00",
    "2026-10-01T23:59:60Z",
    "20261001T120000Z",
    "20261001T1200+0530",
    "at 2026-10-01T12:00:00Z the scout found three bakeries",
    "created_at: 2026-09-30T08:15:00.123+02:00, updated_at: 2026-10-01T09:00:00Z",
    "run-2026-10-01T12-00-00.log",
    "(2026-10-01T12:00:00Z)",
    "https://example.com/runs/2026-10-01T12:00:00Z/output",
    "2026-10-01t12:00:00z",
    "2026-10-01T12:00:00z",
    "2024-02-29T12:00:00Z",
]


@pytest.mark.parametrize("text", TIMESTAMPS)
def test_iso_timestamps_are_not_credentials(text):
    assert credential_shape(text) is None, credential_shape(text)


STAMP_SHAPED_SECRETS = [
    # A key glued to a stamp, with or without a separator, is still a key.
    ("AbC9xQ7LmZ2pR8tW2026-10-01T12:00:00Z", "opaque_high_entropy"),
    ("AbC9xQ7LmZ2pR8tW-2026-10-01T12:00:00Z", "opaque_high_entropy"),
    ("2026-10-01T12:00:00Z-AbC9xQ7LmZ2pR8tW", "opaque_high_entropy"),
    # A glued stamp is not a stamp: taking it out would leave a short key under
    # the length bar, so the whole run is judged.
    ("Xq7Lm9Rt2026-10-01T12:00:00Z", "opaque_high_entropy"),
    ("2026-10-01T12:00:00ZXq7Lm9Rt", "opaque_high_entropy"),
    ("2026-10-01T12:00:00.123ZXq7Lm9Rt", "opaque_high_entropy"),
    ("2026-10-01T12:00:00Z_" + _shape("sk_live", "_51H8ZqKLmNoPqRsTuVwXyZaBcDeFgHi"),
     "opaque_high_entropy"),
    # Not a stamp at all: impossible calendar or clock fields.
    ("2026-13-01T12:00:00Z", "opaque_high_entropy"),
    ("2026-10-32T12:00:00Z", "opaque_high_entropy"),
    ("2026-10-01T24:00:00Z", "opaque_high_entropy"),
    ("2026-10-01T12:61:00Z", "opaque_high_entropy"),
    # A stamp in a URL never hides the secret parameter beside it.
    ("https://example.com/r/2026-10-01T12:00:00Z?token=Zm9vYmFyYmF6cXV1eA",
     "url_secret_parameter"),
    # A stamp cannot carry a short key under the length bar with it.
    ("20261001T120000Z-Xq7Lm9RtAbC9", "opaque_high_entropy"),
    ("https://example.com/20261001T120000Z-Xq7Lm9Rt", "url_path_secret"),
    # Only ASCII digits make a stamp, and a glued non-ASCII digit is glue.
    ("2026-10-01T12:00:00.123456789٠Z", "opaque_high_entropy"),
    ("٢٠٢٦-10-01T12:00:00Z", "opaque_high_entropy"),
    # The calendar decides, and offsets are bounded.
    ("2026-02-31T12:00:00Z", "opaque_high_entropy"),
    ("2026-02-29T12:00:00Z", "opaque_high_entropy"),
    ("2026-10-01T12:00:00+00:99", "opaque_high_entropy"),
    ("2026-10-01T12:00:00+15:00", "opaque_high_entropy"),
    # A UUID stays key material: some services issue API keys as bare UUIDs.
    ("550e8400-e29b-41d4-a716-446655440000", "opaque_high_entropy"),
]


@pytest.mark.parametrize("text,shape", STAMP_SHAPED_SECRETS)
def test_a_stamp_never_launders_key_material(text, shape):
    assert credential_shape(text) == shape
