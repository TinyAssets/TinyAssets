"""Address-based model source discovery: detect which standards a provider uses.

Only an address comes in. :func:`detect` makes GET-only, unauthenticated
probes for published standards and records what each one says; :func:`choose`
picks the connect combination from that record alone. Nothing here knows a
provider by name or host: a provider is connectable exactly when the standards
it publishes add up to a sign-in and a model API.

Sign-in: RFC 9728 protected-resource metadata (well-known or a 401's
``WWW-Authenticate``), RFC 8414 / OpenID discovery, RFC 7591 registration,
Client ID Metadata Documents, RFC 8628 device authorization, PKCE methods and
token endpoint auth methods. Model API: a chat-completions catalogue (``GET
/models`` listing ``data[].id``) and its sibling ``/chat/completions`` and
``/responses`` routes, an OpenAPI document's paths and security schemes, and
the key header style the provider advertises.

GET-only discovery cannot prove what an API behind a blanket 401 serves. Such
a host is offered by API key, unverified: confirmation reads the catalogue
with the owner's key, and nothing becomes a model source if that read fails.
"""
from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

from tinyassets.connection_oauth.transport import OAuthError, validate_https_url

#: A full model catalogue or OpenAPI document is far larger than OAuth metadata.
LARGE_BODY_BYTES = 8 * 1024 * 1024
#: Statuses a GET to a POST-only route returns when the route exists.
_ROUTE_PRESENT = frozenset({400, 401, 403, 405, 415, 422})
_CONTROL_PATH = "/.tinyassets-discovery-control"
_MAX_ISSUERS = 2


def address_url(address):
    if not isinstance(address, str):
        raise OAuthError("invalid_oauth_url")
    address = address.strip()
    parts = urlsplit(validate_https_url(address if "://" in address else "https://" + address))
    return urlunsplit((parts.scheme, parts.netloc.lower().removesuffix(":443"),
                      parts.path.rstrip("/"), "", ""))


def catalogue_rows(payload):
    rows = payload.get("data") if isinstance(payload, dict) else None
    if (not isinstance(rows, list) or not rows or len(rows) > 10000
            or any(not isinstance(row, dict) or not isinstance(row.get("id"), str)
                   or not row["id"] or len(row["id"]) > 200
                   or not row["id"].isprintable() or row["id"] != row["id"].strip()
                   for row in rows)
            or len({row["id"] for row in rows}) != len(rows)):
        raise OAuthError("no_model_api_found")
    return rows


def transport_fetch(url, *, large=False, timeout=15):
    """The product fetch: the SSRF-hardened transport, GET only, no redirects."""
    from tinyassets.connection_oauth.transport import request_json_with_headers

    return request_json_with_headers(
        "GET", url, timeout=timeout,
        **({"max_body_bytes": LARGE_BODY_BYTES} if large else {}))


def _get(fetch, record, url, *, large=False):
    try:
        status, doc, headers = fetch(validate_https_url(url), large=large)
    except OAuthError as exc:
        if exc.code == "discovery_timeout":
            raise
        record["probes"].append({"url": url, "error": exc.code})
        return None, None, {}
    record["probes"].append({"url": url, "status": status})
    return status, doc, headers


def _strings(value):
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _challenge(headers):
    from tinyassets.connection_oauth.discovery import bearer_challenge

    header = headers.get("www-authenticate", "")
    if not header:
        return {}
    scheme = header.strip().split(" ", 1)[0].split(",", 1)[0].lower()
    try:
        params = bearer_challenge(header)
    except OAuthError:
        params = {}
    return {"scheme": scheme, **{k: params[k] for k in ("resource_metadata", "scope")
                                 if k in params}}


def _bases(address):
    parts = urlsplit(address)
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    path = parts.path.rstrip("/").removesuffix("/models")
    bases = [origin + path] if path else []
    return origin, list(dict.fromkeys([*bases, origin + "/v1", origin]))


def _openapi(fetch, record, origin, bases):
    for url in dict.fromkeys([origin + "/openapi.json", *(b + "/openapi.json" for b in bases)]):
        status, doc, _headers = _get(fetch, record, url, large=True)
        if status != 200 or not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict):
            continue
        servers = [s["url"].rstrip("/") for s in doc.get("servers") or []
                   if isinstance(s, dict) and isinstance(s.get("url"), str)
                   and s["url"].startswith("https://")]
        schemes = (doc.get("components") or {}).get("securitySchemes") or {}
        keys, oauth = [], []
        for scheme in schemes.values() if isinstance(schemes, dict) else ():
            if not isinstance(scheme, dict):
                continue
            kind = scheme.get("type")
            if kind == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
                keys.append("bearer")
            elif kind == "apiKey" and isinstance(scheme.get("name"), str):
                keys.append(f"{scheme.get('in')}:{scheme['name']}")
            elif kind in {"oauth2", "openIdConnect"}:
                oauth.append(kind)
        paths = list(doc["paths"])
        return {"url": url, "servers": servers[:4], "key_styles": sorted(set(keys)),
                "oauth_schemes": sorted(set(oauth)),
                "models": any(p.rstrip("/").endswith("/models") for p in paths),
                "chat_completions": any(p.endswith("/chat/completions") for p in paths),
                "responses": any(p.endswith("/responses") for p in paths)}
    return None


def _authorization_server(fetch, record, issuer):
    from tinyassets.connection_oauth.discovery import _metadata_urls

    for url in _metadata_urls(issuer):
        status, doc, _headers = _get(fetch, record, url)
        if status != 200 or not isinstance(doc, dict):
            continue
        if doc.get("issuer") != issuer:
            return {"issuer": issuer, "metadata_url": url, "error": "issuer_mismatch"}
        return {
            "issuer": issuer, "metadata_url": url,
            "kind": "openid" if "openid-configuration" in url else "rfc8414",
            "authorization_endpoint": doc.get("authorization_endpoint") or "",
            "token_endpoint": doc.get("token_endpoint") or "",
            "registration_endpoint": doc.get("registration_endpoint") or "",
            "iss_parameter_supported": doc.get(
                "authorization_response_iss_parameter_supported") is True,
            "dynamic_registration": bool(doc.get("registration_endpoint")),
            "client_id_metadata_document": doc.get(
                "client_id_metadata_document_supported") is True,
            "device_authorization": bool(doc.get("device_authorization_endpoint")),
            "pkce_methods": _strings(doc.get("code_challenge_methods_supported")),
            "token_auth_methods": _strings(doc.get("token_endpoint_auth_methods_supported")),
            "grant_types": _strings(doc.get("grant_types_supported"))
            or ["authorization_code", "implicit"],
        }
    return None


def _protected_resource(fetch, record, resource, challenge):
    parts = urlsplit(resource)
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    urls = []
    if challenge.get("resource_metadata", "").startswith("https://"):
        urls.append((challenge["resource_metadata"], "www-authenticate"))
    well_known = origin + "/.well-known/oauth-protected-resource"
    urls += [(well_known + parts.path, "well-known"), (well_known, "well-known")]
    for url, via in dict.fromkeys(urls):
        status, doc, _headers = _get(fetch, record, url)
        if status != 200 or not isinstance(doc, dict):
            continue
        named = doc.get("resource")
        if not isinstance(named, str) or not (
                named in {resource, origin} or resource.startswith(named.rstrip("/") + "/")):
            return {"url": url, "via": via, "error": "protected_resource_mismatch"}
        found = {"url": url, "via": via, "resource": named,
                 "authorization_servers": _strings(doc.get("authorization_servers"))[:_MAX_ISSUERS],
                 "bearer_methods": _strings(doc.get("bearer_methods_supported"))}
        for key in ("models_endpoint", "chat_completions_endpoint"):
            if isinstance(doc.get(key), str):
                found[key] = doc[key]
        return found
    return None


#: Whole-discovery budget; each probe also has its own timeout (below 60s total).
DETECT_BUDGET_S = 35


def detect(address, *, fetch=transport_fetch):
    """Probe ``address`` for published standards; return the capability record."""
    address = address_url(address)
    origin, bases = _bases(address)
    seen, network = {}, fetch
    import time

    deadline = time.monotonic() + DETECT_BUDGET_S

    def fetch(url, *, large=False):
        if url not in seen:
            if time.monotonic() >= deadline:
                raise OAuthError("discovery_timeout")
            options = {"timeout": min(15, deadline - time.monotonic())} if (
                network is transport_fetch) else {}
            seen[url] = network(url, large=large, **options)
            if time.monotonic() >= deadline:
                raise OAuthError("discovery_timeout")
        return seen[url]
    record = {"address": address, "probes": [], "model_api": {}, "sign_in": {}}
    api = record["model_api"]
    catalogue = None
    for base in bases:
        status, doc, headers = _get(fetch, record, base + "/models", large=True)
        if status == 200:
            try:
                rows = catalogue_rows(doc)
            except OAuthError:
                continue
            catalogue = {"base": base, "public": True, "count": len(rows),
                         "list_shape": doc.get("object") == "list"
                         or all(row.get("object") == "model" for row in rows)}
            break
        if status in (401, 403):
            control, _doc, _h = _get(fetch, record, base + _CONTROL_PATH)
            if control != status:
                catalogue = {"base": base, "public": False, "challenge": _challenge(headers)}
                break
            api.setdefault("blanket_auth", status)
            api.setdefault("challenge", _challenge(headers))
    api["catalogue"] = catalogue
    openapi = _openapi(fetch, record, origin, bases)
    api["openapi"] = openapi
    base = catalogue["base"] if catalogue else None
    if base is None and openapi and openapi["models"] and openapi["servers"]:
        base = openapi["servers"][0]
    for route, key in (("/chat/completions", "chat_completions"), ("/responses", "responses")):
        for root in dict.fromkeys([base, *bases] if base else bases[:1]):
            control, _doc, _h = _get(fetch, record, root + _CONTROL_PATH)
            status, _doc, headers = _get(fetch, record, root + route)
            if status in (401, 403) and not catalogue_challenge(api):
                api["challenge"] = _challenge(headers)
            if status in _ROUTE_PRESENT and status != control:
                api[key] = {"url": root + route, "present": True, "status": status}
                break
        else:
            api[key] = {"url": (base or bases[0]) + route,
                        "present": bool(openapi and openapi[key])}
    challenge = catalogue_challenge(api) or api.get("challenge") or {}
    styles = list(openapi["key_styles"]) if openapi else []
    if challenge.get("scheme") == "bearer" and "bearer" not in styles:
        styles.append("bearer")
    api["key_styles"] = styles
    resource = base or address
    prm = _protected_resource(fetch, record, resource, challenge)
    record["sign_in"]["protected_resource"] = prm
    servers = []
    for issuer in (prm or {}).get("authorization_servers", []):
        try:
            found = _authorization_server(fetch, record, validate_https_url(issuer))
        except OAuthError as exc:
            if exc.code == "discovery_timeout":
                raise
            found = {"issuer": issuer, "error": "invalid_oauth_url"}
        if found:
            servers.append(found)
    record["sign_in"]["authorization_servers"] = servers
    record["chosen"] = choose(record)
    return record


def catalogue_challenge(api):
    return (api.get("catalogue") or {}).get("challenge") or {}


def _site(url):
    """Registrable site, including private suffixes such as shared hosting domains.

    Without the system suffix database, only an exact host can be trusted.
    """
    from tinyassets.connection_oauth.site_identity import site

    return site(url)


def choose(record):
    """The connect combination, from the capability record alone."""
    api, sign_in = record["model_api"], record["sign_in"]
    prm = sign_in.get("protected_resource") or {}
    models = inference = None
    if prm.get("models_endpoint") and prm.get("chat_completions_endpoint"):
        models, inference = prm["models_endpoint"], prm["chat_completions_endpoint"]
    chat = api.get("chat_completions") or {}
    verified = True
    if models is None and api.get("catalogue"):
        # The wire convention: chat completions is the catalogue's sibling.
        models = api["catalogue"]["base"] + "/models"
        inference = chat["url"] if chat.get("present") else (
            api["catalogue"]["base"] + "/chat/completions")
    elif models is None and api.get("blanket_auth") == 401 and chat.get("url"):
        inference = chat["url"]
        models = inference.removesuffix("/chat/completions") + "/models"
        verified = False
    if models is None:
        return {"verdict": "not_connectable", "reason": (
            "every path refuses unauthenticated requests (403)" if api.get("blanket_auth")
            else "no chat-completions model catalogue or advertised model endpoint")}
    chosen = {"models_url": models, "inference_url": inference, "wire": "chat_completions",
              "verified": verified}
    usable = [s for s in sign_in.get("authorization_servers", [])
              if not s.get("error") and "authorization_code" in s["grant_types"]
              and "S256" in s["pkce_methods"]
              and (s["dynamic_registration"] or s["client_id_metadata_document"])
              and _site(s["issuer"]) == _site(models) == _site(inference)
              and all(_site(s[k]) == _site(models) for k in (
                  "authorization_endpoint", "token_endpoint", "registration_endpoint") if s[k])]
    if usable:
        server = usable[0]
        return {**chosen, "verdict": "oauth", "sign_in": "authorization_code_pkce",
                "issuer": server["issuer"],
                "client": "dynamic_registration" if server["dynamic_registration"]
                else "client_id_metadata_document"}
    styles = api.get("key_styles") or []
    if styles and "bearer" not in styles:
        return {"verdict": "not_connectable", **chosen,
                "reason": "API key goes in " + ", ".join(styles)
                + "; model calls send a bearer key"}
    reasons = []
    for server in sign_in.get("authorization_servers", []):
        if server.get("error"):
            reasons.append(server["issuer"] + ": " + server["error"])
        elif server["device_authorization"] and not server["dynamic_registration"]:
            reasons.append("device authorization needs a registered client")
        else:
            reasons.append("no PKCE S256 authorization code with self-registration")
    if not sign_in.get("authorization_servers"):
        reasons.append("no OAuth metadata (RFC 9728 / RFC 8414 / OpenID)")
    if not verified:
        reasons.append("every path needs a key; your key is checked against the catalogue")
    return {**chosen, "verdict": "api_key", "sign_in": "api_key",
            "key_style": "bearer", "reason": "; ".join(reasons)}


def discover(address, *, fetch=transport_fetch):
    """Produce the same source record as an editable declaration."""
    from tinyassets.providers.source_record import SourceRecord, resolve

    declared = resolve(address)
    if declared is not None:
        return declared
    evidence = detect(address, fetch=fetch)
    chosen = evidence["chosen"]
    if chosen["verdict"] == "not_connectable":
        raise OAuthError("no_model_api_found", chosen["reason"])
    found = definition(chosen["models_url"], chosen["inference_url"], evidence["address"])
    http = {"base_url": chosen["inference_url"].removesuffix("/chat/completions"),
            "models_url": chosen["models_url"], "inference_url": chosen["inference_url"],
            "wire": "chat_completions"}
    sign_in = {"api_key": {"header_style": "bearer", "help_url": evidence["address"]}}
    hosts = {ep["host"] for ep in found["endpoints"]}
    if chosen["verdict"] == "oauth":
        server = next(s for s in evidence["sign_in"]["authorization_servers"]
                      if s["issuer"] == chosen["issuer"])
        oauth = {k: server[k] for k in ("issuer", "authorization_endpoint", "token_endpoint",
                                       "registration_endpoint", "iss_parameter_supported")}
        oauth.update(pkce="S256", client_strategy=("dcr" if server["dynamic_registration"]
                     else "metadata_document"), resource=(evidence["sign_in"].get(
                         "protected_resource") or {}).get("resource", ""))
        hosts.update(urlsplit(oauth[k]).hostname for k in (
            "issuer", "authorization_endpoint", "token_endpoint", "registration_endpoint")
                     if oauth[k])
        sign_in = {"oauth": oauth}
    return SourceRecord.parse(dict(id=found["destination"],
        display_name=urlsplit(evidence["address"]).hostname, origin="detected",
        addresses=[evidence["address"]], sign_in=sign_in, invoke={"http": http},
        egress={"provider": sorted(hosts)}, state={"location": "broker"}))


def connection_definition(record):
    http = record.invoke["http"]
    return definition(http["models_url"], http["inference_url"], record.addresses[0])


def oauth_offer(record, callback):
    """Adapt frozen source metadata to the existing mechanism-only PKCE engine."""
    from tinyassets.onboarding.source_connect import CLIENT_METADATA_PATH

    oauth = record.sign_in["oauth"]
    strategy = oauth["client_strategy"]
    parts = urlsplit(callback)
    client = (parts.scheme + "://" + parts.netloc + CLIENT_METADATA_PATH
              if strategy == "metadata_document" else oauth.get("client_id", ""))
    return {"issuer": oauth["issuer"], "authorize_url": oauth["authorization_endpoint"],
            "token_url": oauth["token_endpoint"], "client_id": client,
            "registration_url": oauth.get("registration_endpoint", ""),
            "registration_method": strategy, "scopes": oauth.get("scopes", []),
            "iss_parameter_supported": oauth.get("iss_parameter_supported", False),
            "resource": oauth.get("resource", ""), "source": record.origin}


def definition(models_url, inference_url, address):
    from tinyassets.storage.outbound_connections import _parse_allowed_endpoints

    endpoints = []
    for url, method in ((models_url, "GET"), (inference_url, "POST")):
        parts = urlsplit(validate_https_url(url))
        if any(c in parts.path for c in "*{}"):
            raise OAuthError("oauth_endpoint_not_permitted")
        endpoints.append({"host": parts.hostname, "path_template": parts.path,
                          "methods": [method]})
    # Parse now: rejects wildcards, private names and template authority.
    selected = [ep.as_dict() for ep in _parse_allowed_endpoints(endpoints)]
    if len({ep["host"] for ep in selected}) != 1:
        raise OAuthError("model_api_hosts_unsupported")
    return {"destination": "model-" + hashlib.sha256(inference_url.encode()).hexdigest()[:24],
            "endpoints": selected, "discovery_hosts": [address],
            "models_url": models_url, "inference_url": inference_url}


def source(endpoints, destination):
    """Recognize a generic compatible pair from the granted exact endpoint policy."""
    gets = [ep for ep in endpoints if ep.get("methods") == ["GET"]]
    posts = [ep for ep in endpoints if ep.get("methods") == ["POST"]]
    if len(endpoints) != 2 or len(gets) != 1 or len(posts) != 1:
        return None
    get, post = gets[0], posts[0]
    inference = "https://" + post["host"] + post["path_template"]
    if destination != "model-" + hashlib.sha256(inference.encode()).hexdigest()[:24]:
        return None
    return {"name": post["host"], "catalogue_url": "https://" + get["host"]
            + get["path_template"], "wire": "chat_messages",
            "discovered": True, "offer": "Use the models this account offers.",
            "billing_note": "Calls follow your provider's billing settings. "
                            "TinyAssets cannot measure or enforce this source's charges. "
                            "Confirm only if your account has no per-call charges "
                            "or paid usage is disabled."}
