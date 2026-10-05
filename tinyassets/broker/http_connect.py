"""Prepare/commit HTTP deposits; credential bytes stay in the daemon's vault."""
from __future__ import annotations

import hashlib
import json


def validate(document):
    from tinyassets.api.http_connection import _DESTINATION_RE, _MAX_ENDPOINTS

    if not isinstance(document, dict) or set(document) != {
            "action", "destination", "policy", "expected"}:
        raise ValueError("invalid HTTP connect fields")
    policy = document["policy"]
    if (document["action"] not in {"prepare", "commit"}
            or not isinstance(document["destination"], str)
            or not _DESTINATION_RE.fullmatch(document["destination"])
            or not isinstance(document["expected"], str)
            or (document["action"] == "prepare" and document["expected"])
            or (document["action"] == "commit" and (
                len(document["expected"]) != 64
                or any(c not in "0123456789abcdef" for c in document["expected"])))
            or not isinstance(policy, dict)
            or set(policy) - {"mcp_draft"} != {
                "auth_scheme", "scopes", "endpoints", "access_mode", "git_host"}
            or any(not isinstance(policy[k], str)
                   for k in ("auth_scheme", "access_mode", "git_host"))
            or not isinstance(policy["scopes"], list)
            or any(not isinstance(v, str) for v in policy["scopes"])
            or not isinstance(policy["endpoints"], list)
            or not 1 <= len(policy["endpoints"]) <= _MAX_ENDPOINTS):
        raise ValueError("invalid HTTP connect operation")
    if "mcp_draft" in policy:
        from tinyassets.mcp_attachment import Attachment

        draft = Attachment.parse(policy["mcp_draft"])
        if (draft.state != "draft" or draft.revision != 1 or not draft.activation_request_id
                or draft.tools or draft.catalog_hash or draft.protocol_version
                or bool(draft.auth_header) != (policy["auth_scheme"] == "header")):
            raise ValueError("invalid MCP deposit draft")


def local_operation(ledger, *, principal, command_center, document):
    from tinyassets.api.http_connection import _HTTP_ACTION_CAP, _connect_plan, _ids, _project
    from tinyassets.broker.ledger_queries import AUTHORIZED_CONNECTION, validate_query
    from tinyassets.storage.outbound_connections import (
        _SUPPORTED_HTTP_AUTH_SCHEMES,
        _parse_allowed_endpoints,
        normalize_access_mode,
        validate_url_secret_binding,
    )
    from tinyassets.storage.workspace_authority import normalize_git_host, validate_git_scopes

    validate(document)
    destination, policy = document["destination"], document["policy"]
    connection_id, grant_id = _ids(universe_id=command_center, destination=destination)
    validate_query(AUTHORIZED_CONNECTION, principal, command_center, grant_id, connection_id)
    endpoints = _parse_allowed_endpoints(policy["endpoints"])
    access = normalize_access_mode(policy["access_mode"])
    host = normalize_git_host(policy["git_host"])
    scheme = policy["auth_scheme"]
    if scheme not in _SUPPORTED_HTTP_AUTH_SCHEMES:
        raise ValueError("unsupported HTTP auth scheme")
    validate_url_secret_binding(scheme, endpoints, access_mode=access)
    validate_git_scopes(policy["scopes"], hosts=[ep.host for ep in endpoints], git_host=host)
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM outbound_connections WHERE connection_id=?",
                           (connection_id,)).fetchone()
        grow = conn.execute("SELECT * FROM outbound_connection_grants WHERE grant_id=?",
                            (grant_id,)).fetchone()
        attachment = None
        draft = policy.get("mcp_draft")
        if draft is not None and row is not None:
            attachment = conn.execute(
                "SELECT descriptor_json FROM mcp_attachments WHERE owner_id=? "
                "AND connection_id=? AND incarnation=?",
                (principal, connection_id, row["incarnation"])).fetchone()
            prior = json.loads(attachment[0]) if attachment else None
            if (prior is None or prior.get("state") in {"revoked", "expired"}
                    or any(prior.get(key, "") != draft.get(key, "") for key in
                           ("activation_request_id", "endpoint", "auth_header"))):
                return {"error": "account_label_in_use"}
        # Digest is a comparison value, not authority. Bind both rows and the
        # complete requested policy; no retained prepare state or replay queue.
        revision = hashlib.sha256(json.dumps(
            [principal, command_center, destination, policy,
             dict(row) if row else None, dict(grow) if grow else None,
             attachment[0] if attachment else None],
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if document["action"] == "commit" and revision != document["expected"]:
            return {"error": "connection_conflict", "resource": "connection"}
        resource = ledger._get_connection_resource(connection_id, _transaction=conn)
        grant = ledger.get_grant(grant_id, _transaction=conn)
        raw_policy = (row["allowed_endpoints_json"], row["scopes_json"]) if row else None
        plan = _connect_plan(
            resource=resource, raw_policy=raw_policy, existing_grant=grant,
            actor=principal, uid=command_center, destination=destination,
            connection_id=connection_id, grant_id=grant_id, scheme=scheme,
            credential_ref="vault://http/" + destination, git_host=host,
            requested_endpoints=policy["endpoints"], http_scopes=tuple(policy["scopes"]))
        if "error" in plan:
            return plan
        if document["action"] == "prepare":
            return {"revision": revision}
        scopes = plan["http_scopes"]
        if resource is None:
            ledger.create_connection(
                connection_id=connection_id, owner_user_id=principal, connection_class="http",
                connection_type="http", auth_scheme=scheme, scopes=scopes, provider="http",
                destination=destination, credential_ref="vault://http/" + destination,
                allowed_endpoints=policy["endpoints"], access_mode=access, git_host=host,
                _transaction=conn)
        elif access == "full" and resource.access_mode == "exact":
            if not ledger.set_access_mode(
                    connection_id=connection_id, access_mode="full", expected_mode="exact",
                    expected_endpoints_json=raw_policy[0], expected_scopes_json=raw_policy[1],
                    expected_incarnation=row["incarnation"], _transaction=conn):
                raise RuntimeError("connection policy changed")
        if grant is None:
            grant = ledger.grant_connection(
                grant_id=grant_id, connection_id=connection_id, owner_user_id=principal,
                universe_id=command_center, unprompted_action_cap=_HTTP_ACTION_CAP,
                _transaction=conn)
        if plan["legacy_scope_upgrade"]:
            ledger._upgrade_http_connection_scopes(
                connection_id=connection_id, scopes=scopes, _transaction=conn)
        if plan["endpoints_extend"]:
            current = conn.execute("SELECT * FROM outbound_connections WHERE connection_id=?",
                                   (connection_id,)).fetchone()
            if not ledger.extend_http_connection_endpoints(
                    connection_id=connection_id, endpoints=policy["endpoints"], scopes=scopes,
                    expected_endpoints_json=current["allowed_endpoints_json"],
                    expected_scopes_json=current["scopes_json"],
                    expected_access_mode=current["access_mode"],
                    expected_incarnation=current["incarnation"], expected_grant_id=grant_id,
                    git_host=host, _transaction=conn):
                raise RuntimeError("connection policy changed")
        resource = ledger._get_connection_resource(connection_id, _transaction=conn)
        if draft is not None and attachment is None:
            from tinyassets.storage.outbound_connections import (
                _enforce_endpoint_allowlist,
                _parse_canonical_https_url,
            )

            _enforce_endpoint_allowlist(
                _parse_canonical_https_url(draft["endpoint"], allowed_ports=frozenset({443})),
                "POST", resource.allowed_endpoints, resource.access_mode)
            incarnation = conn.execute(
                "SELECT incarnation FROM outbound_connections WHERE connection_id=?",
                (connection_id,)).fetchone()[0]
            conn.execute("INSERT INTO mcp_attachments VALUES (?,?,?,?)",
                         (principal, connection_id, incarnation, json.dumps(draft, sort_keys=True)))
        return {"projection": _project(resource, grant)}


def connect_operation(data_root, *, principal, command_center, destination, policy, action,
                      expected=""):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.broker.supervisor import get_supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    document = dict(action=action, destination=destination, policy=policy, expected=expected)
    validate(document)
    supervisor = get_supervisor(data_root)
    if supervisor is None:
        raise ProxyRequestError("credential broker is selected but not running")
    client = BrokerClient(supervisor.socket_path, principal=principal,
                          command_center=command_center, fence=supervisor.fence,
                          verify_peer=supervisor.verify_broker, timeout=30)
    result = client.http_connect(document)
    if "error" in result:
        if result not in ({"error": "connection_conflict", "resource": "connection"},
                          {"error": "account_label_in_use"},
                          {"error": "connection_conflict", "resource": "grant"}):
            raise ProxyRequestError("invalid broker connect refusal")
    elif action == "prepare":
        if (set(result) != {"revision"} or not isinstance(result["revision"], str)
                or len(result["revision"]) != 64):
            raise ProxyRequestError("invalid broker connect preparation")
    elif set(result) != {"projection"} or not isinstance(result["projection"], dict):
        raise ProxyRequestError("invalid broker connect response")
    return result
