"""The owner is told which connected source cannot run, not "no model connected".

Live 2026-09-28: the founder's connected codex source was held, and the notice
said the universe had "no model connected yet". Drives the real served turn to
the plan's refusal and composes the notice the app shows from it.
"""

from __future__ import annotations

import pytest

from tests import test_served_model_preferences as prefs

_converse = prefs._converse
rig = prefs.rig
reader = prefs.reader
configured = prefs.configured
served = prefs.served
agent = prefs.agent


@pytest.mark.parametrize("configured", ["mixed"], indirect=True)
def test_a_held_connected_source_is_named_in_the_notice(agent, monkeypatch):
    import tinyassets.universe_server as us
    from tinyassets.conversation_failure import failure_notice
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_preferences import ModelPreferences

    # Custody remains connected, but the installed executor cannot serve it.
    monkeypatch.setattr(agent.served.native, "is_available", lambda: False)
    only_codex = ModelPreferences("explicit", ModelRef("codex", ""), ()).document()

    with pytest.raises(ProviderAuthorityHeldError) as caught:
        _converse(agent, monkeypatch, only_codex)

    record = us._served_failure_record(caught.value, held=True)
    assert (record.code, record.effects) == ("setup_required", "none")
    assert record.provider_detail.startswith("codex (")
    notice = failure_notice(record)
    assert "reconnect one that stopped working" in notice
    assert 'Detail: "codex (' in notice
    assert "no model connected" not in notice
