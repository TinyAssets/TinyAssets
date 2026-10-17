"""RFC 7009 revocation from broker custody and trusted provider data."""
from __future__ import annotations

from urllib.parse import quote_plus

from tinyassets.connection_oauth import directory, tokens, transport


def revoke(credential: str) -> str:
    """Return a fixed failure code; provider text and credentials never escape."""
    try:
        bundle = tokens.decode(credential)
        if bundle.provider_id:
            row = directory.registered(bundle.provider_id, client_id=bundle.client_id,
                                       token_url=bundle.token_url)
        else:
            # Public clients use directory endpoint metadata without client auth.
            row = next((entry for entry in directory.entries()
                        if entry["token_endpoint"] == bundle.token_url), {})
        endpoint = row.get("revocation_endpoint")
        if not endpoint:
            return ""
        form = {"token": bundle.refresh_token or bundle.access_token,
                "token_type_hint": "refresh_token" if bundle.refresh_token else "access_token"}
        sensitive = bundle.secret_values()
        auth = {}
        if bundle.provider_id:
            secret = directory.secret(row["client_secret_env"])
            sensitive += (secret,)
            if row["token_endpoint_auth_method"] == "client_secret_basic":
                auth["basic_auth"] = (quote_plus(bundle.client_id), quote_plus(secret))
            else:
                form.update(client_id=bundle.client_id, client_secret=secret)
        status, _ = transport.request_json("POST", endpoint, form=form,
                                           secrets=sensitive, **auth)
        # RFC 7009 success may have an empty body, including an unknown token.
        return "" if status == 200 else "provider_revocation_failed"
    except Exception:  # noqa: BLE001 - even malformed custody must be erased
        return "provider_revocation_failed"
