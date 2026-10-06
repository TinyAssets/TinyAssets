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
from tinyassets.storage.workspace_authority import connection_git_host, require_git_scope


def target(resource, verb, request):
    kind, repo = require_git_scope(verb)
    if verb not in resource.scopes or resource.connection_type != "http":
        raise PermissionError("git scope unavailable")
    if not isinstance(request, dict) or set(request) != {
            "host", "method", "target", "agent", "incarnation", "upload"}:
        raise PermissionError("invalid git request")
    host = connection_git_host(resource)
    if not host or request["host"] != host or not request["agent"]:
        raise PermissionError("git host or agent mismatch")
    service = "git-upload-pack" if kind == "git_read" else "git-receive-pack"
    discovery = f"/{repo}.git/info/refs?service={service}"
    rpc = f"/{repo}.git/{service}"
    if (request["method"], request["target"], request["upload"]) not in (
            ("GET", discovery, False), ("POST", rpc, True)):
        raise PermissionError("git method or repository mismatch")
    return f"https://{host}{request['target']}", service


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
    url, service = target(resource, verb, request)
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
    credential = broker._resolve_credential(resource.credential_ref, resource.connection_type)
    held = (credential,)
    scheme = resource.auth_scheme
    if scheme == "oauth2":
        from tinyassets.connection_oauth.tokens import decode

        original = decode(credential)
        bundle = broker._oauth_bundle(resource, grant_id, verb, credential)
        held = (*original.secret_values(), *bundle.secret_values())
        credential, scheme = bundle.access_token, "bearer"
    if scheme not in {"bearer", "basic"}:
        raise PermissionError("git requires bearer, basic or oauth2 authentication")
    bundle = oc._build_http_secret_bundle(scheme, credential)
    headers = oc._ssrf_auth_headers(scheme, bundle, method=request["method"], url=url)
    sensitive = (*held, *bundle.secret_values(), *oc._redirect_auth_material(headers))
    headers["Accept"] = f"application/x-{service}-{'result' if body else 'advertisement'}"
    if body is not None:
        headers["Content-Type"] = f"application/x-{service}-request"
    canonical = oc._parse_canonical_https_url(url, allowed_ports=frozenset({443}))
    addresses = oc._resolve_pinned_addresses(canonical.hostname, 443,
                                            resolver=oc._make_default_resolver(10),
                                            validator=oc._classify_global_address)
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
