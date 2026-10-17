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


def recovery_message(credential: str) -> str:
    """Public provider navigation, captured before erasure; never a retry token."""
    from urllib.parse import urlsplit

    name, url = "your provider", ""
    try:
        bundle = tokens.decode(credential)
        name = bundle.provider_id or urlsplit(bundle.token_url).hostname or name
        # The token endpoint was obtained from provider directory/discovery data.
        origin = urlsplit(bundle.token_url)
        url = f"{origin.scheme}://{origin.netloc}"
        row = next((entry for entry in directory.entries()
                    if entry["token_endpoint"] == bundle.token_url), {})
        name = row.get("display_name") or row.get("id") or name
        url = row.get("manage_url") or url
    except Exception:  # noqa: BLE001 - corrupt directory must not prevent erasure
        pass
    return ("Removed locally, but provider-side revocation failed. "
            "The token was erased, so TinyAssets cannot retry; revoke it at "
            + name + (" (" + url + ")" if url else "") + ".")


def removal_result(result):
    """One honest removal outcome for both the door and the box."""
    message = result.get("revocation_message", "")
    return {"status": "removed_locally_revoke_failed" if message else "removed",
            "connection_removed": result["removed"],
            "secrets_removed": result["secrets_removed"],
            **({"message": message, "recovery": "revoke_at_provider"} if message else {})}
