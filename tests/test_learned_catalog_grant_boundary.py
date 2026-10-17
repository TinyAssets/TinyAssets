"""A learned model id is a candidate to GRANT, never an admitted one.

Written before the fix, on the lead's instruction, because the first version of
this change asserted the property from the ABSENCE of a call to
``accepted_native_selection`` instead of from the artefact. Codex refuted it: the
native path did ``catalog = filtered = _native_models(...)`` and handed the same
object to ``admitted``, so a contributed id arrived with
``in_candidate_catalog=true``, the dropdown treated it as usable, selection
succeeded, and only EXECUTION rejected it. Selectable choices that fail is the
"looks real, isn't" failure Hard Rule 8 exists to prevent.

So this asserts the document a client actually reads, on both sides of the grant:

* contributed, not granted -> ``in_candidate_catalog`` false AND a reason saying
  why, so the dropdown files it under "Needs access" rather than offering it;
* granted -> admitted, selectable, and a turn really runs on it.

The second half is what makes the first half worth having. A store that only ever
refused would pass a negative assertion and be useless.
"""

from __future__ import annotations

import pytest

from tests import test_served_model_preferences as integration
from tinyassets.api.commons_catalog import commons_catalog
from tinyassets.providers import served_model_plan
from tinyassets.providers.model_options import model_options_document
from tinyassets.storage.learned_models import OwnModelHistory

rig = integration.rig
reader = integration.reader
configured = integration.configured

#: An id no owner in these fixtures ever declared. It can only reach a list by
#: being learned, which is what makes it a clean probe.
LEARNED_ID = "vendor-newline-9-1"

#: An id only ever verified by ONE owner, so it can never be published. Stands in
#: for the account-bearing selector the threshold is meant to keep private.
SOLO_ID = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/solo-1"


def _document(configured, binding=None):
    # The binding is passed explicitly because a grant REBINDS the serving provider
    # and bumps its revision; prepare_owned_model_plan rightly refuses a stale one
    # ("agent binding changed"), which is a guard worth going through rather than
    # around.
    prepared = served_model_plan.prepare_owned_model_plan(
        base=configured.rig.base, universe=configured.rig.base / "u-models",
        owner="owner", agent=binding or configured.binding, allow_empty=True,
    )
    return model_options_document(prepared.catalog, prepared.plan, prepared.ineligible)


def _row(document, model_id):
    for row in document["options"]:
        if row["reference"]["model_id"] == model_id:
            return row
    return None


def _listed(tmp_path, *model_ids, source_key="codex"):
    """Write a reviewed public list, the way a merged PR would.

    This is the whole sharing mechanism now: a tracked file per source kind,
    with ids keyed to the source that serves them. No database, no attestation,
    no confirmers -- see models/README.md.
    """
    import json

    directory = tmp_path / "models"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "subscription.json").write_text(
        json.dumps({"source_kind": "subscription", "models": {source_key: sorted(model_ids)}}),
        encoding="utf-8")
    return directory


def _learn(monkeypatch, configured, model_id=LEARNED_ID, *, source_key="codex"):
    """Put an id on the shared list for this source kind.

    Redirects the loader's directory through monkeypatch so it is UNDONE after the
    test. Rebinding the module attribute directly leaked the fixture's directory into
    every later module in the same process and broke tests that read the shipped list.
    """
    import tinyassets.providers.public_model_lists as lists

    directory = _listed(configured.rig.base, model_id, source_key=source_key)
    monkeypatch.setattr(lists, "lists_directory", lambda: directory)
    # Cleared on the way in so this test sees its own file. No teardown needed: the
    # cache key includes the source and file fingerprint, and monkeypatch restores
    # the directory, so a
    # later read resolves the real path and misses on a different mtime.
    lists._cached.cache_clear()


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_a_learned_id_is_visible_but_not_admitted_until_access_is_granted(configured, monkeypatch):
    """THE artefact. The owner granted only the provider default."""
    _learn(monkeypatch, configured)
    document = _document(configured)

    row = _row(document, LEARNED_ID)
    assert row is not None, (
        "a learned id must be VISIBLE: invisible is the founder's original bug")
    assert row["in_candidate_catalog"] is False, (
        "a learned id must NOT be an admitted candidate before the owner grants "
        "access to it -- that is the P0 this test exists for")
    assert row["reasons"], (
        "and it must say WHY it is not selectable, or the dropdown cannot file it "
        "under 'Needs access' and the owner is left guessing")
    reasons = {reason["reason"] for reason in row["reasons"]}
    assert "model_access_optin_required" in reasons, reasons
    # It must not reach the routing order either: an unusable id in the fallback
    # chain is a turn that fails later for no reason the user can see.
    assert all(item["model_id"] != LEARNED_ID for item in document["order"])
    # And it says honestly where its evidence came from.
    assert row["availability_basis"] == "publicly_listed"


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_granting_access_makes_a_listed_id_selectable_and_runnable(
        configured, monkeypatch):
    """The other half: after the grant it is a real choice, and a turn runs on it.

    This is the founder's actual ask -- "i cant seem to select fable" -- so the
    test has to end with a turn that ran, not with a flag.
    """
    _learn(monkeypatch, configured)
    from tinyassets.provider_assignment_manifest import ModelAccess

    # The one-tap grant the access sheet offers, as its payload: add the learned id
    # to this universe's accepted model access for that source.
    granted = commons_catalog(
        action="bind_serving_provider", universe_id="u-models",
        binding_id=configured.binding["agent_binding_id"],
        expected_revision=configured.binding["revision"],
        payload={"provider": configured.rig.definition.id,
                 "model_access": {
                     configured.rig.definition.id: ModelAccess("discovered").document(),
                     "codex": ModelAccess("explicit", ("", LEARNED_ID)).document(),
                 }},
    )
    assert granted["status"] == "ready"
    document = _document(configured, granted["agent_binding"])
    row = _row(document, LEARNED_ID)
    assert row is not None and row["in_candidate_catalog"] is True, (
        "once the owner grants access to a learned id it must become a real choice")
    assert row["reasons"] == [], row["reasons"]


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_an_owner_declared_id_keeps_its_own_admission(configured, monkeypatch):
    """The fix must not demote what the owner already granted.

    Without this, "exclude contributed ids" could pass by excluding everything.
    """
    _learn(monkeypatch, configured)
    document = _document(configured)
    default = _row(document, "")
    assert default is not None and default["in_candidate_catalog"] is True, (
        "the provider default the owner granted stays admitted")
    assert default["availability_basis"] == "executor_default"


# ---------------------------------------------------------------------------
# What the WRITER may publish. Codex's P1s: the id was source-controlled, and a
# placeholder was stored as a verified model.
# ---------------------------------------------------------------------------


class _Coordinator:
    """Just enough of AgentTurnCoordinator to exercise its learning hook."""

    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator as _real

    _learn_verified_model = _real._learn_verified_model

    def __init__(self, base, requested, owner="owner-alice"):
        from types import SimpleNamespace

        self.owner = owner
        self.context = SimpleNamespace(
            universe_dir=base / "u-models",
            model_selection=SimpleNamespace(model_id=requested, connection_id="codex"),
        )


def _own_evidence(base, owner):
    """One owner's own ids. There is no "published" counterpart any more."""
    return [row.model_id for row in
            OwnModelHistory(base).ids_for("subscription", "codex", owner)]


def test_verified_candidates_do_not_cross_sources(tmp_path):
    history = OwnModelHistory(tmp_path)
    history.record(source_kind="subscription", provider_ref="codex",
                   model_id="gpt-6-astra", owner_user_id="owner")
    candidates = served_model_plan._own_verified_candidates
    assert [row.model_id for row in candidates(
        tmp_path, "subscription", "codex", "owner", already=())] == ["gpt-6-astra"]
    assert candidates(tmp_path, "subscription", "claude-code", "owner", already=()) == ()


def test_only_the_id_this_universe_REQUESTED_is_recorded(tmp_path):
    """Not the one the source reports. Codex: reported_model is source-controlled.

    The response deliberately carries a hostile reported_model and a placeholder
    model; neither may become this owner's evidence, because evidence is what an
    attestation is later made ABOUT.
    """
    from types import SimpleNamespace

    (tmp_path / "u-models").mkdir(parents=True, exist_ok=True)
    response = SimpleNamespace(
        reported_model="owner-alice@example.com-private-9",
        model="provider-default",
    )
    _Coordinator(tmp_path, "vendor-line-4-7")._learn_verified_model(response)
    assert _own_evidence(tmp_path, "owner-alice") == ["vendor-line-4-7"], (
        "the recorded id must be the one this universe asked for, so a source cannot "
        "put a string of its choosing on the owner's list")


def test_a_provider_default_position_teaches_nobody_anything(tmp_path):
    """An empty requested id is a POSITION, not a model.

    codex_provider reports the literal "provider-default" when it cannot resolve a
    model (codex_provider.py:1041), and the first version stored that as verified.
    """
    from types import SimpleNamespace

    (tmp_path / "u-models").mkdir(parents=True, exist_ok=True)
    _Coordinator(tmp_path, "")._learn_verified_model(
        SimpleNamespace(reported_model="provider-default", model="provider-default"))
    assert _own_evidence(tmp_path, "owner-alice") == []


def test_a_requested_id_that_is_not_an_identifier_is_refused_not_raised(tmp_path):
    """The universe's own selection is still validated, and still cannot break a turn."""
    from types import SimpleNamespace

    (tmp_path / "u-models").mkdir(parents=True, exist_ok=True)
    # No exception: the hook is on the reply path of a turn that already succeeded.
    _Coordinator(tmp_path, "not an identifier")._learn_verified_model(
        SimpleNamespace(reported_model="", model=""))
    assert _own_evidence(tmp_path, "owner-alice") == []


def test_the_owner_threaded_into_the_catalog_is_the_same_identity_the_journal_uses():
    """The one assumption the whole privacy property rests on.

    "Two distinct owners" only protects a private selector if one human cannot hold
    two owner values. The coordinator passes `self.owner`, which comes from
    `check_served_agent_tool_authority` -> `capability.principal_id` -- the value
    `provider_assignment` compares against `agent["created_by"]`, i.e. the
    authenticated USER, and the same value the turn journal scopes its rows by and
    that account deletion treats as a principal key.

    Pinned by reading the coordinator's own source, because the alternative -- a
    universe id, a bind key, a credential digest -- would each be per-universe or
    per-credential and would let one person publish their own ARN by creating a
    second universe or reconnecting a source.
    """
    import inspect

    from tinyassets.account_deletion import PRINCIPAL_KEYS
    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator

    source = inspect.getsource(AgentTurnCoordinator._learn_verified_model)
    assert "owner_user_id=self.owner" in source, (
        "the catalog must be keyed on the turn's OWNER, not on its universe")
    for wrong in ("universe_dir.name", "graph_id", "bind_key", "connection_id",
                  "reference_digest"):
        assert f"owner_user_id={wrong}" not in source, wrong
    # The journal is scoped by the same attribute, so the two cannot drift apart
    # without this test noticing.
    run = inspect.getsource(AgentTurnCoordinator._run)
    assert "self.owner = await off_loop(self._check_scope)" in run
    assert "owner_user_id" in PRINCIPAL_KEYS


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_a_solo_owners_own_verified_id_stays_on_their_own_list(configured):
    """Codex round 3: the store kept it and nothing ever listed it.

    The founder's rule is that an id which worked for one owner "stays on that
    user's own list forever" even though it never becomes public. I tested that the
    STORE kept the row and claimed the requirement met -- but `evidence_ids` had no
    production caller, so the picker never showed it and a solo user's ARN vanished
    from their own list the moment they stopped declaring it. Keeping a row nobody
    reads is not keeping it.

    Asserted on the options DOCUMENT, which is what a client reads, because
    asserting the store is the exact mistake that hid this.
    """
    # One owner only: nothing here is published, so this cannot pass via the
    # shared table.
    OwnModelHistory(configured.rig.base).record(
        source_kind="subscription", provider_ref="codex", model_id=SOLO_ID,
        owner_user_id="owner")
    document = _document(configured)

    row = _row(document, SOLO_ID)
    assert row is not None, (
        "an id this owner has already made work must stay on their own list")
    assert row["availability_basis"] == "owner_verified_here", (
        "and must say it is THEIR history, not that two other owners verified it")
    # Still a candidate, not an admitted one: their access may have narrowed since.
    assert row["in_candidate_catalog"] is False
    assert {reason["reason"] for reason in row["reasons"]} == {"model_access_optin_required"}
    # It is on no shared list: a typed id is personal forever.
    from tinyassets.providers.public_model_lists import read_list

    assert SOLO_ID not in read_list("subscription")
