"""S5 — the `converse` relay handle: fail-closed founder auth + delegation.

The handle relays the founder's turn to the universe intelligence and renders
its reply. Fail-closed: only the authenticated founder (owner) of the universe
may reach it — anonymous and non-owner callers are denied.
"""

from __future__ import annotations



from tinyassets.api import helpers, permissions


































def _founder_auth(monkeypatch, actor="founder-1", *, base=None, uid="u-x"):
    """Authenticate ``actor`` as a real admin-granted founder of ``uid``.

    This used to establish founder-ness by patching
    ``permissions.universe_access_allows`` to return True. That worked because
    the permissive helper WAS the tier check -- and it accepts a `write` grant as
    readily as `admin`, which is exactly the conflation this branch removes. So
    the fixture stops faking it and writes a real `admin` ACL row.

    These three tests failing was the change doing its job. I first recorded them
    as pre-existing after comparing against a stale base; against a tree pinned to
    origin/main they pass, which is what settled it.
    """
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    monkeypatch.setattr(helpers, "_request_universe", lambda gid="": uid)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: actor)
    monkeypatch.setattr(permissions, "current_request_actor_id", lambda: actor)
    if base is not None:
        from tinyassets.daemon_server import (
            ensure_universe_registered,
            grant_universe_access,
        )

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
        udir = base / uid
        udir.mkdir(parents=True, exist_ok=True)
        ensure_universe_registered(base, universe_id=uid, universe_path=udir)
        grant_universe_access(
            base, universe_id=uid, actor_id=actor, permission="admin"
        )








