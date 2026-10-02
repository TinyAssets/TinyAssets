"""Provider-neutral config records are a projection, never launch authority."""

import json
from dataclasses import replace

import pytest
import yaml

from tinyassets import provider_authority
from tinyassets.config import load_universe_config, write_provider_assignment_projection
from tinyassets.provider_assignment import load_provider_assignment
from tinyassets.provider_assignment_manifest import AssignmentCandidate, ModelAccess
from tinyassets.provider_serving_binding import resolve_serving_agent_binding


def _member(provider="future-source:blue"):
    return AssignmentCandidate(
        provider=provider,
        binding_id="binding-" + provider,
        binding_generation=3,
        binding_digest="binding-digest-" + provider,
        credential_reference_id="private-custody-reference",
        credential_reference_generation=7,
        credential_reference_digest="private-custody-digest",
        access=ModelAccess("explicit", ("future-model",)),
    )


def _binding(member):
    return {
        "binding_id": member.binding_id,
        "generation": member.binding_generation,
        "binding_digest": member.binding_digest,
        "assignment_digest": "assignment-digest",
    }


def _publish(path, **overrides):
    member = _member()
    return write_provider_assignment_projection(path, **{
        "state": "ready",
        "generation": 8,
        "provider": member.provider,
        "binding": _binding(member),
        "assignment_candidates": (member,),
        **overrides,
    })


def test_unfamiliar_members_publish_sorted_nonsecret_projection(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("user_option:\n  keep: true\n", encoding="utf-8")
    root, other = _member(), _member("another-source:green")
    _publish(tmp_path, assignment_candidates=(root, other))
    actual = yaml.safe_load(config.read_text(encoding="utf-8"))
    # config.yaml keeps the preferences; authority is in the platform record
    # (tinyassets.provider_authority, command-center-cutover E6).
    assert actual == {
        "user_option": {"keep": True},
        "preferred_writer": root.provider,
        "engine_source": "requester_local",
    }
    record = json.loads(provider_authority.record_path(tmp_path).read_text(encoding="utf-8"))
    assert record == {
        "allowed_providers": [other.provider, root.provider],
        "engine_assignment_generation": 8,
        "engine_assignment_state": "ready",
        "provider_authority_bindings": {
            other.provider: _binding(other), root.provider: _binding(root),
        },
    }
    assert sorted(record["provider_authority_bindings"]) == [other.provider, root.provider]
    assert "private-custody" not in config.read_text(encoding="utf-8")
    assert "future-model" not in config.read_text(encoding="utf-8")


@pytest.mark.parametrize("overrides", [
    {"assignment_candidates": ()},
    {"assignment_candidates": [_member()]},
    {"assignment_candidates": {"future-source:blue": _binding(_member())}},
    {"assignment_candidates": (_binding(_member()),)},
    {"assignment_candidates": (_member(), _member())},
    {"assignment_candidates": (_member("other"),)},
    {"assignment_candidates": (replace(_member(), binding_id="different"),)},
    {"binding": {**_binding(_member()), "generation": True}},
    {"binding": {**_binding(_member()), "generation": 4}},
    {"binding": {**_binding(_member()), "binding_digest": "different"}},
    {"binding": {**_binding(_member()), "assignment_digest": ""}},
    {"binding": {**_binding(_member()), "assignment_digest": None}},
    {"binding": {**_binding(_member()), "extra": "not-projected"}},
    {"binding": {"binding_id": "incomplete"}},
    {"state": "pending"},
    {"state": "failed"},
    {"state": "unassigned"},
    {"generation": 0},
    {"generation": True},
    {"assignment_candidates": None},
])
def test_invalid_projection_never_changes_existing_config(tmp_path, overrides):
    config = tmp_path / "config.yaml"
    before = b"user_option: preserve-verbatim\n"
    config.write_bytes(before)
    with pytest.raises(ValueError):
        _publish(tmp_path, **overrides)
    assert config.read_bytes() == before
    assert list(tmp_path.iterdir()) == [config]


@pytest.mark.parametrize("provider", ["claude-code", "codex", "api_key_http:legacy"])
def test_legacy_single_source_contract_is_unchanged(tmp_path, provider):
    binding = {"binding_id": "legacy-binding", "legacy_extension": "preserved"}
    _publish(tmp_path, provider=provider, binding=binding, assignment_candidates=None)
    loaded = load_universe_config(tmp_path)
    assert loaded.allowed_providers == [provider]
    assert loaded.provider_authority_bindings == {provider: binding}


@pytest.mark.parametrize("content", ["- not-a-mapping\n", "invalid: [\n", ""])
def test_unreadable_config_is_not_overwritten(tmp_path, content):
    config = tmp_path / "config.yaml"
    config.write_text(content, encoding="utf-8")
    with pytest.raises((ValueError, yaml.YAMLError)):
        _publish(tmp_path)
    assert config.read_text(encoding="utf-8") == content


def test_projection_cannot_create_authoritative_assignment_or_serving_binding(tmp_path):
    universe = tmp_path / "universe"
    _publish(universe)
    assert load_provider_assignment(tmp_path, universe_id="u-owner") is None
    with pytest.raises(PermissionError, match="founder serving binding"):
        resolve_serving_agent_binding(
            tmp_path, universe_id="u-owner", owner_user_id="owner-1",
        )
