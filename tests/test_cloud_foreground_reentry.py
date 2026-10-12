"""Process admission must cover callbacks and reuse of an existing receipt."""

from types import SimpleNamespace

import pytest

import tinyassets.platform_runtime_provenance as provenance
from tests.test_cloud_only_admission_regressions import ADMITTED, UNADMITTED
from tests.test_run_provider_session import _branch, _run_branch
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.foreground_run_provider import _ForegroundRunProviderSession


def _bind(monkeypatch, verdict):
    observation = provenance.ProcessProvenanceObservation(resolver=lambda: verdict)
    observation.observe()
    monkeypatch.setattr(provenance, "_PROCESS_OBSERVATION", observation)


@pytest.mark.parametrize("admitted", [False, True])
def test_injected_foreground_callable_cannot_bypass_process_admission(
    tmp_path, monkeypatch, admitted
):
    _bind(monkeypatch, ADMITTED if admitted else UNADMITTED)
    calls = []

    def callback(*args, **kwargs):
        calls.append("called")
        return "fixture"

    session = _ForegroundRunProviderSession(
        tmp_path, universe_id="fixture", principal_id="fixture", provider_call=callback
    )
    if admitted:
        with pytest.raises(ProviderAuthorityHeldError, match="not bound to a run"):
            session("test")
        assert calls == []
    else:
        with pytest.raises(PermissionError, match="platform_not_cloud"):
            session("test")
        assert calls == []


def test_reused_foreground_receipt_does_not_replace_process_admission(
    tmp_path, monkeypatch, authenticate_request
):
    _bind(monkeypatch, ADMITTED)

    def after_call(provider, count):
        if count == 1:
            # Model reuse of a session under a different process observation,
            # not mutation of the immutable cached verdict in production.
            _bind(monkeypatch, UNADMITTED)

    _response, provider, _captured = _run_branch(
        tmp_path, monkeypatch, authenticate_request, _branch(node_count=2),
        after_provider_call=after_call,
    )
    assert len(provider.calls) == 1, "cached receipt admitted the unapproved process"


def test_direct_agent_attempt_refuses_before_reading_existing_authority(tmp_path, monkeypatch):
    _bind(monkeypatch, UNADMITTED)
    session = _ForegroundRunProviderSession(
        tmp_path, universe_id="fixture", principal_id="fixture", provider_call=lambda: None
    )
    session._receipt = SimpleNamespace(allowed_roles=("writer",), authority_scope="work")
    session._claim = object()
    reads = []

    def record_read():
        reads.append("authority")
        raise AssertionError("unadmitted process reached authority reads")

    monkeypatch.setattr(session, "_validate_founder_home", record_read)
    try:
        with session._authorize_attempt(role="writer", prompt="test", system="", policy=None):
            pytest.fail("unadmitted attempt yielded a carrier")
    except (PermissionError, ProviderAuthorityHeldError):
        pass
    assert reads == [], "direct agent attempt bypassed process admission"
