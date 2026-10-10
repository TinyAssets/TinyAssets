"""Closed daemon authority interface for injected connection consumers.

Construction is a trusted daemon operation. Definitions and packets never
provide the principal verifier or the admitted command-center scope.
"""
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tinyassets.broker.ledger_queries import authorized_connection, granted_resource_row


@dataclass(frozen=True)
class BrokerConnectionAuthority:
    data_root: Path
    command_center: str
    verify_authenticated_principal: Callable[[], str]

    def snapshot(self, grant_id):
        principal = self.verify_authenticated_principal()
        if not isinstance(principal, str) or not principal.strip() or not self.command_center:
            raise PermissionError("connection authority requires admitted scope")
        row = granted_resource_row(self.data_root, principal=principal,
                                   command_center=self.command_center, grant_id=grant_id)
        grant, resource, _ = authorized_connection(
            self.data_root, principal=principal, command_center=self.command_center,
            grant_id=grant_id, connection_id=row["connection_id"])
        return principal, grant, resource.to_view()


#: The only connection authority. A daemon-side ``ConnectionLedger`` is not one:
#: the ledger lives in the broker's tree and only the broker opens it.
ConnectionAuthority = BrokerConnectionAuthority


def require_connection_authority(authority):
    if type(authority) is not BrokerConnectionAuthority:
        raise ValueError("connection_ledger must use canonical connection authority")


def read_authority(authority: ConnectionAuthority, grant_id: str, *, universe_dir=None):
    require_connection_authority(authority)
    if universe_dir is not None and Path(universe_dir).resolve() != (
            Path(authority.data_root).resolve() / authority.command_center):
        raise PermissionError("effect directory differs from admitted broker scope")
    return authority.snapshot(grant_id)
