"""The one tool implementation retains per-request context without module copies."""
import asyncio

from tinyassets import universe_tools
from tinyassets.engine_endpoint import EngineEndpoint, binding, current_server, grant_key


def test_concurrent_transports_share_handlers_without_sharing_context():
    async def exercise():
        first = EngineEndpoint("owner", "first", "secret-a", "grant-a")
        second = EngineEndpoint("owner", "second", "secret-b", "grant-b")
        assert first.module is second.module is universe_tools
        ready = asyncio.Event()
        seen = []

        async def app(scope, receive, send):
            if universe_tools._graph_id() == "first":
                ready.set()
                await asyncio.sleep(0)
            else:
                await ready.wait()
            seen.append((universe_tools._actor_id(), universe_tools._graph_id(), grant_key()))
            assert current_server().module is universe_tools

        first.app = second.app = app
        await asyncio.gather(
            first({"type": "lifespan"}, None, None),
            second({"type": "lifespan"}, None, None),
        )
        assert sorted(seen) == [("owner", "first", "grant-a"), ("owner", "second", "grant-b")]
        assert binding() is None

    asyncio.run(exercise())


def test_wrong_transport_secret_cannot_enter_handlers():
    async def exercise():
        endpoint = EngineEndpoint("owner", "home", "right-secret", "grant")
        messages = []

        async def app(*args):
            raise AssertionError("unauthenticated caller reached owner logic")

        async def send(message):
            messages.append(message)

        endpoint.app = app
        await endpoint({"type": "http", "headers": [(b"authorization", b"Bearer wrong")]},
                       None, send)
        assert messages[0]["status"] == 401
        assert binding() is None

    asyncio.run(exercise())


def test_workflow_actions_keep_evidence_but_not_standalone_extensions():
    from tinyassets.auth.provider import build_action_scope_registry

    registry = build_action_scope_registry()
    for action in ("register", "list", "inspect", "approve", "disable", "enable", "remove"):
        assert f"extensions.{action}" not in registry
    for action in ("record_outcome", "record_remix"):
        assert registry[f"extensions.{action}"].effect != "read"


def test_c10_guards_detect_regrowth_under_another_filename(tmp_path):
    from scripts.check_shapes import SHAPES, old_count

    root = tmp_path / "tinyassets"
    root.mkdir()
    (root / "new_registry.py").write_text("class NodeRegistration: pass\n")
    (root / "universe_tools.py").write_text('server = FastMCP("second")\n')
    assert old_count(SHAPES["S6"], tmp_path) == 1
    assert old_count(SHAPES["S7"], tmp_path) == 1
    assert all(SHAPES[key].ceiling == 0 for key in ("S6", "S7", "S18"))
