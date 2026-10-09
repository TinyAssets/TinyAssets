"""A credential ask has to be answerable without the owner guessing.

Founder, 2026-08-31:

    "no more the user having to guess what they need to put where. each single
    indevidual credential will have its own indevidually labled request that
    uses what the agent found online as what that sight uses for calling its
    credentials that it needs ... each should be labled in the user facing way
    with directions for the user facing path to get them with clickable links"

Before this a field carried a `name`, a `label` of at most 120 characters, and a
type. There was nowhere to put "Developer Portal -> your app -> Keys and tokens"
and nowhere at all to put the link — and the cap was six fields, fewer than an
OAuth 1.0a deposit needs, which forced the agent back to one box labelled
"paste the key".

The AGENT composes these from what it knows about the service. There is no table
of services in the platform and these tests assume none: a site nobody has heard
of gets the same quality of ask as a famous one.
"""
from __future__ import annotations

import pytest

from tinyassets.api.pending_requests import _MAX_FIELDS, _validated_fields

_CONNECT = {"type": "connect_http"}


def _field(**over):
    base = {"name": "api_key", "label": "API Key", "type": "secret"}
    base.update(over)
    return base


def test_a_field_carries_its_directions_and_its_link() -> None:
    [field] = _validated_fields(
        [
            _field(
                help="Developer Portal -> your app -> Keys and tokens -> API Key",
                url="https://developer.x.com/en/portal/dashboard",
            )
        ],
        _CONNECT,
    )
    assert field["label"] == "API Key"
    assert "Keys and tokens" in field["help"]
    assert field["url"] == "https://developer.x.com/en/portal/dashboard"


def test_directions_and_link_are_optional() -> None:
    """A simple service should not be forced to invent ceremony."""
    [field] = _validated_fields([_field()], _CONNECT)
    assert "help" not in field
    assert "url" not in field


def test_an_oauth1a_deposit_fits() -> None:
    """The case that motivated the cap change: four values, each its own box.

    The deposit reads these four names exactly (`_OAUTH1A_FIELDS`), so the ask
    must use them — labelled however the service words them. An earlier draft of
    this test asked for five, including a bearer token, which is not part of the
    OAuth 1.0a bundle at all.
    """
    names = ["api_key", "api_secret", "access_token", "access_token_secret"]
    fields = _validated_fields(
        [_field(name=n, label=n.replace("_", " ").title()) for n in names],
        {"type": "connect_http", "auth_scheme": "oauth1a"},
    )
    assert [f["name"] for f in fields] == names
    assert _MAX_FIELDS >= 16, "leave room for a service nobody has enumerated"


def test_the_fixed_names_are_enforced_when_the_scheme_reads_them() -> None:
    """Caught at ASK time, not at deposit.

    The deposit reads fixed names for these schemes. Finding out afterwards
    means the owner has already filled the form and is told "oauth1a secret is
    missing: api_secret" about a box they cannot see — and that mismatch was
    live: the served docs taught `api_key_secret` while the deposit required
    `api_secret` (Codex round 2, Q1).
    """
    wrong = ["api_key", "api_key_secret", "access_token", "access_token_secret"]
    with pytest.raises(ValueError) as caught:
        _validated_fields(
            [_field(name=n) for n in wrong],
            {"type": "connect_http", "auth_scheme": "oauth1a"},
        )
    assert "fixed field names" in str(caught.value)
    assert "api_secret" in str(caught.value)


def test_the_documented_oauth1a_names_are_the_ones_the_deposit_reads() -> None:
    """Doc/runtime parity, because this exact pair disagreed in production."""
    from tinyassets.api.http_connection import _OAUTH1A_FIELDS
    from tinyassets.api.pending_requests import _MULTI_VALUE_FIELD_NAMES

    assert _MULTI_VALUE_FIELD_NAMES["oauth1a"] == _OAUTH1A_FIELDS

    import tinyassets.engine_mcp_server as engine

    # REACHABLE, not resident (2026-09-26): field naming lives in the
    # `connections` handbook chapter, which the resident index names as covering
    # "naming each field the way the site names it". The wrong-name assertion
    # below now spans the whole reachable text, so it got stronger.
    doc = engine.served_tool_guidance("write_graph")
    for name in _OAUTH1A_FIELDS:
        assert name in doc, f"the docs never name {name!r}"
    assert "api_key_secret" not in doc, "the wrong name is back in the docs"


@pytest.mark.parametrize(
    "url",
    [
        "http://insecure.example",             # not https
        "javascript:alert(1)",                 # not a link at all
        "data:text/html;base64,PHN2Zz4=",
        "https://user:password@evil.example/x",  # credentials in the URL
        "https://host/ with a space",
    ],
)
def test_an_unsafe_link_is_refused_not_sanitised(url: str) -> None:
    """This is rendered to the owner as something to click WHILE they are being
    asked for a secret. A caller composing a bad link should be told, not
    quietly corrected — and the agent composing it may be running code the owner
    pulled from the commons.
    """
    with pytest.raises(ValueError) as caught:
        _validated_fields([_field(url=url)], _CONNECT)
    assert "https" in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://developer.x.com/en/portal/dashboard",
        "https://github.com/settings/tokens",
        "https://console.cloud.google.com/apis/credentials?project=x",
        "https://git.internal.example/profile/personal_access_tokens",
    ],
)
def test_ordinary_links_are_accepted_including_ones_nobody_enumerated(url: str) -> None:
    [field] = _validated_fields([_field(url=url)], _CONNECT)
    assert field["url"] == url


def test_long_directions_are_bounded_not_refused() -> None:
    """Truncating help is kind; refusing the whole ask over prose is not."""
    [field] = _validated_fields([_field(help="x" * 5000)], _CONNECT)
    assert len(field["help"]) <= 400


def test_too_many_fields_is_still_refused() -> None:
    many = [_field(name=f"f{i}") for i in range(_MAX_FIELDS + 1)]
    with pytest.raises(ValueError) as caught:
        _validated_fields(many, _CONNECT)
    assert "at most" in str(caught.value)


def test_a_secret_field_is_still_only_for_a_credential_ask() -> None:
    """The boundary that stops "compose requests however you like" becoming a
    way to ask for a password and store it as a plain answer. Unchanged."""
    with pytest.raises(ValueError) as caught:
        _validated_fields([_field()], {"type": "grant_workspace_consent"})
    assert "secret field is only allowed" in str(caught.value)


def test_a_credential_ask_may_not_carry_a_plain_text_field() -> None:
    """A non-secret answer is recorded and relayed back into chat, so a field
    typed `text` on a credential ask persists that value in the clear.

    Harmless when a credential ask was one box; a live hazard once asks carry
    four or five and one could be mislabelled (Codex, Q1). If a flow genuinely
    needs a non-secret answer, that is a different ask.
    """
    with pytest.raises(ValueError) as caught:
        _validated_fields(
            [_field(), {"name": "note", "label": "Note", "type": "text"}], _CONNECT
        )
    assert "must be type 'secret'" in str(caught.value)
    assert "'note'" in str(caught.value)


def test_several_boxes_need_a_scheme_that_can_encode_them() -> None:
    """`basic` is one string and `bearer`/`header` are one token, so assembling
    several into JSON would hand the service a credential that cannot
    authenticate -- failing at the far end with nothing to point at."""
    two = [_field(name="username"), _field(name="password")]
    with pytest.raises(ValueError) as caught:
        _validated_fields(two, {"type": "connect_http", "auth_scheme": "bearer"})
    assert "single value" in str(caught.value)

    # `basic` DOES encode two, and refusing it stranded ordinary
    # username/password services outright (Codex round 2, Q4).
    ok = _validated_fields(two, {"type": "connect_http", "auth_scheme": "basic"})
    assert [f["name"] for f in ok] == ["username", "password"]


def test_an_overlong_link_is_refused() -> None:
    """The bound existed and was never applied, so a 10,000-character hostname
    matched the pattern and was stored and rendered (Codex, Q6)."""
    with pytest.raises(ValueError):
        _validated_fields([_field(url="https://" + "a" * 10_000 + ".example")], _CONNECT)


@pytest.mark.parametrize("length", [430, 8192])
def test_long_request_links_are_preserved(length) -> None:
    prefix = "https://x.com/intent/post?text="
    url = prefix + "a" * (length - len(prefix))
    assert _validated_fields([_field(url=url)], _CONNECT)[0]["url"] == url


def test_request_link_shape_bound_is_enforced() -> None:
    prefix = "https://x.com/intent/post?text="
    with pytest.raises(ValueError):
        _validated_fields([_field(url=prefix + "a" * (8193 - len(prefix)))], _CONNECT)
