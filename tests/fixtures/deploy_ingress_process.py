"""Real HTTP app factory with isolated auth/cloud observations and no executor.

Only this Linux fixture enables AppAcceptance. Lifespan is off: the independent
frontend must not start the execution owner's schedulers, reconciliation or LLM.
"""

import socket
import sys
from pathlib import Path

import uvicorn
from starlette.responses import HTMLResponse
from starlette.routing import Route

from tests.fixtures.deploy_traffic_acceptance import SCOPE
from tinyassets.auth.provider import Identity


class FixtureAuth:
    def resolve_token(self, token):
        if token in {"fixture-token", "foreign-token"}:
            owner = SCOPE.principal_id if token == "fixture-token" else "foreign-user"
            return Identity(user_id=owner, username=owner)
        return None

    def is_auth_required(self):
        return True

    def resolve_always_writes(self):
        return False


def provision(root):
    from tinyassets.daemon_server import grant_universe_access, set_founder_home
    from tinyassets.storage.ingress_journal import initialize

    base = root / "runtime"
    (base / SCOPE.command_center_id).mkdir(parents=True, exist_ok=True)
    set_founder_home(base, founder_sub=SCOPE.principal_id,
                     universe_id=SCOPE.command_center_id, platform_generated=True)
    grant_universe_access(base, universe_id=SCOPE.command_center_id,
                          actor_id=SCOPE.principal_id, permission="admin",
                          granted_by=SCOPE.principal_id)
    initialize(root / "ingress")


def policy(scope, payload):
    # Pure fixture policy; production quota/custody integration is not enabled.
    if scope != SCOPE:
        raise PermissionError("fixture scope mismatch")


def main():
    from tinyassets import origin_admission, universe_server
    from tinyassets.auth import middleware
    from tinyassets.ingress import AppAcceptance

    root = Path(sys.argv[1])
    listener = socket.socket(fileno=int(sys.argv[2]))
    middleware.set_provider(FixtureAuth())
    # Isolated observation ONLY; never an environment bypass in product code.
    origin_admission.cached_process_is_cloud_admitted = lambda: True
    app = universe_server.create_streamable_http_app(ingress=AppAcceptance(
        root / "runtime", root / "ingress", admission_policy=policy,
    ))

    async def page(request):
        return HTMLResponse('<textarea id="draft"></textarea><output id="status"></output>')

    app.routes.insert(0, Route("/fixture-browser", page))
    uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="info")).run(sockets=[listener])


if __name__ == "__main__":
    main()
