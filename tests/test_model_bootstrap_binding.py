"""First-agent preparation cannot serve or repair somebody's private content."""

import pytest

from tinyassets.commons_catalog import create_binding, get_binding, list_bindings, update_binding
from tinyassets.onboarding.model_bootstrap_binding import ensure_bootstrap_binding
from tinyassets.onboarding.serving import _BINDING_PAYLOAD, _platform_definition


@pytest.fixture
def home(tmp_path):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home

    (tmp_path / "u-owner").mkdir()
    grant_universe_access(tmp_path, universe_id="u-owner", actor_id="owner-1",
                          permission="admin", granted_by="owner-1")
    set_founder_home(tmp_path, founder_sub="owner-1", universe_id="u-owner",
                     platform_generated=True)
    return tmp_path


def ensure(home):
    return ensure_bootstrap_binding(home, uid="u-owner", owner="owner-1")


def test_first_binding_stays_inert_and_repeated_resume_is_read_only(home):
    from tinyassets.provider_assignment import load_provider_assignment

    binding = ensure(home)
    assert binding["status"] == "configured"
    assert binding["revision"] == 1
    assert binding["configuration"] == _BINDING_PAYLOAD
    assert ensure(home) == binding
    assert len(list_bindings(home, universe_id="u-owner")) == 1
    assert load_provider_assignment(home, universe_id="u-owner") is None


def test_private_content_is_preserved_not_reset(home):
    first = ensure(home)
    changed = update_binding(home, universe_id="u-owner", binding_id=first["agent_binding_id"],
                             expected_revision=1, updated_by="owner-1",
                             payload={**_BINDING_PAYLOAD, "name": "My custom universe"})
    with pytest.raises(PermissionError, match="requires_review"):
        ensure(home)
    assert get_binding(home, universe_id="u-owner", binding_id=first["agent_binding_id"]) == changed


def test_ambiguous_or_foreign_binding_is_untouched(home):
    definition = _platform_definition(home)
    foreign = create_binding(home, universe_id="u-owner",
                             definition_id=definition["agent_definition_id"],
                             created_by="owner-2", payload=dict(_BINDING_PAYLOAD))
    with pytest.raises(PermissionError, match="requires_review"):
        ensure(home)
    assert list_bindings(home, universe_id="u-owner") == [foreign]
    create_binding(home, universe_id="u-owner", definition_id=definition["agent_definition_id"],
                   created_by="owner-1", payload=dict(_BINDING_PAYLOAD))
    with pytest.raises(PermissionError, match="requires_review"):
        ensure(home)
    assert len(list_bindings(home, universe_id="u-owner")) == 2


def test_another_owners_home_cannot_get_a_binding(home):
    with pytest.raises(PermissionError):
        ensure_bootstrap_binding(home, uid="u-owner", owner="owner-2")
    assert list_bindings(home, universe_id="u-owner") == []
