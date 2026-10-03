"""A four-box ask must deposit all four values.

The credential ask became one field per value so the owner never has to work out
what goes where. The answer path had not caught up: it took the FIRST secret
field and silently discarded the rest —

    secret = next((values.get(n) for n in secret_names if values.get(n)), "")

— which was harmless while an ask was one unlabelled box and broken the moment
it stopped being. An OAuth 1.0a owner would fill four boxes, three would vanish,
and the deposit would refuse a malformed bundle with nothing to say which box
had been thrown away.

This is the seam between "ask for four things" and "deposit one credential", and
it is exactly the kind of place the last six gates lived: each side correct, the
join never exercised.
"""
from __future__ import annotations

import json

import pytest

from tests.test_pending_requests import (  # noqa: F401 - fixtures
    _answer,
    _ask,
    _login,
    _make_universe,
    # The autouse logout. An autouse fixture applies only to the module that
    # DEFINES or imports it, so without this `_login()` left a static
    # "founder" provider bound for the rest of the session -- and later files
    # (test_phase7_h2_goals_cutover: KeyError 'status'; test_onboarding_model_
    # connect: current_home_changed) ran as that founder whenever they shared
    # a process with this one (2026-10-01; #4201's 4-way split, -n 5).
    _reset_auth,
)


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Declared here rather than imported: a fixture whose name is also every
    test's parameter name reads to the linter as a redefinition at each use."""
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root

_OAUTH1A = [
    {"name": "api_key", "type": "secret", "label": "API Key",
     "help": "Developer portal -> your app -> Keys and tokens",
     "url": "https://developer.example.com/portal"},
    {"name": "api_secret", "type": "secret", "label": "API Key Secret"},
    {"name": "access_token", "type": "secret", "label": "Access Token"},
    {"name": "access_token_secret", "type": "secret", "label": "Access Token Secret"},
]

_VALUES = {
    "api_key": "ck-value",
    "api_secret": "cs-value",
    "access_token": "at-value",
    "access_token_secret": "ats-value",
}


def _four_box_ask(uid: str):
    return _ask(
        uid,
        fields=_OAUTH1A,
        action={
            "type": "connect_http",
            "destination": "some-service",
            "host": "api.example.com",
            "path_template": "/v2/post",
            "methods": ["POST"],
            "auth_scheme": "oauth1a",
        },
    )


def test_all_four_values_reach_the_deposit(base) -> None:
    """The headline: fill four boxes, all four are stored."""
    udir = _make_universe(base, "u-1", admin="founder")
    _login("founder")
    asked = _four_box_ask("u-1")

    out = _answer("u-1", request_id=asked["request_id"], values=dict(_VALUES))
    assert out.get("status") == "answered", out

    from tinyassets.credential_vault import load_credential_vault

    [record] = [
        r for r in load_credential_vault(udir)
        if str(r.get("credential_type") or "").lower() == "http"
    ]
    stored = json.loads(record["token"])
    assert stored == _VALUES, "a value the owner typed did not reach the vault"


def test_a_missing_box_is_refused_rather_than_deposited_short(base) -> None:
    """Partial is worse than refused.

    A bundle short one value deposits a credential that cannot sign, and the
    owner finds out later from a failing call with no idea which box was empty.
    """
    udir = _make_universe(base, "u-2", admin="founder")
    _login("founder")
    asked = _four_box_ask("u-2")

    short = dict(_VALUES)
    del short["access_token_secret"]
    out = _answer("u-2", request_id=asked["request_id"], values=short)

    assert "missing" in json.dumps(out).lower(), out
    assert "access_token_secret" in json.dumps(out)

    from tinyassets.credential_vault import load_credential_vault

    assert load_credential_vault(udir) == [], "nothing may be deposited"


def test_the_refusal_names_the_empty_box(base) -> None:
    """The owner has to know WHICH one, or they are guessing again."""
    _make_universe(base, "u-3", admin="founder")
    _login("founder")
    asked = _four_box_ask("u-3")

    out = _answer(
        "u-3",
        request_id=asked["request_id"],
        values={"api_key": "ck", "api_secret": "cs", "access_token": "at"},
    )
    assert "access_token_secret" in json.dumps(out)


def test_a_single_box_ask_is_unchanged(base) -> None:
    """The ordinary bearer case must not have grown a JSON wrapper."""
    udir = _make_universe(base, "u-4", admin="founder")
    _login("founder")
    asked = _ask("u-4")  # the shared fixture: one secret field

    out = _answer("u-4", request_id=asked["request_id"],
                  values={"secret": "ghp_" + "x" * 36})
    assert out.get("status") == "answered", out

    from tinyassets.credential_vault import load_credential_vault

    [record] = [
        r for r in load_credential_vault(udir)
        if str(r.get("credential_type") or "").lower() == "http"
    ]
    assert record["token"] == "ghp_" + "x" * 36, "a bare token must stay bare"


def test_no_secret_at_all_is_still_refused(base) -> None:
    _make_universe(base, "u-5", admin="founder")
    _login("founder")
    asked = _four_box_ask("u-5")

    out = _answer("u-5", request_id=asked["request_id"], values={})
    assert "required" in json.dumps(out).lower() or "missing" in json.dumps(out).lower()


def test_the_bundle_keys_are_the_field_names(base) -> None:
    """The agent chooses the field names, and for a scheme with a fixed bundle
    shape it must use that scheme's names — so the join is name-for-name and
    nothing here has to know which service it is."""
    udir = _make_universe(base, "u-6", admin="founder")
    _login("founder")
    asked = _four_box_ask("u-6")
    _answer("u-6", request_id=asked["request_id"], values=dict(_VALUES))

    from tinyassets.credential_vault import load_credential_vault

    [record] = [
        r for r in load_credential_vault(udir)
        if str(r.get("credential_type") or "").lower() == "http"
    ]
    assert sorted(json.loads(record["token"])) == sorted(f["name"] for f in _OAUTH1A)


def test_one_box_of_four_is_refused_not_deposited_as_the_whole_credential(base) -> None:
    """The exact hole a cross-family review found in the first version of this.

    Completeness was judged on how many boxes came back FILLED rather than how
    many the ask DECLARED, so a single filled box took the single-value branch
    and was deposited as the entire credential — skipping the missing-field
    check written for precisely this case, which only ran when two or more were
    filled. The owner would have "successfully" deposited a quarter of an OAuth
    credential and found out from a failing call much later.
    """
    udir = _make_universe(base, "u-7", admin="founder")
    _login("founder")
    asked = _four_box_ask("u-7")

    out = _answer(
        "u-7", request_id=asked["request_id"], values={"api_key": "ck-only"}
    )

    blob = json.dumps(out)
    assert "missing" in blob.lower(), out
    for absent in ("api_secret", "access_token", "access_token_secret"):
        assert absent in blob, f"the refusal must name {absent}"

    from tinyassets.credential_vault import load_credential_vault

    assert load_credential_vault(udir) == [], "a partial credential was deposited"


def test_a_legacy_request_stored_under_the_old_rules_is_refused(base) -> None:
    """A row created before these rules must not bypass them when answered.

    Fields are validated when an ask is CREATED. A request stored earlier can
    carry a shape the current rules refuse — a `text` field beside the secret,
    whose answer is recorded in the clear, or several secrets under a
    single-value scheme, which would assemble into JSON and be deposited as a
    bearer token. Answering did not re-run the validator, so a legacy row
    bypassed both fixes (Codex round 2, Q2/Q5).

    Written directly to storage, because the ask API can no longer create one.
    """
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.storage.pending_requests import create_request

    udir = _make_universe(base, "u-legacy", admin="founder")
    _login("founder")

    kind, title = "API", "pre-upgrade ask"
    body = "stored before the current rules"
    fields = [
        {"name": "part_a", "label": "Part A", "type": "secret"},
        {"name": "part_b", "label": "Part B", "type": "secret"},
    ]
    action = {
        "type": "connect_http",
        "destination": "legacy-service",
        "auth_scheme": "bearer",          # single-value scheme, two secrets
        "endpoints": [{"host": "api.example.com",
                       "path_template": "/v1/x", "methods": ["POST"]}],
    }
    # The REAL dedupe key, computed the way the old code computed it. An
    # arbitrary string here is not a legacy row -- it is a tampered one, and it
    # trips the binding check instead of the legacy-fields check this test is
    # about. A genuine pre-upgrade row reproduces its key and has to be refused
    # on its FIELDS, which is the harder thing to get right.
    legacy_id = create_request(
        _universe_dir("u-legacy"),
        kind=kind,
        title=title,
        body=body,
        fields=fields,
        action=action,
        dedupe_key=json.dumps([kind, title, body, fields, action],
                              sort_keys=True, separators=(",", ":")),
    )
    request_id = legacy_id if isinstance(legacy_id, str) else legacy_id["request_id"]

    out = _answer(
        "u-legacy",
        request_id=request_id,
        values={"part_a": "alpha-token", "part_b": "beta-token"},
    )

    blob = json.dumps(out)
    assert "before the current rules" in blob, out

    from tinyassets.credential_vault import load_credential_vault

    assert load_credential_vault(udir) == [], "a legacy row deposited anyway"
