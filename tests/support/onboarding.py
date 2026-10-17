"""Authenticated onboarding route drivers shared by app tests."""
import asyncio
import json


def _home(monkeypatch, home="u-home"):
    """Pin the server-resolved home: the POST routes bootstrap it, the GET reads it."""
    import tinyassets.onboarding as onboarding

    monkeypatch.setattr(onboarding, "_bootstrap_home", lambda identity: home)
    monkeypatch.setattr(onboarding, "_read_home", lambda identity, **kwargs: home)


def _user(sub="user_123"):
    from tinyassets.auth.provider import Identity

    return Identity(
        user_id=sub, username=sub, display_name=sub, capabilities=["write"], metadata={}
    )


def _drive_get(path, *, identity=None, monkeypatch):
    from starlette.requests import Request

    from tinyassets.auth.middleware import identity_context
    from tinyassets.onboarding import onboarding_routes

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    route = next(r for r in onboarding_routes() if r.path == path)
    scope = {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def run():
        req = Request(scope, receive)
        if identity is not None:
            with identity_context(identity):
                return await route.endpoint(req)
        # `identity=None` means NOBODY IS HERE, which is what the 401 tests
        # assert. The ambient signed-in operator would otherwise answer for
        # them, and the route would return 200.
        from tinyassets.auth.middleware import clear_identity

        clear_identity()
        return await route.endpoint(req)

    resp = asyncio.run(run())
    return resp.status_code, json.loads(resp.body or b"{}")

