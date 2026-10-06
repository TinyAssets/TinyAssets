#!/usr/bin/env python3
"""Observe verified AuthKit client/family claims without emitting credentials.

Feed JSON on stdin with initial, refreshed and reauthorized access tokens from
the SAME owner/client. Never pass tokens on a command line or save them in the
repo. --issuer must be the trusted configured AuthKit issuer, not token input.
Exit 0 means a generation candidate was observed, NOT that cutover is safe or
that the operator's refresh/interactive-exchange labels have been proven.
Exit 1 means no usable candidate; exit 2 means invalid/unavailable evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tinyassets.auth.workos_provider import WorkOSAuthProvider, derive_endpoints

_STAGES = ("initial", "refreshed", "reauthorized")


def _client(claims: dict) -> str | None:
    """Only explicit verified client claims; never infer identity from aud/sid."""
    client = claims.get("client_id")
    if not isinstance(client, str) or not client or client != client.strip():
        return None
    # azp alone is deliberately unsupported until tenant evidence proves it.
    if "azp" in claims and claims["azp"] != client:
        return None
    return client


def observe(provider: WorkOSAuthProvider, samples: dict) -> tuple[int, dict]:
    """Return a fixed-field, secret-free observation, never an authorization."""
    invalid = (2, {"status": "invalid_evidence"})
    if not isinstance(samples, dict) or set(samples) != set(_STAGES):
        return invalid
    tokens = [samples[stage] for stage in _STAGES]
    if any(not isinstance(token, str) or not token or len(token) > 65536 for token in tokens):
        return invalid
    if len(set(tokens)) != 3:
        return invalid
    claims = [provider.verified_claims(token) for token in tokens]
    if any(item is None for item in claims):
        return invalid
    clients = [_client(item) for item in claims]
    if any(client is None for client in clients):
        return 2, {"status": "client_identity_unavailable_or_conflicting"}
    if len(set(clients)) != 1 or len({(item["iss"], item["sub"]) for item in claims}) != 1:
        return 2, {"status": "owner_or_client_mismatch"}

    times = [item.get("auth_time") for item in claims]
    time_candidate = (
        all(type(value) is int and value >= 0 for value in times)
        and all(type(item.get("iat")) is int and value <= item["iat"]
                for item, value in zip(claims, times, strict=True))
        and times[0] == times[1] < times[2]
    )
    sessions = [item.get("sid") for item in claims]
    session_candidate = (
        all(isinstance(value, str) and value and value == value.strip() for value in sessions)
        and sessions[0] == sessions[1] != sessions[2]
    )
    candidate = bool(time_candidate or session_candidate)
    return (0 if candidate else 1), {
        "status": "candidate_observed" if candidate else "protected_family_binding_required",
        "same_verified_owner_and_client": True,
        "auth_time_stable_on_refresh_and_increases_on_reauth": bool(time_candidate),
        "sid_stable_on_refresh_and_changes_on_reauth": bool(session_candidate),
        "exchange_provenance": "operator_supplied_not_verified_by_probe",
        "registered_metadata": "not_observed",
        "cutover_ready": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issuer", required=True)
    args = parser.parse_args(argv)
    origin = urlsplit(args.issuer)
    if (origin.scheme != "https" or not origin.hostname or origin.username
            or origin.password or origin.path not in ("", "/") or origin.query or origin.fragment):
        print('{"status":"invalid_configured_issuer"}')
        return 2
    try:
        issuer, jwks = derive_endpoints(args.issuer)
        provider = WorkOSAuthProvider(
            issuer=issuer, jwks_uri=jwks, audience="https://tinyassets.io/mcp",
        )
        # Bound input and report only a fixed error category, even on parse errors.
        raw = sys.stdin.read(200001)
        if len(raw) > 200000:
            raise ValueError("input too large")
        code, receipt = observe(provider, json.loads(raw))
    except Exception:
        # Library/network exception text can include tokens; never print it.
        print('{"status":"evidence_unavailable"}')
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
