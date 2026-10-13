"""Fresh step authority against real foreground stores, not tool-execution proof."""

import sqlite3
from contextlib import contextmanager

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_run_provider_session import _branch, _run_branch
from tests.test_work_model_selection import http_wire  # noqa: F401 - pytest fixture
from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.providers.base import ModelConfig, UniverseContext
from tinyassets.storage.provider_work_authority import db_path

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.mark.parametrize("manifest", [False, True])
@pytest.mark.parametrize("change", [
    None, "closed", "subject", "cancelled", "revoked", "receipt", "claim", "home", "seal",
])
@pytest.mark.usefixtures("http_wire")
def test_step_rechecks_current_authority_without_new_reservation(
    tmp_path, monkeypatch, authenticate_request, manifest, change,
):
    # Avoid persona/tool transport setup: only admission and its independent
    # between-step fence are in scope. No engine tool is ever executed here.
    monkeypatch.setattr(
        "tinyassets.shared_self.prepare_shared_self_turn",
        lambda base, uid, owner, prompt, config, node=None: (prompt, "", ModelConfig()),
    )
    original = _ForegroundRunProviderSession._authorize_attempt
    observations = []

    @contextmanager
    def authorize(session, **kwargs):
        with original(session, **kwargs) as launch:
            carrier = launch[0]
            assert session._check_agent_authority(carrier) == "acct_alice"
            yield launch
            # Provider has settled; read-only step checking remains valid, but
            # must not consume/rearm the already consumed inference carrier.
            assert session._check_agent_authority(carrier) == "acct_alice"
            with sqlite3.connect(db_path(tmp_path)) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM provider_invocation_reservations",
                ).fetchone()
            if change == "closed":
                session._closed = True
            elif change == "subject":
                session._branch_snapshot["node_defs"][0]["prompt_template"] = "changed"
            elif change == "cancelled":
                from tinyassets.runs import request_cancel

                request_cancel(tmp_path, session.bound_run_id)
            elif change == "revoked":
                # C3 admits live connected custody, not a provider_work_binding.
                if manifest:
                    from tinyassets.providers.definition import get_definition
                    from tinyassets.storage.outbound_connections import ConnectionLedger

                    definition = get_definition(
                        session._universe_id,
                        carrier.provider.removeprefix("api_key_http:"),
                    )
                    assert definition is not None
                    ledger = ConnectionLedger(
                        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
                        verify_authenticated_principal=lambda: "acct_alice",
                    )
                    ledger.revoke_grant(definition.ref)
                else:
                    from tinyassets.credential_vault import write_credential_vault

                    write_credential_vault(
                        session._universe_dir, [], owner_user_id="acct_alice",
                        universe_id=session._universe_id,
                    )
            elif change in {"receipt", "claim"}:
                with sqlite3.connect(db_path(tmp_path)) as conn:
                    if change == "receipt":
                        assert carrier.binding_id is None
                        conn.execute(
                            "UPDATE provider_work_receipts SET state = 'revoked' "
                            "WHERE receipt_id = ?", (carrier.work_receipt_id,),
                        )
                    else:
                        conn.execute("DELETE FROM provider_work_execution_claims")
            elif change == "home":
                from tinyassets.daemon_server import set_founder_home

                set_founder_home(tmp_path, founder_sub="acct_alice", universe_id="other",
                                 platform_generated=True)
            elif change == "seal":
                object.__setattr__(carrier, "_seal", b"invalid")
            if change:
                with pytest.raises((PermissionError, ValueError)):
                    session._check_agent_authority(carrier)
            else:
                assert session._check_agent_authority(carrier) == "acct_alice"
                with pytest.raises(PermissionError, match="consumed"):
                    carrier.validate_for_call(role="writer", operation="run_graph")
            with sqlite3.connect(db_path(tmp_path)) as conn:
                assert conn.execute(
                    "SELECT COUNT(*) FROM provider_invocation_reservations",
                ).fetchone() == count
            observations.append(change)

    monkeypatch.setattr(_ForegroundRunProviderSession, "_authorize_attempt", authorize)
    branch = _branch(node_count=1)
    branch.node_defs[0].tools_allowed = ["universe_self"]
    result, _, _ = _run_branch(
        tmp_path, monkeypatch, authenticate_request, branch, open_provider=manifest,
        model_access=ModelAccess("discovered") if manifest else None,
    )
    assert observations == [change], result


@pytest.mark.parametrize("field", ["provider_request", "provider_invocation", "served_provider",
                                  "agent_model_plan", "model_selection"])
def test_foreground_call_rejects_injected_authority_before_admission(tmp_path, field):
    from dataclasses import replace

    session = _ForegroundRunProviderSession(
        tmp_path, universe_id="universe_alice", principal_id="acct_alice", provider_call=None,
    )
    context = replace(UniverseContext(universe_dir=tmp_path / "universe_alice", config=None),
                      **{field: object()})
    with pytest.raises(PermissionError, match="authority cannot be substituted"):
        session._call("writer", "prompt", "", ModelConfig(), None, {"universe_context": context})
    assert session._receipt is None and session._claim is None
