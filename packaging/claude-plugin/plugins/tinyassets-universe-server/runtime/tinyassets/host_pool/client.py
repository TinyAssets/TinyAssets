"""Supabase REST client for the ``host_pool`` table.

Stdlib-only. Wraps the Supabase PostgREST endpoints for the 5 host-pool
operations the daemon needs. No WebSocket (Presence deferred until
Realtime wiring lands — see package docstring).

Why not supabase-py: adds ~30 transitive deps for what amounts to
HTTP CRUD. We already ship ``urllib`` + ``json``; use those.

Auth: ``SUPABASE_SERVICE_ROLE_KEY`` for server-side writes (daemons are
trusted hosts, not tier-1 clients). Anon-key scoped calls would need RLS
policies that accept the host's own user context via JWT — out of scope
for Wave 1; use service-role until proper OAuth flow is wired at the
MCP edge.

Error model: one ``HostPoolError`` with ``status`` + ``body``. All
expected failure modes (non-2xx, JSON parse, missing env) surface as
this exception so callers don't branch on transport details.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


class HostPoolError(Exception):
    """Raised when a host_pool REST call fails.

    ``status`` is the HTTP code (0 if the request never completed, e.g.
    DNS / TLS / socket error). ``body`` is the response body or the
    underlying error message.
    """

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"host_pool REST error (status={status}): {body[:200]}")
        self.status = status
        self.body = body


@dataclass
class HostPoolRow:
    """Typed view of a ``public.host_pool`` row.

    Matches the schema at
    ``prototype/full-platform-v0/migrations/001_core_tables.sql``.
    """

    host_id: str
    owner_user_id: str
    provider: str
    capability_id: str
    visibility: str
    price_floor: float | None
    max_concurrent: int
    always_active: bool
    version: int

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> HostPoolRow:
        return cls(
            host_id=data["host_id"],
            owner_user_id=data["owner_user_id"],
            provider=data["provider"],
            capability_id=data["capability_id"],
            visibility=data["visibility"],
            price_floor=data.get("price_floor"),
            max_concurrent=int(data.get("max_concurrent", 1)),
            always_active=bool(data.get("always_active", False)),
            version=int(data.get("version", 1)),
        )


class _HttpClient(Protocol):
    """Injection seam so tests can swap in an in-memory transport."""

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, str]: ...


class _UrllibClient:
    """Real stdlib client. One instance per HostPoolClient."""

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, str]:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        ctx = ssl.create_default_context()
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                body_text = str(exc)
            return exc.code, body_text
        except urllib.error.URLError as exc:
            raise HostPoolError(0, f"unreachable: {exc.reason}") from exc
        except (TimeoutError, ssl.SSLError, OSError) as exc:
            raise HostPoolError(0, f"transport error: {exc!r}") from exc


class HostPoolClient:
    """REST wrapper over the ``host_pool`` / ``capabilities`` / ``requests`` tables.

    Parameters
    ----------
    supabase_url :
        Base URL of the Supabase project (e.g.
        ``https://<project>.supabase.co``). Override via
        ``SUPABASE_URL`` env var.
    service_role_key :
        Supabase service-role JWT (NOT the anon key). Override via
        ``SUPABASE_SERVICE_ROLE_KEY``.
    http :
        Injection seam — pass a custom ``_HttpClient`` in tests.
    timeout :
        Per-call HTTP timeout in seconds. Default 10s.
    """

    def __init__(
        self,
        supabase_url: str | None = None,
        service_role_key: str | None = None,
        *,
        http: _HttpClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        url = supabase_url or os.environ.get("SUPABASE_URL", "").strip()
        key = service_role_key or os.environ.get(
            "SUPABASE_SERVICE_ROLE_KEY", ""
        ).strip()
        if not url:
            raise HostPoolError(0, "SUPABASE_URL not configured")
        if not key:
            raise HostPoolError(0, "SUPABASE_SERVICE_ROLE_KEY not configured")
        self._base = url.rstrip("/")
        self._key = key
        self._http = http or _UrllibClient()
        self._timeout = timeout

    # -- helpers ------------------------------------------------------------

    def _headers(self, *, prefer: str | None = None) -> dict[str, str]:
        h = {
            "apikey": self._key,
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if prefer:
            h["Prefer"] = prefer
        return h

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        params: dict[str, str] | None = None,
        prefer: str | None = None,
    ) -> Any:
        qs = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self._base}/rest/v1/{path.lstrip('/')}{qs}"
        encoded = json.dumps(body).encode("utf-8") if body is not None else None
        status, text = self._http.request(
            method, url, self._headers(prefer=prefer), encoded, self._timeout,
        )
        if status < 200 or status >= 300:
            raise HostPoolError(status, text)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise HostPoolError(status, f"invalid JSON: {exc}; body={text[:200]}") from exc

    # -- host_pool CRUD ----------------------------------------------------

    def register(
        self,
        *,
        owner_user_id: str,
        provider: str,
        capability_id: str,
        visibility: str = "self",
        price_floor: float | None = None,
        max_concurrent: int = 1,
        always_active: bool = False,
    ) -> HostPoolRow:
        """Insert a row into ``host_pool``. Returns the new row.

        ``provider`` must be one of ``local|claude|codex|gemini``
        per schema CHECK. ``visibility`` must be one of
        ``self|network|paid``.
        """
        payload = {
            "owner_user_id": owner_user_id,
            "provider": provider,
            "capability_id": capability_id,
            "visibility": visibility,
            "max_concurrent": max_concurrent,
            "always_active": always_active,
        }
        if price_floor is not None:
            payload["price_floor"] = price_floor
        rows = self._request(
            "POST",
            "host_pool",
            body=payload,
            prefer="return=representation",
        )
        if not isinstance(rows, list) or not rows:
            raise HostPoolError(0, f"register returned no row: {rows!r}")
        return HostPoolRow.from_api(rows[0])

    def heartbeat(self, host_id: str) -> None:
        """Bump ``updated_at`` on the host_pool row (bimodal heartbeat).

        Per design call 2026-04-20: DB updated_at is the load-bearing
        liveness signal pre-Realtime. Upgrade to Presence when Supabase
        Realtime Presence wires in; this call then drops to ~hourly OR
        retires entirely.

        Safe to call on a missing row — returns without error (the
        next register call can recreate). Callers that care about
        row-presence should use ``get`` first.
        """
        self._request(
            "PATCH",
            "host_pool",
            body={"updated_at": "now()"},
            params={"host_id": f"eq.{host_id}"},
        )

    def update_visibility(self, host_id: str, visibility: str) -> None:
        """Change a host's visibility. ``self|network|paid``."""
        self._request(
            "PATCH",
            "host_pool",
            body={"visibility": visibility},
            params={"host_id": f"eq.{host_id}"},
        )

    def update_capability(self, host_id: str, capability_id: str) -> None:
        """Change a host's declared capability."""
        self._request(
            "PATCH",
            "host_pool",
            body={"capability_id": capability_id},
            params={"host_id": f"eq.{host_id}"},
        )

    def deregister(self, host_id: str) -> None:
        """Remove a host_pool row (clean shutdown).

        Not strictly required — Presence-based liveness would mark the
        host offline within TTL. But explicit cleanup is cheap + keeps
        the pool small for queries that filter on ``updated_at``.
        """
        self._request(
            "DELETE",
            "host_pool",
            params={"host_id": f"eq.{host_id}"},
        )

    def get(self, host_id: str) -> HostPoolRow | None:
        rows = self._request(
            "GET",
            "host_pool",
            params={"host_id": f"eq.{host_id}", "select": "*"},
        )
        if not isinstance(rows, list) or not rows:
            return None
        return HostPoolRow.from_api(rows[0])

    # -- capabilities ------------------------------------------------------

    def ensure_capability(
        self, capability_id: str, *, node_type: str, llm_model: str,
        description: str | None = None,
    ) -> None:
        """Insert the capability row if absent; no-op if present.

        Per schema: first daemon registration auto-inserts capability
        rows it declares (``openspec/specs/daemon-identity-and-host-pool/spec.md``).
        """
        payload = {
            "capability_id": capability_id,
            "node_type": node_type,
            "llm_model": llm_model,
        }
        if description:
            payload["description"] = description
        # ON CONFLICT DO NOTHING via Prefer header.
        self._request(
            "POST",
            "capabilities",
            body=payload,
            prefer="resolution=ignore-duplicates",
        )

    # -- requests surface (bid polling) ------------------------------------

    def list_pending_requests(
        self,
        capability_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Read pending requests matching a capability.

        Returns raw dicts (not a dataclass) because the request shape
        has jsonb ``inputs`` + per-field nullability we don't fully
        model yet. Consumers can project what they need.

        Filters: ``state='pending'`` AND ``capability_id=<cap>`` AND
        ``visibility IN ('paid', 'public')``. Sorted by oldest first —
        FIFO fairness for the first-draft.
        """
        rows = self._request(
            "GET",
            "requests",
            params={
                "state": "eq.pending",
                "capability_id": f"eq.{capability_id}",
                "visibility": "in.(paid,public)",
                "select": (
                    "request_id,requester_user_id,capability_id,"
                    "node_id,visibility,reserve_price,deadline,inputs,created_at"
                ),
                "order": "created_at.asc",
                "limit": str(limit),
            },
        )
        return list(rows) if isinstance(rows, list) else []
