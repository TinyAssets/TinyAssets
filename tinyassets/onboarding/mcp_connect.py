"""Create the existing protected connect sheet without requiring a model."""


def offer(base, uid, owner, data):
    from tinyassets.api.http_connection import _DESTINATION_RE
    from tinyassets.api.pending_requests import request_from_user
    from tinyassets.broker.catalog import connections
    from tinyassets.onboarding.serving import _gesture_lock

    destination = data["destination"].strip().lower()
    if not _DESTINATION_RE.fullmatch(destination):
        return {"error": "invalid_account_label"}
    with _gesture_lock(uid):
        if any(view.destination == destination for _, view, _ in connections(
                base, principal=owner, command_center=uid)):
            return {"error": "account_label_in_use",
                    "detail": "Choose another account label, or disconnect the existing account."}
        return request_from_user(universe_id=uid, origin="platform", payload={
            "kind": "API", "title": "Connect MCP: " + destination,
            "body": "Connect this account and make its tools available in your command center.",
            "fields": [{"name": "secret", "type": "secret", "label": "API key"}],
            "action": {"type": "connect", "destination": destination,
                       "mcp_url": data["mcp_url"], "auth_scheme": data["auth_scheme"],
                       "mcp_auth_header": data["auth_header"], "uses": {"call": {}}},
        })
