"""Refresh a deposited subscription document in custody before launch."""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tinyassets.credential_refresh import (
    RefreshError,
    RefreshRejected,
    RefreshUnavailable,
    refresh_credential,
)

#: Refresh when the access token is within this long of expiry. Matches the
#: ``oauth2`` connection skew so one owner's two credential kinds do not drift.
REFRESH_SKEW_SECONDS = 60.0

#: Refresh when the stored document's own ``last_refresh`` is older than this.
#: The CLI refreshes on its own schedule when it can; the platform must get
#: there first, so this is deliberately shorter than any CLI threshold observed
#: (the Codex CLI's is 28 days). A document refreshed within the window is left
#: alone, so an ordinary turn does no network work.
REFRESH_AGE_SECONDS = 12 * 3600.0

logger = logging.getLogger(__name__)

_MAX_DOCUMENT_BYTES = 128 * 1024
_TIMEOUT = 20.0

#: Token-endpoint error codes and phrases that mean the STORED refresh token is
#: finished: no retry of it helps and only the owner signing in again produces a
#: new one. Everything else is transient.
_TERMINAL_CODES = frozenset({"invalid_grant", "invalid_client", "unauthorized_client"})
_TERMINAL_PHRASES = (
    "already been used",
    "already used",
    "sign in again",
    "log in again",
    "reauthenticate",
    "re-authenticate",
)


@dataclass(frozen=True)
class _Document:
    """One stored subscription document, parsed. Never rendered anywhere."""

    text: str
    access_token: str
    refresh_token: str
    id_token: str
    last_refresh: str
    expires_at: float | None

    def encoded(self) -> str:
        return base64.b64encode(self.text.encode("utf-8")).decode("ascii")


def _claim_expiry(token: str) -> float | None:
    """``exp`` from an unverified JWT payload, or None for anything else.

    Unverified on purpose: this is a freshness HINT about our own stored token,
    never an authorization decision. A token that is not a JWT (an opaque
    string) simply has no readable expiry, which is a supported answer.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        value = claims.get("exp")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)
    except Exception:  # noqa: BLE001 - an unreadable token has no expiry, never raises
        return None


_UNREADABLE = "the stored authorization is unreadable; reconnect"


def _parse(encoded: str) -> _Document:
    """Parse the stored base64 document. Raises :class:`RefreshError` on any
    shape this module will not spend a refresh token against.

    Every raise happens OUTSIDE the handler that decided it. ``from None`` clears
    ``__cause__`` but LEAVES ``__context__``, and a ``JSONDecodeError``'s ``doc``
    attribute is the entire credential document -- so a raise inside the handler
    hands every token in it to any traceback, log or error collector that walks
    the context chain (Codex refute-review, P1 #6; the same reason
    ``credential_vault.load_credential_vault`` raises after its handler exits).
    """
    document: dict[str, Any] | None = None
    text = ""
    try:
        raw = base64.b64decode(encoded.strip(), validate=True)
        if len(raw) <= _MAX_DOCUMENT_BYTES:
            text = raw.decode("utf-8")
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                document = parsed
    except (binascii.Error, UnicodeDecodeError, ValueError):
        document = None
    if document is None or not isinstance(document.get("tokens"), dict):
        raise RefreshError(_UNREADABLE)
    tokens = document["tokens"]
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token")
    if not isinstance(access, str) or not isinstance(refresh, str):
        raise RefreshError(_UNREADABLE)
    identity = tokens.get("id_token")
    stamp = document.get("last_refresh")
    return _Document(
        text=text,
        access_token=access,
        refresh_token=refresh,
        id_token=identity if isinstance(identity, str) else "",
        last_refresh=stamp if isinstance(stamp, str) else "",
        expires_at=_claim_expiry(access),
    )


def _stamp_age(last_refresh: str, now: float) -> float | None:
    """How long ago ``last_refresh`` was, or None when it cannot be read."""
    text = (last_refresh or "").strip()
    if not text:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return now - parsed.timestamp()


def document_is_stale(document: _Document, now: float) -> bool:
    """Whether this stored document must be replaced before a launch.

    True when the access token is within the skew of expiry, or when the
    document's own refresh stamp is older than the platform's threshold. A
    document with NEITHER a readable expiry NOR a readable stamp is not stale:
    see this module's docstring on why a guess is not made here.
    """
    if document.expires_at is not None and now >= document.expires_at - REFRESH_SKEW_SECONDS:
        return True
    age = _stamp_age(document.last_refresh, now)
    return age is not None and age >= REFRESH_AGE_SECONDS


def _terminal(status: int, body: Any) -> bool:
    """Whether the endpoint's refusal is about the stored refresh token itself.

    Only a 4xx can be, and only when its own words say so: a 400 with no code is
    as likely to be a transport or proxy artefact, and treating that as a dead
    credential would ask the owner to sign in again over a blip.
    """
    if not 400 <= status < 500:
        return False
    code = ""
    description = ""
    if isinstance(body, dict):
        code = str(body.get("error") or "").strip().lower()
        description = str(body.get("error_description") or "").strip().lower()
    if code in _TERMINAL_CODES:
        return True
    return any(phrase in description for phrase in _TERMINAL_PHRASES)


def _spend(document: _Document, *, token_url: str, client_id: str) -> _Document:
    """RFC 6749 section 6 against the source's token endpoint, one attempt.

    Sent through the SSRF-hardened broker transport
    (:func:`tinyassets.connection_oauth.transport.request_json`), the same one the
    ``oauth2`` connection refresh uses -- not a plain client. The endpoint is
    derived from an UNSIGNED identity token, so the request carries a refresh
    token to a URL this process did not choose: HTTPS validation, the SSRF driver,
    the body cap and the secret scrubbing are exactly the protections that makes
    safe. A plain client here would have been a token-exfiltration path (Codex
    refute-review, P1 #1).

    A source that rotates returns a new refresh token; one that does not leaves
    the old one valid, so it is kept. The response is read field by field --
    never merged wholesale into the stored document, which would let the
    endpoint add keys to a document a subprocess is launched with.
    """
    from tinyassets.connection_oauth.transport import (
        OAuthError,
        request_json,
        validate_https_url,
    )

    secrets = tuple(
        value for value in (document.refresh_token, document.access_token, document.id_token)
        if value
    )
    try:
        validate_https_url(token_url)
        status, body = request_json("POST", token_url, form={
            "grant_type": "refresh_token",
            "refresh_token": document.refresh_token,
            "client_id": client_id,
        }, secrets=secrets)
    except ValueError:
        raise RefreshUnavailable("the sign-in endpoint is not usable") from None
    except OAuthError:
        # The transport's own failure (unreachable, refused host, oversized body).
        # Its detail is scrubbed but describes OUR request, so it is not echoed.
        raise RefreshUnavailable("the sign-in service could not be reached") from None
    if status >= 400:
        if _terminal(status, body):
            raise RefreshRejected(
                "the stored sign-in is no longer accepted; sign in again")
        raise RefreshUnavailable(f"the sign-in service answered {status}")
    if not isinstance(body, dict):
        raise RefreshUnavailable("the sign-in service answered in an unreadable shape")
    access = body.get("access_token")
    if not isinstance(access, str) or not access:
        raise RefreshUnavailable("the sign-in service returned no usable authorization")
    rotated = body.get("refresh_token")
    identity = body.get("id_token")
    return _rebuild(
        document,
        access_token=access,
        refresh_token=rotated if isinstance(rotated, str) and rotated else document.refresh_token,
        id_token=identity if isinstance(identity, str) and identity else document.id_token,
    )


def _rebuild(
    document: _Document, *, access_token: str, refresh_token: str, id_token: str,
) -> _Document:
    """The stored document with its tokens replaced and its stamp moved forward.

    Every other key the owner's sign-in wrote (auth mode, account id, and
    anything a newer CLI added) is preserved: the launch copy must stay the
    document the CLI reads, not a subset this module happens to know about.
    """
    doc = json.loads(document.text)
    tokens = dict(doc.get("tokens") or {})
    tokens["access_token"] = access_token
    tokens["refresh_token"] = refresh_token
    if id_token:
        tokens["id_token"] = id_token
    doc["tokens"] = tokens
    stamp = _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    doc["last_refresh"] = stamp
    text = json.dumps(doc)
    return _Document(
        text=text,
        access_token=access_token,
        refresh_token=refresh_token,
        id_token=tokens.get("id_token") or "",
        last_refresh=stamp,
        expires_at=_claim_expiry(access_token),
    )


def _stored(universe_dir: Path, service: str) -> tuple[dict[str, Any], str] | None:
    """The one subscription record for ``service`` and its inline document.

    None when there is no record, when there is more than one (the launch path
    refuses that too), or when the record stores a PATH instead of a document.
    """
    from tinyassets.credential_vault import load_credential_vault

    key = service.strip().lower()
    records = [
        record
        for record in load_credential_vault(universe_dir)
        if record.get("credential_type") == "llm_subscription"
        and str(record.get("service") or "").strip().lower() == key
    ]
    if len(records) != 1:
        return None
    record = records[0]
    encoded = record.get("auth_json_b64")
    if not isinstance(encoded, str) or not encoded.strip():
        return None
    return record, encoded.strip()


def _refresh_document(
    *,
    universe_dir: str | Path,
    service: str,
    owner_user_id: str | None,
    universe_id: str,
    token_url: str = "",
    client_id: str = "",
    now: float | None = None,
    carry_as: str = "",
) -> str:
    """Bring this universe's stored subscription document up to date.

    Returns ``CARRIED`` when a rotated document was written AND its custody pin
    was carried onto it (same owner, same account, nothing downstream moves),
    ``ROTATED`` when a rotated document was written and the accepted source
    still has to be renewed, and ``""`` when nothing needed doing (no inline
    document, not stale, or another holder had already done it). Both non-empty
    values are truthy.

    ``carry_as`` is the proven owner. With it, the custody pin is moved in the
    same exclusive vault hold as the byte write, fenced on the pin still naming
    the document this call re-read and spent (``carry_llm_subscription_custody``)
    and on the rotated document naming the same account. Without it, or when the
    carry does not apply, the result is ``ROTATED``.
    Raises :class:`~tinyassets.credential_refresh.RefreshRejected` when the
    stored refresh token itself is finished, and
    :class:`~tinyassets.credential_refresh.RefreshUnavailable` for a transport or
    5xx failure — the caller decides what each means for the turn.

    Call BEFORE custody is resolved. Writing the vault changes
    ``_subscription_record_digest``, which is pinned into the provider binding,
    so a record rotated after custody resolution would make the launch refuse
    with "credential changed before launch snapshot". :func:`renew_accepted_source`
    is what carries the binding forward.
    """
    universe = Path(universe_dir)
    key = service.strip().lower()
    stored = _stored(universe, key)
    if stored is None:
        return ""
    moment = time.time() if now is None else now
    if not document_is_stale(_parse(stored[1]), moment):
        return ""
    rotated = False
    carried = False
    # The pin of the document re-read under the locks: the carry is fenced on
    # custody still naming exactly this record.
    spent_from: dict[str, Any] = {}

    def read() -> _Document:
        current = _stored(universe, key)
        if current is None:
            raise RefreshError(_UNREADABLE)
        return _parse(current[1])

    def stale(current: _Document) -> bool:
        # Re-decided on the re-read: a holder before us may already have rotated,
        # and spending a single-use refresh token twice is the bug being fixed.
        return document_is_stale(current, time.time())

    def spend(current: _Document) -> _Document:
        nonlocal rotated
        # The endpoint is resolved from the document that was RE-READ under the
        # locks, never from the one seen before them. Resolving it early and
        # capturing the pair let this interleaving through: read credential A,
        # the owner deposits credential B from a different issuer, the locks are
        # taken and B is re-read -- and B's refresh token went to A's endpoint
        # (Codex refute-review, P1 #2). The credential and the endpoint it is
        # spent at must come from the same read.
        endpoint, identifier = _source_endpoint(current, token_url, client_id)
        if not endpoint or not identifier:
            # The credential does not say where it came from, or that issuer
            # publishes no usable metadata. Nothing is spent; the launch proceeds
            # with what is stored, exactly as before.
            raise _NoEndpoint()
        if carry_as:
            from tinyassets.credential_vault import pinned_subscription_record_digest

            spent_from["pin"] = pinned_subscription_record_digest(universe, key)
            spent_from["document"] = current
        fresh = _spend(current, token_url=endpoint, client_id=identifier)
        rotated = True
        return fresh

    def carry(fresh: _Document) -> None:
        nonlocal carried
        pin = spent_from.get("pin")
        if not carry_as or not pin or not _same_account(spent_from["document"], fresh):
            return
        from tinyassets.credential_vault import carry_llm_subscription_custody
        from tinyassets.storage.provider_work_authority import (
            SQLiteProviderWorkAuthorityStore,
        )

        with SQLiteProviderWorkAuthorityStore(universe.parent).connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                moved = carry_llm_subscription_custody(
                    conn, universe_dir=universe, owner_user_id=carry_as,
                    universe_id=universe_id, service=key, expected_record_digest=pin,
                )
            except BaseException:
                conn.rollback()
                raise
            if moved:
                conn.commit()
            else:
                conn.rollback()
        carried = moved

    from tinyassets.credential_vault import llm_subscription_credential_record

    try:
        refresh_credential(
            universe_dir=universe,
            lock_id=f"llm_subscription::{key}",
            owner_user_id=owner_user_id,
            universe_id=universe_id,
            read=read,
            stale=stale,
            spend=spend,
            records=lambda fresh: [
                llm_subscription_credential_record(
                    service=key,
                    auth_json_b64=fresh.encoded(),
                    last_refresh=fresh.last_refresh,
                )
            ],
            subject="sign-in",
            after_write=carry,
        )
    except _NoEndpoint:
        return ""
    return CARRIED if carried else ROTATED if rotated else ""


#: :func:`_refresh_document` results. Truthy both, so "did it rotate" still reads.
CARRIED = "carried"
ROTATED = "rotated"


def _same_account(before: _Document, after: _Document) -> bool:
    """The rotated document names the account the spent one did.

    Belt, not the proof: the platform spent the exact pinned document's refresh
    token at the issuer that minted it, so the lineage holds by construction.
    This refuses a carry if a returned identity token says otherwise. The
    document's own account id is not compared: ``_rebuild`` carries every
    non-token key over unchanged, so it could never differ here.
    """
    old, new = _claims(before.id_token), _claims(after.id_token)
    return all(
        new.get(claim) is None or new.get(claim) == old.get(claim)
        for claim in ("iss", "sub")
    )


class _NoEndpoint(Exception):
    """The locked credential does not name a resolvable issuer. Nothing spent."""


def _claims(token: str) -> dict[str, Any]:
    """The unverified claim set of a JWT, or an empty mapping.

    Unverified deliberately: nothing here is an authorization decision. These are
    claims about OUR OWN stored credential, read to find out where it came from.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return claims if isinstance(claims, dict) else {}
    except Exception:  # noqa: BLE001 - a token that is not a JWT says nothing
        return {}


def _issued_by(document: _Document) -> tuple[str, str]:
    """Where this credential was issued and to which client, FROM the credential.

    No name of any source appears in this module, and that is the requirement, not
    a coincidence: the platform gives users primitives and does not carry
    per-source knowledge (``scripts/check_channel_agnostic.py`` measures it). The
    identity token the owner's sign-in stored already says both things -- ``iss``
    is the authorization server, ``aud``/``azp``/``client_id`` is the client it
    was issued to -- so the record describes its own source and the substrate
    learns nothing about any particular one.

    It also means this works for credentials already deposited, which a field
    added at deposit time would not.
    """
    claims = _claims(document.id_token)
    issuer = str(claims.get("iss") or "").strip()
    client = claims.get("client_id") or claims.get("azp") or claims.get("aud")
    if isinstance(client, list):
        client = client[0] if len(client) == 1 else ""
    client = str(client or "").strip()
    if not issuer or not client:
        return "", ""
    return issuer, client


def _source_endpoint(document: _Document, token_url: str, client_id: str) -> tuple[str, str]:
    """The token endpoint and client id to refresh against.

    An explicit pair from the caller wins (that is how a test names a fixture
    endpoint). Otherwise the issuer comes off the stored identity token and its
    token endpoint from that issuer's own RFC 8414 / OpenID metadata -- so the
    refresh is spent where the credential was issued and nowhere else, and a
    guessed URL is never constructed. A credential that does not say where it came
    from is not refreshed.
    """
    if token_url and client_id:
        return token_url, client_id
    issuer, client = _issued_by(document)
    if not issuer or not client:
        return "", ""
    from tinyassets.connection_oauth.discovery import fetch_server_metadata
    from tinyassets.connection_oauth.transport import OAuthError

    try:
        metadata = fetch_server_metadata(issuer)
    except (OAuthError, ValueError):
        return "", ""
    # The issuer here comes from an UNVERIFIED identity token, and that is a known
    # gap rather than an oversight. A signature check against the issuer's published
    # keys was built and removed: with an attacker-controlled ``iss`` it proves only
    # that the token is self-consistent, not where the credential came from, and it
    # added an unhardened key fetch to buy that. Anchoring the issuer outside the
    # mutable document is the real fix, and it needs a design rather than a patch:
    # docs/concerns/2026-09-26-subscription-refresh-endpoint-trust.md.
    return metadata.token_endpoint, client




def refresh_deposited_subscriptions(
    *,
    base_path: str | Path,
    universe_dir: str | Path,
    owner_user_id: str,
    universe_id: str,
    launching: str = "",
) -> None:
    """Refresh owner-deposited inline documents before a launch."""
    from tinyassets.credential_vault import (
        clear_refresh_rejected,
        load_credential_vault,
        record_refresh_rejected,
    )
    from tinyassets.external_agents.registry import PACKAGES
    from tinyassets.principals import named_principal

    owner = named_principal(owner_user_id)
    if not owner:
        raise ValueError("a launch refresh needs the proven owner to renew the binding")
    universe = Path(universe_dir)
    launched = launching.strip().lower()
    try:
        services = sorted({
            str(record.get("service") or "").strip().lower()
            for record in load_credential_vault(universe)
            if record.get("credential_type") == "llm_subscription"
            and isinstance(record.get("auth_json_b64"), str)
        })
    except ValueError:
        # A malformed vault is the launch path's own refusal to make, with its
        # own words; it is not this seam's business to pre-empt it.
        return
    for service in services:
        if service in PACKAGES:
            continue  # the installed CLI refreshes and the runner carries its custody
        if not service or not _deposited_by(base_path, universe_id, service, owner):
            # Another principal's deposit is never spent or adopted on this
            # owner's turn: proving the serving binding is not proving every
            # credential in the vault (Codex refute-review on #4076, P1).
            continue
        try:
            rotated = _refresh_document(
                universe_dir=universe,
                service=service,
                owner_user_id=None,
                universe_id=universe_id,
                carry_as=owner,
            )
        except RefreshRejected as exc:
            # Remembered for EVERY source, launching or not, and before the raise:
            # the owner has to be asked to sign in again whichever turn discovered
            # it, and an in-memory mark would be gone after the next deploy. Stored
            # off the credential record on purpose -- see record_refresh_rejected.
            record_refresh_rejected(base_path, universe_id=universe_id, service=service)
            if service != launched:
                # Another source's dead credential. The card is raised from the
                # stored rejection above; it is never this launch's failure.
                logger.info("a deposited sign-in for another source needs renewing")
                continue
            from tinyassets.exceptions import ProviderAuthenticationError

            raise ProviderAuthenticationError(
                f"{service} needs you to sign in again: {exc.detail}"
            ) from None
        except RefreshError:
            rotated = False  # transient: launch with what is stored
        if rotated:
            # A source that just produced a working authorization is not refused any
            # more, so the card goes. Cleared here rather than only on a deposit,
            # because a rotation the CLI made on disk fixes it without the owner
            # doing anything, and a card for a connection that works is worse than
            # no card.
            clear_refresh_rejected(base_path, universe_id=universe_id, service=service)
        if rotated == CARRIED:
            # Same owner, same account, new tokens: custody's pin moved with the
            # bytes, the consent did not change, and nothing downstream needs to
            # be republished -- which is what keeps receipts in flight valid.
            continue
        if rotated or _accepted_custody_is_stale(
            base_path, universe, owner, universe_id, service,
        ):
            # Also when nothing rotated HERE: a rotation whose renewal did not land
            # (a crash between the vault write and the renewal, or the pre-fix
            # renewal that named no owner) leaves fresh bytes behind a binding that
            # pins the old ones, and nothing else would ever renew it.
            renewed = renew_accepted_source(
                base_path=base_path,
                universe_dir=universe,
                service=service,
                owner_user_id=owner,
                universe_id=universe_id,
            )
            if renewed.get("status") != "serving":
                # Not raised: the launch's own custody check refuses with its own
                # words. Logged, because this is the one place that knows WHY.
                logger.warning(
                    "a renewed %s sign-in was not carried onto the binding: %s",
                    service, renewed.get("reason") or renewed.get("status"),
                )


def _deposited_by(base_path: str | Path, universe_id: str, service: str, owner: str) -> bool:
    """Whether ``owner`` is the recorded depositor of this universe's ``service``.

    A later deposit by anyone else is refused by the vault writer ("ownership
    transfer requires a dedicated flow"), so this row cannot change under us.
    """
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    with SQLiteProviderWorkAuthorityStore(base_path).connection() as conn:
        if conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'llm_credential_deposit_owners'",
        ).fetchone() is None:
            return False
        row = conn.execute(
            "SELECT owner_user_id FROM llm_credential_deposit_owners "
            "WHERE universe_id = ? AND service = ?",
            (universe_id, service),
        ).fetchone()
    return row is not None and str(row[0]) == owner


def _accepted_custody_is_stale(
    base_path: str | Path, universe: Path, owner: str, universe_id: str, service: str,
) -> bool:
    """Whether the owner CURRENTLY accepts this source and its bytes have moved.

    Only a source in the current accepted assignment is renewed: a custody row
    left from an earlier acceptance is history, not consent, and renewing it
    would re-point the assignment (Codex refute-review on #4076, P1).
    """
    from tinyassets.credential_vault import current_llm_subscription_custody
    from tinyassets.provider_assignment import load_provider_assignment_in_transaction
    from tinyassets.provider_serving_binding import _PROVIDER_SERVICE
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    providers = {p for p, s in _PROVIDER_SERVICE.items() if s == service}
    with SQLiteProviderWorkAuthorityStore(base_path).connection() as conn:
        conn.execute("BEGIN")
        try:
            try:
                assignment = load_provider_assignment_in_transaction(
                    conn, universe_id=universe_id,
                )
            except (ValueError, RuntimeError, PermissionError):
                # An unreadable assignment is the launch's own refusal to make,
                # in its own words; it is never a reason to renew anything.
                return False
            if (
                assignment is None or assignment.state != "ready"
                or assignment.owner_user_id != owner
            ):
                return False
            members = (
                {m.provider for m in assignment.candidates}
                if assignment.manifest_digest else {assignment.provider}
            )
            if not providers & members:
                return False
            return current_llm_subscription_custody(
                conn, universe_dir=universe, owner_user_id=owner,
                universe_id=universe_id, service=service,
            ) is None
        finally:
            conn.rollback()


def renew_accepted_source(
    *,
    base_path: str | Path,
    universe_dir: str | Path,
    service: str,
    owner_user_id: str,
    universe_id: str,
) -> dict[str, Any]:
    """Carry the accepted source's binding onto a rotated document.

    ``_subscription_record_digest`` covers the credential material, and that
    digest is pinned into the provider binding, so a refresh that only wrote the
    vault would leave serving refusing with "connect your provider before
    enabling serving". This is the landed renewal path, whose whole purpose is
    renewing an accepted source WITHOUT reading renewal as new model consent: a
    refresh is the same account and the same accepted model list with new bytes.

    Returns the serving module's own non-secret projection; never raises for an
    authority refusal, so a successful refresh is not reported as a failure.
    """
    from tinyassets.onboarding.serving import ensure_founder_serving

    return ensure_founder_serving(
        base_path=base_path,
        universe_dir=universe_dir,
        owner_user_id=owner_user_id,
        universe_id=universe_id,
        service=service.strip().lower(),
    )
