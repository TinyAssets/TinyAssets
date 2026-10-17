"""Broker-owned HTTP deposits and snapshot-bound connection updates."""
from __future__ import annotations

import hashlib
import json


def validate(document):
    from tinyassets.api.http_connection import _DESTINATION_RE, _MAX_ENDPOINTS, _MAX_SECRET_CHARS

    if isinstance(document, dict) and document.get("action") in {
            "oauth_offer", "oauth_exchange", "preset_exchange"}:
        from tinyassets.broker.model_connect import validate as validate_flow

        return validate_flow(document)
    if isinstance(document, dict) and document.get("action") in {"inspect_custody", "rotate"}:
        return _validate_custody(document)
    if isinstance(document, dict) and document.get("action") in {"extend", "full"}:
        return _validate_update(document)
    if not isinstance(document, dict) or set(document) - {"request_ref"} != {
            "action", "destination", "policy", "expected", "secret"}:
        raise ValueError("invalid HTTP connect fields")
    ref = document.get("request_ref", "")
    if not isinstance(ref, str) or len(ref) > 512 or any(ord(c) < 33 for c in ref):
        raise ValueError("invalid request reference")
    if not isinstance(document["secret"], str) or len(document["secret"]) > _MAX_SECRET_CHARS:
        raise ValueError("invalid HTTP credential")
    if document["action"] == "prepare" and document["secret"]:
        raise ValueError("preparation cannot contain a credential")
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
            or set(policy) != {"auth_scheme", "scopes", "endpoints", "access_mode", "git_host"}
            or any(not isinstance(policy[k], str)
                   for k in ("auth_scheme", "access_mode", "git_host"))
            or not isinstance(policy["scopes"], list)
            or any(not isinstance(v, str) for v in policy["scopes"])
            or not isinstance(policy["endpoints"], list)
            or not 1 <= len(policy["endpoints"]) <= _MAX_ENDPOINTS):
        raise ValueError("invalid HTTP connect operation")


def _validate_update(document):
    from tinyassets.api.http_connection import _DESTINATION_RE

    expected = document.get("expected")
    if (set(document) != {"action", "destination", "expected", "endpoints", "scopes",
                          "git_host"}
            or not isinstance(document["destination"], str)
            or not _DESTINATION_RE.fullmatch(document["destination"])
            or not isinstance(expected, dict)
            or set(expected) != {"endpoints_json", "scopes_json", "access_mode", "incarnation"}
            or any(not isinstance(v, str) or not v for v in expected.values())
            or not isinstance(document["git_host"], str)
            or not isinstance(document["endpoints"], list)
            or not isinstance(document["scopes"], list)
            or any(not isinstance(v, str) for v in document["scopes"])):
        raise ValueError("invalid HTTP policy update")
    if document["action"] == "full" and (document["endpoints"] or document["scopes"]):
        raise ValueError("full access changes only the mode")


def local_operation(ledger, *, principal, command_center, document, committed_via):
    if committed_via not in {"door", "box"}:
        raise ValueError("invalid commit transport")
    if committed_via == "box" and "request_ref" in document:
        raise PermissionError("request references require the door")
    if document.get("action") in {"oauth_offer", "oauth_exchange", "preset_exchange"}:
        from tinyassets.broker.model_connect import local_operation as flow_operation

        return flow_operation(ledger, principal=principal, command_center=command_center,
                              document=document, committed_via=committed_via)
    if document.get("action") in {"inspect_custody", "rotate"}:
        return _custody_local(ledger, principal=principal, command_center=command_center,
                              document=document, committed_via=committed_via)
    if document.get("action") in {"extend", "full"}:
        return _update_local(ledger, principal=principal, command_center=command_center,
                             document=document)
    from tinyassets.api.http_connection import _connect_plan, _ids, _project
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
        # Digest is a comparison value, not authority. Bind both rows and the
        # complete requested policy; no retained prepare state or replay queue.
        revision = hashlib.sha256(json.dumps(
            [principal, command_center, destination, policy,
             dict(row) if row else None, dict(grow) if grow else None],
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if document["action"] == "commit" and revision != document["expected"]:
            return {"error": "connection_conflict", "resource": "connection"}
        resource = ledger._get_connection_resource(connection_id, _transaction=conn)
        grant = ledger.get_grant(grant_id, _transaction=conn)
        raw_policy = (row["allowed_endpoints_json"], row["scopes_json"]) if row else None
        erased = bool(row and row["custody_erased"] and row["revoked_at"] is not None
                      and row["owner_user_id"] == principal and grow is None)
        if erased:
            resource, raw_policy = None, None
        plan = _connect_plan(
            resource=resource, raw_policy=raw_policy, existing_grant=grant,
            actor=principal, uid=command_center, destination=destination,
            connection_id=connection_id, grant_id=grant_id, scheme=scheme,
            credential_ref="vault://http/" + destination, git_host=host,
            requested_endpoints=policy["endpoints"], http_scopes=tuple(policy["scopes"]),
            access_mode=access)
        if "error" in plan:
            return plan
        if document["action"] == "prepare":
            return {"revision": revision}
        secret = document["secret"]
        from tinyassets.api.http_connection import _secret_shape_error
        error = ((_secret_shape_error(scheme, secret) if secret.strip() else "secret is required")
                 if scheme != "none" else "")
        if error:
            return {"error": "connection_setup_invalid", "detail": error}
        scopes = plan["http_scopes"]
        if erased:
            conn.execute("DELETE FROM outbound_connections WHERE connection_id=?", (connection_id,))
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
                universe_id=command_center,
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
        if scheme != "none":
            failure = _deposit(ledger, command_center, principal, destination, secret)
            if failure:
                conn.rollback()
                return failure
        resource = ledger._get_connection_resource(connection_id, _transaction=conn)
        conn.execute("UPDATE outbound_connections SET request_ref=?, committed_via=? "
                     "WHERE connection_id=?",
                     (document.get("request_ref", ""), committed_via, connection_id))
        return {"projection": {**_project(resource, grant),
                               "request_ref": document.get("request_ref", "")}}


def connect_operation(data_root, *, principal, command_center, destination, policy, action,
                      expected="", secret="", request_ref=""):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.broker.supervisor import get_supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    document = dict(action=action, destination=destination, policy=policy,
                    expected=expected, secret=secret)
    if request_ref:
        document["request_ref"] = request_ref
    validate(document)
    supervisor = get_supervisor(data_root)
    if supervisor is None:
        raise ProxyRequestError("the credential broker is not running")
    client = BrokerClient(supervisor.socket_path, principal=principal,
                          command_center=command_center, fence=supervisor.fence,
                          verify_peer=supervisor.verify_broker, timeout=30)
    result = client.http_connect(document)
    if "error" in result:
        if result.get("error") not in {"connection_conflict", "deposit_failed",
                "credential_ownership_transfer_unsupported", "connection_setup_invalid"}:
            raise ProxyRequestError("invalid broker connect refusal")
    elif action == "prepare":
        if (set(result) != {"revision"} or not isinstance(result["revision"], str)
                or len(result["revision"]) != 64):
            raise ProxyRequestError("invalid broker connect preparation")
    elif set(result) != {"projection"} or not isinstance(result["projection"], dict):
        raise ProxyRequestError("invalid broker connect response")
    return result


def _update_local(ledger, *, principal, command_center, document):
    """Apply an owner-approved extension against its exact broker snapshot."""
    from tinyassets.api.http_connection import _canonical_endpoint_set, _ids
    from tinyassets.broker.ledger_queries import AUTHORIZED_CONNECTION, validate_query
    from tinyassets.storage.outbound_connections import GrantResolutionError

    validate(document)
    connection_id, grant_id = _ids(universe_id=command_center, destination=document["destination"])
    validate_query(AUTHORIZED_CONNECTION, principal, command_center, grant_id, connection_id)
    expected = document["expected"]
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT c.* FROM outbound_connections c JOIN outbound_connection_grants g "
            "ON g.connection_id=c.connection_id WHERE c.connection_id=? AND g.grant_id=? "
            "AND c.owner_user_id=? AND g.owner_user_id=? AND g.universe_id=? "
            "AND c.revoked_at IS NULL AND g.revoked_at IS NULL",
            (connection_id, grant_id, principal, principal, command_center)).fetchone()
        if (row is None or row["connection_type"] != "http" or row["provider"] != "http"
                or row["connection_class"] != "http"
                or row["destination"] != document["destination"]
                or row["credential_ref"] != "vault://http/" + document["destination"]):
            raise GrantResolutionError("outbound connection identity mismatch")
        actual = {"endpoints_json": row["allowed_endpoints_json"],
                  "scopes_json": row["scopes_json"], "access_mode": row["access_mode"],
                  "incarnation": row["incarnation"]}
        if expected != actual or document["git_host"] != row["git_host"]:
            return {"updated": False}
        if document["action"] == "full":
            updated = ledger.set_access_mode(
                connection_id=connection_id, access_mode="full",
                expected_mode=expected["access_mode"],
                expected_endpoints_json=expected["endpoints_json"],
                expected_scopes_json=expected["scopes_json"],
                expected_incarnation=expected["incarnation"], _transaction=conn)
        else:
            if (not _canonical_endpoint_set(json.loads(expected["endpoints_json"]))
                    <= _canonical_endpoint_set(document["endpoints"])
                    or not set(json.loads(expected["scopes_json"])) <= set(document["scopes"])):
                raise ValueError("HTTP policy extension cannot narrow")
            updated = ledger.extend_http_connection_endpoints(
                connection_id=connection_id, endpoints=document["endpoints"],
                scopes=tuple(document["scopes"]), git_host=document["git_host"],
                expected_endpoints_json=expected["endpoints_json"],
                expected_scopes_json=expected["scopes_json"],
                expected_access_mode=expected["access_mode"],
                expected_incarnation=expected["incarnation"], expected_grant_id=grant_id,
                _transaction=conn)
        return {"updated": updated}


def update_operation(data_root, *, principal, command_center, destination, expected,
                     action, endpoints=(), scopes=(), git_host=""):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.broker.supervisor import get_supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    document = dict(action=action, destination=destination, expected=expected,
                    endpoints=list(endpoints), scopes=list(scopes), git_host=git_host)
    validate(document)
    supervisor = get_supervisor(data_root)
    if supervisor is None:
        raise ProxyRequestError("the credential broker is not running")
    client = BrokerClient(supervisor.socket_path, principal=principal,
                          command_center=command_center, fence=supervisor.fence,
                          verify_peer=supervisor.verify_broker, timeout=30)
    result = client.http_connect(document)
    if set(result) != {"updated"} or type(result["updated"]) is not bool:
        raise ProxyRequestError("invalid broker policy response")
    return result["updated"]


def _deposit(ledger, center, principal, destination, secret):
    from tinyassets.broker.vault import write_credential_vault
    from tinyassets.credential_vault import http_credential_record

    try:
        write_credential_vault(ledger._data_root / center,
            [http_credential_record(destination=destination, token=secret)],
            owner_user_id=principal, universe_id=center)
    except PermissionError:
        return {"error": "credential_ownership_transfer_unsupported",
                "detail": "this destination's credential is owned by another principal"}
    except Exception:
        return {"error": "deposit_failed", "resource": "connection"}
    return None


def _validate_custody(document):
    ref = document.get("request_ref", "")
    if not isinstance(ref, str) or len(ref) > 512 or any(ord(c) < 33 for c in ref):
        raise ValueError("invalid request reference")
    from tinyassets.api.http_connection import _DESTINATION_RE, _MAX_SECRET_CHARS

    if (set(document) - {"request_ref"} != {"action", "destination", "incarnation", "secret"}
            or not isinstance(document["destination"], str)
            or not _DESTINATION_RE.fullmatch(document["destination"])
            or not isinstance(document["incarnation"], str)
            or not isinstance(document["secret"], str)
            or len(document["secret"]) > _MAX_SECRET_CHARS
            or (document["action"] == "inspect_custody" and document["secret"])
            or (document["action"] == "rotate" and not document["secret"].strip())):
        raise ValueError("invalid custody operation")


def _custody_local(ledger, *, principal, command_center, document, committed_via):
    from tinyassets.api.http_connection import _ids, _secret_shape_error, _unpasteable_scheme
    from tinyassets.credential_vault import http_deposit_refusal
    from tinyassets.storage.outbound_connections import GrantResolutionError

    validate(document)
    connection, grant = _ids(universe_id=command_center, destination=document["destination"])
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT c.* FROM outbound_connections c "
            "JOIN outbound_connection_grants g ON g.connection_id=c.connection_id "
            "WHERE c.connection_id=? AND g.grant_id=? AND c.owner_user_id=? "
            "AND g.owner_user_id=? AND g.universe_id=? AND c.revoked_at IS NULL "
            "AND g.revoked_at IS NULL", (connection, grant, principal, principal,
                                        command_center)).fetchone()
        if (row is None or row["connection_type"] != "http"
                or row["connection_class"] != "http" or row["provider"] != "http"
                or row["destination"] != document["destination"]
                or row["credential_ref"] != "vault://http/" + document["destination"]):
            raise GrantResolutionError("connection unavailable")
        refusal = http_deposit_refusal(ledger._data_root / command_center,
            destination=document["destination"], owner_user_id=principal)
        if document["action"] == "inspect_custody":
            return {"refusal": refusal}
        if row["incarnation"] != document["incarnation"]:
            return {"error": "connection_changed", "resource": "connection"}
        error = _unpasteable_scheme(row["auth_scheme"])
        if error:
            return error
        error = _secret_shape_error(row["auth_scheme"], document["secret"])
        if error:
            return {"error": "connection_setup_invalid", "detail": error}
        failure = _deposit(ledger, command_center, principal, document["destination"],
                           document["secret"])
        if failure:
            return failure
        conn.execute("UPDATE outbound_connections SET request_ref=?, committed_via=? "
                     "WHERE connection_id=?",
                     (document.get("request_ref", ""), committed_via, connection))
        return {"rotated": True}


def custody_operation(data_root, *, principal, command_center, destination,
                      incarnation="", secret="", action="inspect_custody", request_ref=""):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.broker.supervisor import get_supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    supervisor = get_supervisor(data_root)
    if supervisor is None:
        raise ProxyRequestError("the credential broker is not running")
    document = dict(action=action, destination=destination, incarnation=incarnation, secret=secret)
    if request_ref:
        document["request_ref"] = request_ref
    validate(document)
    return BrokerClient(supervisor.socket_path, principal=principal,
        command_center=command_center, fence=supervisor.fence,
        verify_peer=supervisor.verify_broker, timeout=30).http_connect(document)
