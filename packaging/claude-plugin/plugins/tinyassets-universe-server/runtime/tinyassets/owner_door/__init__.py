"""The OWNER door: the app's reads of its owner's own data, always complete.

There are two doors onto a universe's data (ADR-011, *The Owner Door Is
Complete*; ``openspec/changes/archive/2026-09-30-owner-door-complete-reads/``):

* the **model door** — the MCP connector (``tinyassets.universe_server``) and the
  served-agent engine (``tinyassets.engine_mcp_server``). A model's context
  window is small, so that door projects what it returns: a single-result
  ceiling, compact views.
* the **owner door** — this package, ``/app/api/*``. The app on web, phone
  (Capacitor over the live ``/app``) and desktop (Electron over the live SPA)
  renders an owner's own data here. A screen has no context window, so this door
  returns the complete document and has nothing to bound.

This package contains no size, limit or truncation logic, and it cannot import
any: ``tests/test_owner_door_import_boundary.py`` fails if a module here imports
the ceiling (``engine_result_bounds``), the projections (``engine_read_views``)
or either model-door server. The only reads it makes are the shared domain reads
(``tinyassets.api.graph_reads`` and ``tinyassets.api.status``), and those cannot
import them either. So "make the app see all of it" has no switch to flip: the
app is already on the door that has no bound.

Authority is the same as the connector's, by construction. ``/app/*`` is
bearer-challenged by ``auth.middleware``; each handler re-checks for a named
identity and runs the domain read under that identity, which reaches the same
domain function and the same owner gate the connector does. This package adds
no gate and removes none.

Live incident, 2026-09-30: the founder's 7 pending requests (34 KB) crossed the
connector's 24 KB model ceiling. The app read the rail through that same door,
got a truncation marker with no ``pending`` list, and hid the whole rail, while
the free test account (with less data) kept its rail.
"""

from tinyassets.owner_door.routes import owner_door_routes

__all__ = ["owner_door_routes"]
