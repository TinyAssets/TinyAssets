"""Closed daemon authority interface for injected connection consumers.

Construction is a trusted daemon operation. Definitions and packets never
provide the principal verifier or the admitted command-center scope.
"""
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tinyassets.broker.ledger_queries import authorized_connection, granted_resource_row
from tinyassets.broker.supervisor import broker_selected
from tinyassets.storage.outbound_connections import ConnectionLedger, ProxyRequestError


@dataclass(frozen=True)
class BrokerConnectionAuthority:
    data_root: Path
    command_center: str
    verify_authenticated_principal: Callable[[], str]

    def snapshot(self, grant_id):
        if not broker_selected():
            raise ProxyRequestError("broker connection authority requires selected broker")
        principal = self.verify_authenticated_principal()
        if not isinstance(principal, str) or not principal.strip() or not self.command_center:
            raise PermissionError("connection authority requires admitted scope")
        row = granted_resource_row(self.data_root, principal=principal,
                                   command_center=self.command_center, grant_id=grant_id)
        grant, resource, _ = authorized_connection(
            self.data_root, principal=principal, command_center=self.command_center,
            grant_id=grant_id, connection_id=row["connection_id"])
        return principal, grant, resource.to_view()


ConnectionAuthority = ConnectionLedger | BrokerConnectionAuthority


def require_connection_authority(authority):
    if type(authority) is BrokerConnectionAuthority:
        return
    if not broker_selected() and isinstance(authority, ConnectionLedger):
        return
    raise ValueError("connection_ledger must use canonical connection authority")


def read_authority(authority: ConnectionAuthority, grant_id: str, *, universe_dir=None):
    require_connection_authority(authority)
    if type(authority) is BrokerConnectionAuthority:
        if universe_dir is not None and Path(universe_dir).resolve() != (
                Path(authority.data_root).resolve() / authority.command_center):
            raise PermissionError("effect directory differs from admitted broker scope")
        return authority.snapshot(grant_id)
    principal = authority.require_authenticated_principal_id()
    grant = authority.require_active_grant(grant_id)
    return principal, grant, authority.get_connection(grant.connection_id)
