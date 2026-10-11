"""TinyAssets daemon host-pool — Track D Wave 1.

Spec: docs/architecture.md §2
Track D. Schema: prototype/full-platform-v0/migrations/001_core_tables.sql
(commit ``98055aa``'s Track A foundation).

Surface:
- ``HostPoolClient`` (``client.py``) — Supabase REST wrapper for the 5
  host-pool operations: register, update visibility, update capabilities,
  heartbeat, deregister. No WebSocket; pure HTTP. Mockable via protocol.
- ``register_daemon`` (``registration.py``) — startup flow. Creates the
  host-pool row + returns the host_id the tray holds for the session.
- ``HeartbeatLoop`` (``heartbeat.py``) — asyncio task. Bumps
  ``host_pool.updated_at`` every 5 min. Bimodal per design call 2026-04-20:
  DB updated_at is the load-bearing liveness signal until Supabase Realtime
  Presence wires in; then Presence takes over and this loop can drop to
  ~1 hour or retire.
- ``BidPoller`` (``bid_poller.py``) — asyncio task. Every 60s, reads
  ``public.requests`` rows matching the host's capability, surfaces them
  to a caller-supplied callback. NO auto-claim in first-draft —
  tray decides what to show / claim (host action).

All HTTP calls go through ``scripts/mcp_public_canary.py``-style stdlib
``urllib`` to avoid adding a Supabase SDK dep. Third-party deps are a
separate scope call (``supabase-py`` is ~30 transitive deps).
"""

from __future__ import annotations

from tinyassets.host_pool.bid_poller import BidPoller
from tinyassets.host_pool.client import HostPoolClient, HostPoolError
from tinyassets.host_pool.heartbeat import HeartbeatLoop
from tinyassets.host_pool.registration import register_daemon

__all__ = [
    "BidPoller",
    "HeartbeatLoop",
    "HostPoolClient",
    "HostPoolError",
    "register_daemon",
]
