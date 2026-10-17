"""Owner fixture shared by app and connection boundary tests."""
import pytest

from tests.test_model_bootstrap_candidate import row
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from tinyassets.api import pending_requests
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.providers import discovery_http

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    (tmp_path / "u-owner").mkdir()
    set_founder_home(tmp_path, founder_sub="owner", universe_id="u-owner", platform_generated=True)
    grant_universe_access(tmp_path, universe_id="u-owner", actor_id="owner", permission="admin")
    monkeypatch.setattr(discovery_http, "read_granted_discovery_document",
                        lambda **kw: {"data": [row()]})
    original_request = pending_requests.request_from_user
    def checked_request(**kwargs):
        result = original_request(**kwargs)
        assert not result.get("error"), result
        return result
    monkeypatch.setattr(pending_requests, "request_from_user", checked_request)
    with identity_context(Identity(user_id="owner", username="owner", capabilities=["write"])):
        yield tmp_path

