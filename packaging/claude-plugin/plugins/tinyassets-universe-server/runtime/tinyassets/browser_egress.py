"""Browser relay: public HTTPS only, one pinned hop, no ambient proxy or logs."""
from __future__ import annotations

import base64
import time
from urllib.parse import urlsplit

MAX_BODY = 4 * 1024 * 1024


def origin(url):
    if not isinstance(url, str) or len(url) > 8192:
        raise ValueError('invalid browser URL')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in (None, 443)
            or any(ord(c) < 33 for c in url) or '\\' in url):
        raise ValueError('browser requires public HTTPS')
    return 'https://' + parsed.hostname.lower()


def fetch(document):
    from tinyassets.storage.outbound_connections import (
        _classify_global_address,
        _default_open_socket,
        _default_ssl_context,
        _make_default_resolver,
        _PinnedHTTPSConnection,
        _resolve_pinned_addresses,
    )

    origin(document['url'])
    url = urlsplit(document['url'])
    addresses = _resolve_pinned_addresses(
        url.hostname, 443, resolver=_make_default_resolver(10),
        validator=_classify_global_address)
    body = base64.b64decode(document.get('body', ''), validate=True)
    if len(body) > MAX_BODY or document['method'] not in {
            'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'}:
        raise ValueError('browser request exceeds bounds')
    headers = document['headers']
    if (not isinstance(headers, dict) or len(headers) > 100
            or sum(len(k) + len(v) for k, v in headers.items()) > 65536):
        raise ValueError('browser headers exceed bounds')
    headers = {k: v for k, v in headers.items() if k.lower() not in {
        'host', 'connection', 'content-length', 'transfer-encoding', 'accept-encoding',
        'proxy-authorization', 'proxy-connection'}}
    connection = _PinnedHTTPSConnection(
        url.hostname, pinned_address=addresses[0], open_socket=_default_open_socket,
        context=_default_ssl_context(), timeout=15, deadline=time.monotonic() + 30)
    try:
        connection.request(document['method'], (url.path or '/') + (
            '?' + url.query if url.query else ''), body=body or None, headers=headers)
        response = connection.getresponse()
        data = response.read(MAX_BODY + 1)
        raw_headers = response.getheaders()
        if (len(data) > MAX_BODY or len(raw_headers) > 100
                or sum(len(k) + len(v) for k, v in raw_headers) > 65536):
            raise ValueError('browser response exceeds bounds')
        result_headers = {}
        for key, value in raw_headers:
            key = key.lower()
            if key not in {'transfer-encoding', 'connection', 'content-length'}:
                result_headers[key] = result_headers.get(key, '') + (
                    '\n' if key in result_headers else '') + value
        return dict(status=response.status, headers=result_headers,
                    body=base64.b64encode(data).decode())
    finally:
        connection.close()
