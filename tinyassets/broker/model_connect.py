"""Door-only authorization exchanges; no credential leaves the broker."""
from __future__ import annotations

import asyncio
from dataclasses import fields


def validate(document):
    from tinyassets.connection_oauth.discovery import validate_request
    from tinyassets.connection_oauth.pkce import VERIFIER_RE

    action = document.get("action")
    if action == "oauth_offer":
        if set(document) != {"action", "requested", "hosts"}:
            raise ValueError("invalid offer fields")
        validate_request(document["requested"])
        hosts = document["hosts"]
        if (not isinstance(hosts, list) or not 1 <= len(hosts) <= 8
                or any(not isinstance(h, str) or len(h) > 253 for h in hosts)):
            raise ValueError("invalid offer hosts")
        return
    required = {"action", "code", "verifier", "redirect_uri", "request_ref"}
    required |= ({"flow"} if action == "preset_exchange" else
                 {"offer", "client_id", "destination", "policy"})
    if action not in {"preset_exchange", "oauth_exchange"} or set(document) != required:
        raise ValueError("invalid exchange fields")
    for key in ("code", "verifier", "redirect_uri", "request_ref"):
        value = document[key]
        if (not isinstance(value, str) or len(value) > (512 if key == "request_ref" else 2048)
                or any(not 33 <= ord(c) <= 126 for c in value)):
            raise ValueError("invalid exchange value")
    if not document["code"] or not VERIFIER_RE.fullmatch(document["verifier"]):
        raise ValueError("invalid exchange code or verifier")
    if action == "preset_exchange":
        from tinyassets.onboarding.hosted_model_auth import PendingFlow

        if not isinstance(document["flow"], dict) or set(document["flow"]) != {
                f.name for f in fields(PendingFlow)}:
            raise ValueError("invalid preset flow")
    elif not isinstance(document["offer"], dict) or not isinstance(document["client_id"], str):
        raise ValueError("invalid OAuth offer")


def local_operation(ledger, *, principal, command_center, document, committed_via):
    from tinyassets.broker.http_connect import local_operation as connect
    from tinyassets.connection_oauth import directory, tokens
    from tinyassets.connection_oauth.transport import OAuthError
    from tinyassets.onboarding.hosted_model_auth import HostedAuthError, PendingFlow, load_preset

    validate(document)
    action = document["action"]
    if action == "oauth_offer":
        return {"offer": directory.resolve(document["requested"], document["hosts"])}
    if action == "preset_exchange":
        from tinyassets.onboarding.hosted_model_auth import endpoint_policy

        flow = PendingFlow(**document["flow"])
        if (flow.owner != principal or flow.universe_id != command_center
                or flow.callback_url != document["redirect_uri"]):
            raise ValueError("invalid preset binding")
        preset = load_preset(flow.preset_id)
        destination = "model:" + preset.id
        policy = dict(auth_scheme="bearer", scopes=["GET", "POST"],
                      endpoints=endpoint_policy(preset), access_mode="exact", git_host="")
    else:
        destination, policy = document["destination"], document["policy"]
        if policy.get("auth_scheme") != "oauth2":
            raise ValueError("OAuth requires OAuth custody")
    args = dict(principal=principal, command_center=command_center, committed_via=committed_via)
    operation = dict(action="prepare", destination=destination, policy=policy,
                     expected="", secret="", request_ref=document["request_ref"])
    prepared = connect(ledger, **args, document=operation)
    if "error" in prepared:
        return prepared
    try:
        if action == "preset_exchange":
            from tinyassets.broker.acquisition import exchange_key

            secret = asyncio.run(exchange_key(flow=flow, code=document["code"],
                                              verifier=document["verifier"]))
        else:
            offer = document["offer"]
            secret = tokens.encode(tokens.exchange_code(
                token_url=offer["token_url"], client_id=document["client_id"],
                code=document["code"], verifier=document["verifier"],
                redirect_uri=document["redirect_uri"], provider_id=offer.get("provider_id", ""),
                resource=offer.get("resource", ""), issuer=offer.get("issuer", ""),
                scope=" ".join(offer.get("scopes") or [])))
        if action == "preset_exchange":
            from tinyassets.onboarding import hosted_model_auth

            if hosted_model_auth.load_preset(flow.preset_id).digest != flow.preset_digest:
                return {"error": "model_connection_preset_changed"}
        return connect(ledger, **args, document={
            **operation, "action": "commit", "expected": prepared["revision"], "secret": secret})
    except (OAuthError, HostedAuthError) as exc:
        # Provider prose can echo credentials. Only our fixed code crosses the IPC.
        return {"error": exc.code}


def request(base, *, owner, center, document):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.broker.supervisor import get_supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    live = get_supervisor(base)
    if live is None:
        raise ProxyRequestError("the credential broker is not running")
    return BrokerClient(live.socket_path, principal=owner, command_center=center,
                        fence=live.fence, verify_peer=live.verify_broker,
                        timeout=60).http_connect(document)
