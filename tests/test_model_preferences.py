"""Saved preference codec: choices are opaque data, not inference permission."""

from __future__ import annotations

import json

import pytest

from tinyassets.providers.model_policy import ModelRef
from tinyassets.providers.model_preferences import (
    MAX_FALLBACKS,
    MAX_GENERATION,
    MAX_POLICY_BYTES,
    ModelPreferences,
    capture_preference_policy,
    exact_generation,
    parse_preference_write,
    strict_json,
)


def policy(**updates):
    result = {
        "version": 2,
        "mode": "explicit",
        "saved_default": {"provider_ref": "owned:FutureProvider", "model_id": "Model/未来"},
        "fallbacks": [],
        "efforts": [],
    }
    result.update(updates)
    return result


def legacy_policy(**updates):
    """A stored document from before per-model effort existed."""
    result = {
        "version": 1,
        "mode": "explicit",
        "saved_default": {"provider_ref": "owned:FutureProvider", "model_id": "Model/未来"},
        "fallbacks": [],
    }
    result.update(updates)
    return result


def test_unicode_case_native_default_and_order_roundtrip():
    doc = policy(
        fallbacks=[
            {"provider_ref": "local:UserComputer", "model_id": ""},
            {"provider_ref": "owned:FutureProvider", "model_id": "Other/模型"},
        ]
    )
    prefs = ModelPreferences.from_document(doc)
    assert prefs.document() == doc
    assert ModelPreferences.from_document(strict_json(prefs.canonical_json())) == prefs
    assert prefs.fallbacks[0] == ModelRef("local:UserComputer", "")
    assert prefs.saved_default.model_id == "Model/未来"


def test_empty_order_is_not_automatic_and_roundtrips():
    prefs = ModelPreferences.from_document(policy())
    assert prefs.mode == "explicit" and prefs.fallbacks == ()
    assert prefs.document()["fallbacks"] == []


def test_automatic_is_explicitly_represented():
    doc = policy(mode="automatic", saved_default=None)
    assert ModelPreferences.from_document(doc).document() == doc


def test_stored_version_one_still_reads_as_no_effort_choice():
    """A saved default from before effort existed must not become unreadable.

    The store HOLDS an unparseable row rather than defaulting it, so refusing
    version 1 would not quietly lose the owner's choice -- it would wedge their
    picker. Reading it as "no effort saved" is the truthful interpretation.
    """
    prefs = ModelPreferences.from_document(legacy_policy())
    assert prefs.efforts == ()
    assert prefs.effort_for(ModelRef("owned:FutureProvider", "Model/未来")) == ""
    # Rewriting it upgrades the document in place, without inventing a level.
    assert prefs.document() == policy()


@pytest.mark.parametrize("version", [0, 3, 4, -1, "2", 1.0, True, None])
def test_unknown_preference_version_refuses(version):
    with pytest.raises(ValueError):
        ModelPreferences.from_document(policy(version=version))


def test_effort_is_kept_per_model_and_survives_switching_away():
    doc = policy(efforts=[
        {"provider_ref": "claude-code", "model_id": "claude-opus-5-5", "level": "xhigh"},
        {"provider_ref": "claude-code", "model_id": "claude-sonnet-5-5", "level": "low"},
    ])
    prefs = ModelPreferences.from_document(doc)
    assert prefs.document() == doc
    assert prefs.effort_for(ModelRef("claude-code", "claude-opus-5-5")) == "xhigh"
    # The level for a model that is NOT the saved default is still remembered,
    # which is what makes switching away and back non-destructive.
    assert prefs.saved_default != ModelRef("claude-code", "claude-sonnet-5-5")
    assert prefs.effort_for(ModelRef("claude-code", "claude-sonnet-5-5")) == "low"
    # An unset model carries no level; the executor's own default applies.
    assert prefs.effort_for(ModelRef("claude-code", "claude-haiku-4-5-20251001")) == ""
    assert prefs.effort_for(None) == ""


@pytest.mark.parametrize("efforts", [
    # Two levels for one model: no way to say which the turn would use.
    [{"provider_ref": "p", "model_id": "m", "level": "low"},
     {"provider_ref": "p", "model_id": "m", "level": "high"}],
    [{"provider_ref": "p", "model_id": "m", "level": ""}],       # empty is "unset", not a row
    [{"provider_ref": "p", "model_id": "m", "level": " high"}],  # unnormalized
    [{"provider_ref": "p", "model_id": "m", "level": "hi\ngh"}],
    [{"provider_ref": "p", "model_id": "m", "level": 1}],
    [{"provider_ref": "", "model_id": "m", "level": "low"}],
    [{"provider_ref": "p", "model_id": "m"}],                    # missing level
    [{"provider_ref": "p", "model_id": "m", "level": "low", "extra": 1}],
    ["high"],
    {"p": "high"},
])
def test_malformed_effort_choice_refuses(efforts):
    with pytest.raises(ValueError):
        ModelPreferences.from_document(policy(efforts=efforts))


def test_automatic_mode_still_carries_per_model_effort():
    """Effort is a per-model setting, so it is not an "explicit order" field.

    An automatic plan still lands on some model, and the owner's level for that
    model is the level the turn should run at.
    """
    doc = policy(mode="automatic", saved_default=None, efforts=[
        {"provider_ref": "claude-code", "model_id": "claude-opus-5-5", "level": "max"},
    ])
    prefs = ModelPreferences.from_document(doc)
    assert prefs.mode == "automatic" and prefs.saved_default is None
    assert prefs.effort_for(ModelRef("claude-code", "claude-opus-5-5")) == "max"
    captured = capture_preference_policy(saved=prefs, observed_generation=4)
    assert captured is not None
    assert captured[0].effort_for(ModelRef("claude-code", "claude-opus-5-5")) == "max"


def test_per_turn_override_without_effort_keeps_saved_levels():
    """Switching model for one message is not a decision about effort."""
    saved = ModelPreferences.from_document(policy(efforts=[
        {"provider_ref": "claude-code", "model_id": "claude-opus-5-5", "level": "xhigh"},
    ]))
    current = ModelPreferences.from_document(policy(
        saved_default={"provider_ref": "claude-code", "model_id": "claude-sonnet-5-5"},
    ))
    captured = capture_preference_policy(saved=saved, observed_generation=9, current=current)
    assert captured is not None
    policy_out, source = captured
    assert source == "current"
    assert policy_out.current_selection == ModelRef("claude-code", "claude-sonnet-5-5")
    assert policy_out.effort_for(ModelRef("claude-code", "claude-opus-5-5")) == "xhigh"


def test_absent_preferences_preserve_legacy_path():
    assert capture_preference_policy(saved=None, observed_generation=0) is None


def test_current_order_replaces_saved_order_without_mutating_it():
    saved = ModelPreferences("explicit", ModelRef("saved", "primary"), (ModelRef("s", "f"),))
    current = ModelPreferences("explicit", ModelRef("current", "primary"), ())
    captured, source = capture_preference_policy(
        saved=saved, observed_generation=7, current=current,
    )
    assert source == "current" and captured.generation == 7
    assert captured.current_selection == current.saved_default
    assert captured.saved_default is None and captured.fallbacks == ()
    assert saved.saved_default == ModelRef("saved", "primary")
    assert saved.fallbacks == (ModelRef("s", "f"),)


def test_current_automatic_clears_saved_primary_and_fallbacks():
    saved = ModelPreferences("explicit", ModelRef("saved", "primary"), (ModelRef("s", "f"),))
    captured, source = capture_preference_policy(
        saved=saved, observed_generation=7, current=ModelPreferences("automatic", None, ()),
    )
    assert source == "current" and captured.mode == "automatic"
    assert captured.current_selection is None and captured.saved_default is None
    assert captured.fallbacks == () and captured.generation == 7


@pytest.mark.parametrize("preferences", [
    ModelPreferences("automatic", None, ()),
    ModelPreferences("explicit", ModelRef("subscription", ""), (ModelRef("http", "future"),)),
])
def test_saved_or_one_turn_choice_retains_exact_order_and_provenance(preferences):
    saved, source = capture_preference_policy(saved=preferences, observed_generation=3)
    assert source == "saved" and saved.generation == 3
    assert saved.current_selection is None and saved.saved_default == preferences.saved_default
    assert saved.fallbacks == preferences.fallbacks
    current, source = capture_preference_policy(
        saved=None, observed_generation=0, current=preferences,
    )
    assert source == "current" and current.generation == 0
    assert current.saved_default is None and current.current_selection == preferences.saved_default
    assert current.fallbacks == preferences.fallbacks


@pytest.mark.parametrize("saved,generation", [
    (None, 1), (ModelPreferences("automatic", None, ()), 0), (None, True), ({}, 1),
])
def test_inconsistent_saved_capture_is_not_defaulted(saved, generation):
    with pytest.raises(ValueError):
        capture_preference_policy(saved=saved, observed_generation=generation)


@pytest.mark.parametrize(
    "updates",
    [
        {"version": True},
        {"version": 1.0},
        # 2 is now the CURRENT version and 1 is the readable legacy one, so the
        # "unknown version" case has to be a version that really is unknown.
        {"version": 3},
        {"version": None},
        {"mode": "latest"},
        {"mode": []},
        {"mode": "automatic"},
        {"saved_default": None},
        {"fallbacks": None},
        {"fallbacks": {}},
        {"fallbacks": [None]},
        {"owner_user_id": "victim"},
        {"current_selection": {"provider_ref": "x", "model_id": "y"}},
        {"cost_caps": []},
        {"ranking_source": "client-forged"},
    ],
)
def test_malformed_policy_rejected(updates):
    with pytest.raises(ValueError):
        ModelPreferences.from_document(policy(**updates))


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_ref", ""),
        ("provider_ref", " x"),
        ("provider_ref", "x\n"),
        ("provider_ref", "x" * 401),
        ("model_id", "x" * 201),
        ("model_id", "\ud800"),
        ("model_id", "x\x00"),
        ("model_id", "x\u200b"),
        ("model_id", 123),
        ("model_id", None),
        ("provider_ref", ["x"]),
    ],
)
def test_invalid_reference_rejected(field, value):
    doc = policy()
    doc["saved_default"][field] = value
    with pytest.raises(ValueError):
        ModelPreferences.from_document(doc)


def test_duplicate_primary_or_fallback_rejected():
    doc = policy()
    doc["fallbacks"] = [doc["saved_default"]]
    with pytest.raises(ValueError, match="duplicate"):
        ModelPreferences.from_document(doc)
    doc["fallbacks"] = [{"provider_ref": "p", "model_id": "m"}] * 2
    with pytest.raises(ValueError, match="duplicate"):
        ModelPreferences.from_document(doc)


@pytest.mark.parametrize(
    "raw",
    [
        '{"version":1,"version":2}',
        '{"outer":{"model_id":"one","model_id":"two"}}',
        "[" * 2000,
        b"\xff",
        "\ud800",
        "x" * (MAX_POLICY_BYTES + 1),
    ],
    ids=["duplicate", "nested-duplicate", "deep", "invalid-utf8", "surrogate", "oversized"],
)
def test_strict_json_rejects_ambiguous_invalid_or_oversized(raw):
    with pytest.raises(ValueError):
        strict_json(raw)


@pytest.mark.parametrize("value", [True, False, -1, 1.0, "1", None, MAX_GENERATION + 1])
def test_generation_is_bounded_exact_integer(value):
    with pytest.raises(ValueError):
        exact_generation(value)


def test_write_envelope_no_owner_no_current_no_missing_generation():
    doc = {"expected_generation": 7, "policy": policy()}
    generation, prefs = parse_preference_write(json.dumps(doc).encode())
    assert generation == 7 and prefs.document() == policy()
    for invalid in (
        {"policy": policy()},
        {**doc, "universe_id": "victim"},
        {**doc, "current_selection": None},
        {**doc, "expected_generation": True},
    ):
        with pytest.raises(ValueError):
            parse_preference_write(json.dumps(invalid).encode())


def test_maximum_order_fits_body_and_one_more_refused():
    doc = policy(
        fallbacks=[
            {"provider_ref": "🪐" * 390 + str(n), "model_id": "🪐" * 200}
            for n in range(MAX_FALLBACKS)
        ]
    )
    encoded = json.dumps({"expected_generation": 0, "policy": doc}, ensure_ascii=False).encode()
    assert len(encoded) < MAX_POLICY_BYTES
    assert len(parse_preference_write(encoded)[1].fallbacks) == MAX_FALLBACKS
    doc["fallbacks"].append({"provider_ref": "one-more", "model_id": ""})
    with pytest.raises(ValueError):
        ModelPreferences.from_document(doc)


def test_direct_constructor_cannot_bypass_validation():
    for args in [
        ("explicit", ModelRef("p", "m"), []),
        ("explicit", "p", ()),
        ("automatic", None, (ModelRef("p", "m"),)),
    ]:
        with pytest.raises(ValueError):
            ModelPreferences(*args)


def test_json_must_be_utf8_not_auto_detected_utf16():
    with pytest.raises(ValueError):
        strict_json(json.dumps(policy()).encode("utf-16"))
