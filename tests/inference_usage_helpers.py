"""Account synthetic proxy sends with the real private-reference consumer.

These fixtures replace only the remote transport. Production ScopedConnectionProxy
instances retain their real broker/worker path, exercised in separate composition
and authorization tests. No provider adapter gains a test bypass.
"""
from pathlib import Path

from tinyassets.storage.agent_request_usage import UsageStore
from tinyassets.storage.outbound_connections import ScopedConnectionProxy


def accounting_resolver(resolve):
    def wrapped(*args, **identity):
        proxy = resolve(*args, **identity)
        if isinstance(proxy, ScopedConnectionProxy):
            return proxy
        return _AccountedProxy(proxy, identity)
    return wrapped


def _broker_store(db_path):
    """The store as the broker builds it: claiming a reference is broker-only."""
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger_path = Path(db_path).resolve()
    base = ledger_path.parent.parent if ledger_path.parent.name == ".broker" else (
        ledger_path.parent)
    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base)
    return UsageStore(base, broker_ledger=ledger)


class _AccountedProxy:
    def __init__(self, delegate, identity):
        self.delegate, self.identity = delegate, identity

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def request(self, verb, request, *, inference_usage=None):
        if inference_usage is not None:
            identity = self.identity
            dispatch = _broker_store(identity["db_path"]).claim_reference(
                inference_usage.reference, owner=identity["owner_user_id"],
                universe=identity["universe_id"], usage_id=inference_usage.usage_id,
                grant_id=identity["grant_id"], connection_id=identity["connection_id"],
                verb=verb, request=request, operation_id=inference_usage.operation_id,
            )
            dispatch.dispatched()
        return self.delegate.request(verb, request)


def broker_accounting_resolver(resolve):
    """Use the real grant ledger and broker; inject only its remote response IO."""
    from tinyassets.storage.outbound_connections import ConnectionLedger, CredentialBlindBroker

    def wrapped(*args, **identity):
        delegate = resolve(*args, **identity)
        ledger = ConnectionLedger(identity["db_path"],
                                  verify_authenticated_principal=lambda: identity["owner_user_id"])

        def network(**kwargs):
            kwargs["checkpoint"]()
            kwargs["on_connect"](None)
            return delegate.request(kwargs["verb"], kwargs["request"])

        broker = CredentialBlindBroker(ledger, resolve_credential=lambda *_: "synthetic",
                                       network_request=network)

        class Channel:
            def request(self, verb, request, *, inference_usage=None):
                return broker.dispatch(
                    identity["grant_id"], verb, request,
                    **({"inference_usage": inference_usage.document(),
                        "operation_id": inference_usage.operation_id}
                       if inference_usage is not None else {}),
                )

            def close(self):
                delegate.close()

        resource = ledger._active_resource_for_grant(identity["grant_id"])
        return ScopedConnectionProxy(
            grant_id=identity["grant_id"], provider=resource.provider,
            destination=resource.destination, scopes=resource.scopes,
            access_mode=resource.access_mode, _channel=Channel(),
        )

    return wrapped
