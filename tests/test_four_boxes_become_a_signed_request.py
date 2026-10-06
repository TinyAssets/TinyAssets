"""The seam: four labelled boxes the owner fills become a signed OAuth request.

Both halves were tested and the join was not. `test_request_fields_are_answerable`
proves the ask accepts four fields; `test_outbound_scheme_encoding_binding` proves
a well-formed JSON bundle signs correctly. Nothing carried a value from one to the
other, so the ONE thing that has to hold -- that what the ask assembles is what
the signer reads -- rested on two files agreeing about four strings.

That is exactly the shape of the defect Codex round 2 found: the served docs said
`api_key_secret` and the deposit read `api_secret`, and every test passed because
each side was consistent with itself. A name is only correct relative to the thing
that reads it, so the test has to span the seam.

This is the founder's X step. It is a unit-level proof, not a live one -- the
live proof is a real post through the rendered app.
"""
from __future__ import annotations

import json

import pytest

from tests.test_pending_requests import (  # noqa: F401 - fixtures and harness
    _ask,
    _login,
    _make_universe,
    _owner_answer,
    _reset_auth,
)

_VALUES = {
    "api_key": "ck-live-value",
    "api_secret": "cs-live-value",
    "access_token": "at-live-value",
    "access_token_secret": "ats-live-value",
}


@pytest.fixture
def base(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


def _deposit_through_the_rail(udir_uid: str):
    """The ask the agent composes, and the four boxes the owner fills."""
    from tinyassets.api.http_connection import _OAUTH1A_FIELDS

    ask = _ask(
        udir_uid,
        kind="API",
        title="Connect X",
        fields=[
            {"name": name, "label": name.replace("_", " ").title(),
             "type": "secret",
             "help": "Developer Portal -> your app -> Keys and tokens",
             "url": "https://developer.x.com/en/portal/dashboard"}
            for name in _OAUTH1A_FIELDS
        ],
        action={
            "type": "connect_http", "destination": "x:posting",
            "auth_scheme": "oauth1a",
            "endpoints": [{"host": "api.x.com", "path_template": "/2/tweets",
                           "methods": ["POST"]}],
        },
    )
    assert ask.get("request_id"), ask
    return ask, _owner_answer(udir_uid, request_id=ask["request_id"], values=dict(_VALUES))


def test_four_boxes_reach_the_signer_intact(base):
    """Ask -> answer -> vault -> bundle -> Authorization header, one chain."""
    from tinyassets.credential_vault import load_credential_vault
    from tinyassets.storage.outbound_connections import (
        _build_http_secret_bundle,
        _ssrf_auth_headers,
    )

    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _, out = _deposit_through_the_rail("u-1")
    assert not out.get("error"), out

    [record] = [r for r in load_credential_vault(udir)
                if r["credential_type"] == "http"]

    # What the rail assembled is what the signer's builder accepts, under the
    # scheme it was deposited as.
    bundle = _build_http_secret_bundle("oauth1a", record["token"])
    for name, value in _VALUES.items():
        assert bundle.get(name) == value, f"{name} did not survive the round trip"

    header = _ssrf_auth_headers(
        "oauth1a", bundle, method="POST", url="https://api.x.com/2/tweets"
    )["Authorization"]

    assert header.startswith("OAuth ")
    assert 'oauth_consumer_key="ck-live-value"' in header
    assert 'oauth_token="at-live-value"' in header
    assert "oauth_signature=" in header
    # The two SECRETS are signing material and must never be sent.
    assert "cs-live-value" not in header
    assert "ats-live-value" not in header


def test_a_box_named_wrong_is_caught_at_the_ASK_not_at_the_far_end(base):
    """Round 2's defect, pinned at the seam.

    The docs taught `api_key_secret`; the deposit reads `api_secret`. Left to the
    far end, the owner fills four boxes, the deposit succeeds or fails obscurely,
    and X answers 401 with nothing to point at. It has to fail while the ask is
    being built.
    """
    _make_universe(base, "u-1", admin="alice")
    _login("alice")

    out = _ask(
        "u-1", kind="API", title="Connect X",
        fields=[{"name": n, "label": n, "type": "secret"} for n in
                ("api_key", "api_key_secret", "access_token", "access_token_secret")],
        action={"type": "connect_http", "destination": "x:posting",
                "auth_scheme": "oauth1a",
                "endpoints": [{"host": "api.x.com", "path_template": "/2/tweets",
                               "methods": ["POST"]}]},
    )
    assert out.get("error") == "request_invalid"
    assert "api_secret" in out["detail"], "the message must name the box that is wrong"


def test_the_stored_bundle_cannot_be_re_read_under_another_scheme(base):
    """A deposit made here inherits the leak protection.

    Flipping the row to `bearer` would otherwise emit all four secrets verbatim
    as `Authorization: Bearer {json}` (Codex, PR #2525). Asserted on a bundle
    this rail actually produced, not a hand-written one.
    """
    from tinyassets.credential_vault import load_credential_vault
    from tinyassets.storage.outbound_connections import (
        SsrfValidationError,
        _build_http_secret_bundle,
    )

    udir = _make_universe(base, "u-1", admin="alice")
    _login("alice")
    _deposit_through_the_rail("u-1")

    [record] = [r for r in load_credential_vault(udir)
                if r["credential_type"] == "http"]
    for mutated in ("bearer", "header", "basic"):
        with pytest.raises(SsrfValidationError, match="encoding does not match"):
            _build_http_secret_bundle(mutated, record["token"])


def test_the_secret_never_appears_in_what_the_rail_says_back(base):
    """Four boxes mean four chances to echo one into the thread."""
    _make_universe(base, "u-1", admin="alice")
    _login("alice")
    ask, out = _deposit_through_the_rail("u-1")

    said = json.dumps([ask, out])
    for value in _VALUES.values():
        assert value not in said, "a pasted secret came back out of the rail"
