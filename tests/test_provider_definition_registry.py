"""ProviderDefinition registry — the open, user-defined compute provider set.

Requirement source:
``openspec/changes/compute-agnostic-provider-set/`` (design §1-3, §7) and the
provider-routing delta "Providers are open, user-defined definitions; registration
is not authority" + "a commons/remixed definition never carries a credential".

Covers: register creates a CANDIDATE ONLY (no authority/credential side-effect),
deterministic-id idempotency, owner-conflict refusal, access-method/protocol
coherence + input validation, the commons SHAPE-only listing, and remix that never
carries the original owner's ref/credential.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.providers import definition as pd
from tinyassets.providers.definition import ProviderDefinitionError


@pytest.fixture
def base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    return root


def _reg_http(uid: str, owner: str = "founder", **kw: object) -> pd.ProviderDefinition:
    doc: dict = {
        "universe_id": uid,
        "owner_user_id": owner,
        "access_method": "api_key_http",
        "protocol": "openai_chat",
        "model": "moonshotai/kimi-k2",
        "ref": "http_deadbeefdeadbeefdeadbeefdeadbeef",
    }
    doc.update(kw)
    return pd.register_definition(**doc)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Register creates a candidate ONLY.
# --------------------------------------------------------------------------- #


def test_register_creates_candidate_only(base: Path) -> None:
    d = _reg_http("u-cand")
    assert d.id.startswith("provdef_")
    assert d.access_method == "api_key_http"
    assert d.visibility == "private"
    # Persisted + retrievable.
    assert pd.get_definition("u-cand", d.id) == d
    assert pd.list_definitions("u-cand") == [d]
    # Candidate-only: the ONLY file written under the universe dir is the registry
    # store — no enrollment, serving-binding, credential, or authority artifact.
    udir = base / "u-cand"
    written = sorted(p.name for p in udir.iterdir())
    assert written == ["provider_definitions.json"]


def test_registration_is_idempotent(base: Path) -> None:
    first = _reg_http("u-idem")
    second = _reg_http("u-idem")
    assert first.id == second.id
    assert pd.list_definitions("u-idem") == [first]  # no duplicate row


def test_deterministic_id_is_stable_across_universes_differs(base: Path) -> None:
    a = _reg_http("u-a")
    b = _reg_http("u-b")
    # Same descriptor, different universe -> different id (universe is in the material).
    assert a.id != b.id


def test_different_owners_get_separate_definitions_no_squat(base: Path) -> None:
    # owner_user_id is part of the id material (Codex review): two owners registering
    # the byte-identical descriptor get DISTINCT ids — no shared, predictable slot for
    # one principal to pre-register and squat.
    a = _reg_http("u-own", owner="founder")
    b = _reg_http("u-own", owner="intruder")
    assert a.id != b.id
    assert {d.id for d in pd.list_definitions("u-own")} == {a.id, b.id}
    assert pd.get_definition("u-own", a.id).owner_user_id == "founder"
    assert pd.get_definition("u-own", b.id).owner_user_id == "intruder"


def test_corrupt_store_raises_not_clobbers(base: Path) -> None:
    _reg_http("u-corrupt")
    store = base / "u-corrupt" / "provider_definitions.json"
    store.write_text("{ not valid json", encoding="utf-8")
    # A PRESENT-but-corrupt store must fail loud — never silently [] then overwrite,
    # which would discard `d`.
    with pytest.raises(ProviderDefinitionError):
        pd.list_definitions("u-corrupt")
    with pytest.raises(ProviderDefinitionError):
        _reg_http("u-corrupt")


def test_owner_may_toggle_visibility_without_new_id(base: Path) -> None:
    priv = _reg_http("u-vis", visibility="private")
    pub = _reg_http("u-vis", visibility="commons")
    assert priv.id == pub.id
    assert pub.visibility == "commons"
    assert len(pd.list_definitions("u-vis")) == 1


# --------------------------------------------------------------------------- #
# Validation + access-method/protocol coherence.
# --------------------------------------------------------------------------- #




@pytest.mark.parametrize(
    "bad",
    [
        {"access_method": "sdk_direct"},
        {"visibility": "public"},
        {"protocol": "grpc"},
        {"model": ""},
        {"model": "x" * 201},
        {"ref": "has space"},
        {"ref": ""},
    ],
)
def test_invalid_inputs_rejected(base: Path, bad: dict) -> None:
    with pytest.raises(ProviderDefinitionError):
        _reg_http("u-bad", **bad)


def test_missing_identity_rejected(base: Path) -> None:
    with pytest.raises(ProviderDefinitionError):
        _reg_http("", owner="f")
    with pytest.raises(ProviderDefinitionError):
        _reg_http("u-x", owner="")


# --------------------------------------------------------------------------- #
# Commons listing + remix never carries a credential/ref.
# --------------------------------------------------------------------------- #


def test_commons_listing_is_shape_only(base: Path) -> None:
    _reg_http("u-src", owner="alice", visibility="commons")
    _reg_http("u-priv", owner="bob", visibility="private")  # must NOT appear
    views = pd.list_commons_definitions(base)
    assert len(views) == 1
    view = views[0]
    # SHAPE only — no owner, no ref (another user's connection).
    assert "owner_user_id" not in view
    assert "ref" not in view
    assert view["access_method"] == "api_key_http"
    assert view["model"] == "moonshotai/kimi-k2"


def test_remix_creates_private_candidate_with_new_ref(base: Path) -> None:
    _reg_http("u-src", owner="alice", visibility="commons")
    view = pd.list_commons_definitions(base)[0]

    remixed = pd.remix_definition(
        source_public_view=view,
        into_universe_id="u-dst",
        new_owner_user_id="carol",
        new_ref="http_carolownconnection00000000000",
    )
    assert remixed.owner_user_id == "carol"
    assert remixed.visibility == "private"
    assert remixed.universe_id == "u-dst"
    # The remixer's OWN ref — never the source owner's.
    assert remixed.ref == "http_carolownconnection00000000000"
    assert remixed.ref != "http_deadbeefdeadbeefdeadbeefdeadbeef"
    # Shape preserved.
    assert remixed.access_method == "api_key_http"
    assert remixed.model == "moonshotai/kimi-k2"


def test_remix_refuses_a_full_definition_dict(base: Path) -> None:
    """Guard: passing a full definition (with ref/owner) instead of a public view
    must be refused, so a credential/connection is never silently imported."""
    d = _reg_http("u-src", owner="alice", visibility="commons")
    with pytest.raises(ProviderDefinitionError):
        pd.remix_definition(
            source_public_view=d.as_dict(),  # carries ref + owner_user_id
            into_universe_id="u-dst",
            new_owner_user_id="carol",
            new_ref="http_carolownconnection00000000000",
        )
