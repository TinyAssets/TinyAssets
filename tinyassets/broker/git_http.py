"""Smart HTTP authority and binary transport, used only inside the broker.

The caller selects a grant, never an authentication header. Exact wire targets
are derived from its current git scope. No redirects, helpers or git processes
run here. The existing pinned HTTPS driver and response scanner own the wire.
"""
from __future__ import annotations

import contextlib
import ssl
import time

from tinyassets.storage import outbound_connections as oc
from tinyassets.storage.workspace_authority import (
    connection_git_host,
    has_git_scope,
    require_git_scope,
)

GIT_REQUEST_FIELDS = frozenset({"host", "method", "target", "agent", "incarnation", "upload"})


def credential_endpoints(resource):
    """Express repository rights in the same credential endpoint rule as HTTP."""
    host = connection_git_host(resource)
    endpoints = []
    for scope in resource.scopes:
        if not scope.startswith(("git_read:", "git_write:")):
            continue
        kind, repo = require_git_scope(scope)
        service = "git-upload-pack" if kind == "git_read" else "git-receive-pack"
        endpoints.extend((
            oc.OutboundEndpoint(host, f"/{repo}.git/info/refs", ("GET",),
                                allowed_query=("service",), required_query=("service",),
                                query_patterns=(("service", service),)),
            oc.OutboundEndpoint(host, f"/{repo}.git/{service}", ("POST",)),
        ))
    return tuple(endpoints)


def git_target(resource, verb, request) -> tuple[str, str]:
    """The ``(kind, repo)`` an exact git request reaches, or PermissionError.

    The verb must be one of the connection's own git scopes, the host its git
    host, and the target exactly one of that verb's two endpoints: a git_read
    reaches only upload-pack, a git_write only receive-pack, of that one repo.
    """
    kind, repo = require_git_scope(verb)
    if not isinstance(request, dict) or set(request) != GIT_REQUEST_FIELDS:
        raise PermissionError("invalid git request")
    if not has_git_scope(resource, kind, repo):
        raise PermissionError("git verb is not one of the connection's scopes")
    if request["host"] != connection_git_host(resource):
        raise PermissionError("git host is not the connection's")
    service = "git-upload-pack" if kind == "git_read" else "git-receive-pack"
    if (request["method"], request["target"]) not in {
            ("GET", f"/{repo}.git/info/refs?service={service}"),
            ("POST", f"/{repo}.git/{service}")}:
        raise PermissionError("git target is not the verb's own endpoint")
    return kind, repo


class CheckedStream:
    """Recheck the grant even while response credit is exhausted."""

    def __init__(self, stream, check):
        self._stream, self.check_authority = stream, check
        self.status, self.reason = stream.status, stream.reason
        self.headers, self.redirect_count = stream.headers, 0

    def read(self, size):
        self.check_authority()
        chunk = self._stream.read(size)
        self.check_authority()
        return chunk

    def close(self):
        self._stream.close()


def dispatch(broker, grant_id, verb, request, *, body=None, guard=None,
             checkpoint=None, on_connect=None, deadline_at=None, **_ignored):
    initial = broker._ledger._active_resource_snapshot_for_grant(grant_id)
    if initial is None:
        raise oc.GrantResolutionError("git grant unavailable")
    resource, stamp = initial
    kind, _ = git_target(resource, verb, request)
    service = "git-upload-pack" if kind == "git_read" else "git-receive-pack"
    url = f"https://{request['host']}{request['target']}"
    canonical = oc._parse_canonical_https_url(url, allowed_ports=frozenset({443}))
    endpoint = oc._credential_endpoint(canonical, request["method"],
                                       credential_endpoints(resource))
    with broker._ledger._connect() as conn:
        row = conn.execute("SELECT incarnation FROM outbound_connections WHERE connection_id=?",
                           (resource.connection_id,)).fetchone()
    if row is None or row[0] != request["incarnation"]:
        raise oc.GrantResolutionError("git connection changed")
    deadline = deadline_at or time.monotonic() + 600

    def authority():
        current = broker._ledger._active_resource_snapshot_for_grant(grant_id, deadline=deadline)
        if current is None or current[1] != stamp:
            raise oc.GrantResolutionError("git authority changed")

    def check():
        if checkpoint:
            checkpoint()
        authority()

    if body is not None:
        body.check_authority = authority
    check()
    oc._validate_connection_credential_scheme(resource.connection_type, resource.credential_ref)
    credential = (broker._resolve_credential(resource.credential_ref, resource.connection_type)
                  if endpoint is not None else "")
    held = (credential,)
    scheme = resource.auth_scheme if credential else "none"
    if scheme == "oauth2":
        from tinyassets.connection_oauth.tokens import decode

        original = decode(credential)
        bundle = broker._oauth_bundle(resource, grant_id, verb, credential)
        held = (*original.secret_values(), *bundle.secret_values())
        credential, scheme = bundle.access_token, "bearer"
    if scheme not in {"bearer", "basic", "none"}:
        raise PermissionError("git requires bearer, basic or oauth2 authentication")
    bundle = oc._build_http_secret_bundle(scheme, credential)
    headers = oc._ssrf_auth_headers(scheme, bundle, method=request["method"], url=url)
    sensitive = (*held, *bundle.secret_values(), *oc._redirect_auth_material(headers))
    headers["Accept"] = f"application/x-{service}-{'result' if body else 'advertisement'}"
    if body is not None:
        headers["Content-Type"] = f"application/x-{service}-request"
    from tinyassets.broker.egress import destination, identity

    addresses = destination(identity.get(), canonical.hostname, canonical.port)
    check()
    with guard() if guard else contextlib.nullcontext():
        check()
        upstream = oc._open_pinned_https_stream(
            method=request["method"], canonical=canonical, pinned_address=addresses[0],
            headers=headers, body=body, ssl_context=ssl.create_default_context(),
            open_socket=oc._default_open_socket, timeout=15,
            max_total_seconds=max(0.001, deadline - time.monotonic()),
            max_body_bytes=2**63 - 1, max_header_count=100, max_header_bytes=65536,
            sensitive=sensitive, checkpoint=check, on_connect=on_connect)
    if upstream.status != 200:
        upstream.close()
        raise oc.ProxyRequestError(
            "git upstream refused; inspect remote refs before retrying a push")
    return CheckedStream(oc.BrokerStream(upstream, sensitive), authority)
