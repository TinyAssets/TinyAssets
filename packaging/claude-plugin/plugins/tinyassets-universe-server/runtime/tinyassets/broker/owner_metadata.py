"""Owner-only display/consent facts; these reads grant no egress authority."""
from tinyassets.broker.ledger_queries import (
    OWNER_CONNECTION_NAMES,
    OWNER_CONNECTION_VIEW,
    query_ledger,
)
from tinyassets.storage.outbound_connections import (
    ConnectionView,
    ProxyRequestError,
    _parse_allowed_endpoints,
)


def view(data_root, *, principal, command_center, connection_id):
    answer = query_ledger(data_root, query=OWNER_CONNECTION_VIEW, principal=principal,
                          command_center=command_center, grant_id="owner-metadata",
                          connection_id=connection_id)
    try:
        if set(answer) != {"view"}:
            raise ValueError("invalid view")
        row = answer["view"]
        if row is None:
            return None
        result = ConnectionView(**(row | {"scopes": tuple(row["scopes"]),
            "allowed_endpoints": _parse_allowed_endpoints(row["allowed_endpoints"])}))
        if (result.owner_user_id != principal or result.connection_id != connection_id
                or result.revoked_at is not None):
            raise ValueError("invalid owner")
        return result
    except (LookupError, TypeError, ValueError):
        raise ProxyRequestError("invalid broker owner metadata") from None


def names(data_root, *, principal, command_center):
    cursor, result = "", set()
    while True:
        answer = query_ledger(data_root, query=OWNER_CONNECTION_NAMES, principal=principal,
                              command_center=command_center, grant_id="owner-metadata",
                              connection_id=cursor)
        try:
            if (set(answer) != {"items", "next_cursor"}
                    or not isinstance(answer["items"], list) or len(answer["items"]) > 64):
                raise ValueError("invalid page")
            for item in answer["items"]:
                if (set(item) != {"connection_id", "destination"}
                        or not isinstance(item["connection_id"], str)
                        or item["connection_id"] <= cursor
                        or not isinstance(item["destination"], str)):
                    raise ValueError("invalid item")
                cursor = item["connection_id"]
                result.add(item["destination"])
            if answer["next_cursor"] is None:
                return result
            if len(answer["items"]) != 64 or answer["next_cursor"] != cursor:
                raise ValueError("invalid cursor")
        except (LookupError, TypeError, ValueError):
            raise ProxyRequestError("invalid broker owner catalog") from None
