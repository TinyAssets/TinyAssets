"""`_connect_plan` decides exactly what main's inline connect checks decided.

Isolation L3 (risk R6) moved `_connect_http`'s existing-policy conflict checks
into `_connect_plan` so the broker's prepare/commit can share them. The
original block is kept below, verbatim, as the executable specification; the
matrix walks every field it reads, alone and in combination with the scope and
endpoint shapes it branches on.
"""
import itertools
import json
from types import SimpleNamespace

from tinyassets.api.http_connection import (
    _canonical_endpoint_set,
    _canonical_policy,
    _connect_plan,
    _git_scopes_in,
)

ACTOR, UID, DESTINATION = "alice", "cc-alice", "example-api"
CONNECTION_ID, GRANT_ID = "conn-example", "grant-example"
SCHEME, CREDENTIAL_REF, GIT_HOST = "bearer", "vault://http/example-api", ""


def _main_inline_checks(*, resource, raw_policy, get_grant, actor, uid, destination,
                        connection_id, grant_id, scheme, credential_ref, git_host,
                        requested_endpoints, http_scopes):
    """origin/main `_connect_http` step 4: its code verbatim, comments dropped and
    the grant read injected, returning the plan instead of assigning locals."""
    legacy_scope_upgrade = False
    endpoints_extend = False
    if resource is not None and raw_policy is not None:
        http_scopes = tuple(
            sorted(set(http_scopes) | _git_scopes_in(raw_policy[1]))
        )
        non_scope_mismatch = (
            resource.owner_user_id != actor
            or resource.connection_type != "http"
            or resource.connection_class != "http"
            or resource.provider != "http"
            or resource.auth_scheme != scheme
            or resource.destination != destination
            or resource.credential_ref != credential_ref
            or resource.revoked_at is not None
            or resource.git_host != git_host
            or _canonical_policy([e.as_dict() for e in resource.allowed_endpoints])
            != _canonical_policy(requested_endpoints)
        )
        stored_endpoints = [e.as_dict() for e in resource.allowed_endpoints]
        endpoints_extend = (
            _canonical_endpoint_set(requested_endpoints)
            > _canonical_endpoint_set(stored_endpoints)
        )
        non_scope_mismatch = non_scope_mismatch and not (
            endpoints_extend
            and _canonical_policy(stored_endpoints)
            != _canonical_policy(requested_endpoints)
            and resource.owner_user_id == actor
            and resource.connection_type == "http"
            and resource.connection_class == "http"
            and resource.provider == "http"
            and resource.auth_scheme == scheme
            and resource.destination == destination
            and resource.credential_ref == credential_ref
            and resource.revoked_at is None
            and resource.git_host == git_host
        )
        scopes_match = tuple(resource.scopes) == http_scopes
        legacy_scope_upgrade = (
            not non_scope_mismatch
            and not scopes_match
            and tuple(resource.scopes) == ("http",)
        )
        if non_scope_mismatch or (
            not scopes_match and not legacy_scope_upgrade and not endpoints_extend
        ):
            return {"error": "connection_conflict", "resource": "connection"}
    existing_grant = get_grant(grant_id)
    if existing_grant is not None and (
        existing_grant.connection_id != connection_id
        or existing_grant.owner_user_id != actor
        or existing_grant.universe_id != uid
        or existing_grant.revoked_at is not None
    ):
        return {"error": "connection_conflict", "resource": "grant"}
    return {"http_scopes": http_scopes, "legacy_scope_upgrade": legacy_scope_upgrade,
            "endpoints_extend": endpoints_extend}


def _endpoint(path, methods):
    return {"host": "api.example.com", "path_template": path, "methods": list(methods)}


GET_A = _endpoint("/v1/a", ["GET"])
POST_B = _endpoint("/v1/b", ["POST"])
GET_POST_A = _endpoint("/v1/a", ["POST", "GET"])
ENDPOINT_SHAPES = {
    "same": [GET_A],
    "reordered_methods": [_endpoint("/v1/a", ["GET"])],
    "superset": [GET_A, POST_B],
    "different": [POST_B],
    "wider_methods": [GET_POST_A],
    "duplicate": [GET_A, GET_A],
}
SCOPE_SHAPES = [("GET",), ("http",), ("GET", "POST"), ("POST",), ()]
STORED_SCOPES_JSON = ['["GET"]', '["GET", "git_read:owner/repo"]', "not json", "[]"]
RESOURCE_FIELDS = {
    "owner_user_id": "bob", "connection_type": "mcp", "connection_class": "mcp",
    "provider": "other", "auth_scheme": "basic", "destination": "elsewhere",
    "credential_ref": "vault://http/elsewhere", "revoked_at": 1.0,
    "git_host": "git.example.com",
}
GRANT_FIELDS = {"connection_id": "conn-other", "owner_user_id": "bob",
                "universe_id": "cc-bob", "revoked_at": 1.0}


def _resource(scopes, stored_endpoints, **changes):
    fields = dict(owner_user_id=ACTOR, connection_type="http", connection_class="http",
                  provider="http", auth_scheme=SCHEME, destination=DESTINATION,
                  credential_ref=CREDENTIAL_REF, revoked_at=None, git_host=GIT_HOST,
                  scopes=tuple(scopes),
                  allowed_endpoints=[SimpleNamespace(as_dict=lambda e=e: dict(e))
                                     for e in stored_endpoints])
    return SimpleNamespace(**(fields | changes))


def _grants():
    yield None
    match = dict(connection_id=CONNECTION_ID, owner_user_id=ACTOR, universe_id=UID,
                 revoked_at=None)
    yield SimpleNamespace(**match)
    for field, value in GRANT_FIELDS.items():
        yield SimpleNamespace(**(match | {field: value}))


def _resources():
    yield None, None
    for scopes, stored_json, field in itertools.product(
            SCOPE_SHAPES, STORED_SCOPES_JSON, [None, *RESOURCE_FIELDS]):
        changes = {} if field is None else {field: RESOURCE_FIELDS[field]}
        resource = _resource(scopes, [GET_A], **changes)
        yield resource, (json.dumps([GET_A]), stored_json)
    # A stored row whose policy text is gone is treated as no prior policy.
    yield _resource(("GET",), [GET_A], owner_user_id="bob"), None


def test_connect_plan_matches_main_inline_checks_across_the_matrix():
    cases = 0
    outcomes = set()
    for (resource, raw_policy), grant, (shape, requested), http_scopes in itertools.product(
            list(_resources()), list(_grants()), ENDPOINT_SHAPES.items(),
            [("GET",), ("GET", "POST")]):
        common = dict(resource=resource, raw_policy=raw_policy, actor=ACTOR, uid=UID,
                      destination=DESTINATION, connection_id=CONNECTION_ID,
                      grant_id=GRANT_ID, scheme=SCHEME, credential_ref=CREDENTIAL_REF,
                      git_host=GIT_HOST, requested_endpoints=requested,
                      http_scopes=http_scopes)
        expected = _main_inline_checks(get_grant=lambda _id, g=grant: g, **common)
        actual = _connect_plan(existing_grant=grant, **common)
        assert actual == expected, (shape, http_scopes, resource, raw_policy, grant)
        outcomes.add(json.dumps(expected, sort_keys=True, default=list))
        cases += 1
    # The matrix must reach every branch, not just agree on one of them.
    assert cases > 10_000
    assert {"connection", "grant"} <= {
        json.loads(o).get("resource") for o in outcomes}
    plans = [json.loads(o) for o in outcomes if "error" not in json.loads(o)]
    assert any(p["legacy_scope_upgrade"] for p in plans)
    assert any(p["endpoints_extend"] for p in plans)
    assert any("git_read:owner/repo" in p["http_scopes"] for p in plans)
