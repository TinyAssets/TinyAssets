"""Successful HTTP inference records only the owner's requested model id."""

from __future__ import annotations

from tests import test_served_model_preferences as integration
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
            model_selection=SimpleNamespace(
                model_id=requested, connection_id="api_key_http:fixture"),
        )


def _own_evidence(base, owner):
    """One owner's own ids. There is no "published" counterpart any more."""
    return [row.model_id for row in
            OwnModelHistory(base).ids_for("http", "api_key_http:fixture", owner)]




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
