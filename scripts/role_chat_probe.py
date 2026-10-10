"""In-container production-backup chat acceptance, invoked by role_image_oracle.

The real bootstrap and HTTP server build every command, persona and MCP config.
Only auth transport material and the vendor endpoint are replaced locally. The
Docker network is internal and the real egress proxy admits only this endpoint.
No provider, authority check, launcher, model catalogue or engine route is mocked.
"""


def main():
    import http.server
    import json
    import os
    import runpy
    import sys
    import threading
    import time
    from pathlib import Path

    sys.path.insert(0, "/app")
    launch = runpy.run_path("/usr/local/libexec/ta-launch.py")
    bindings, services = launch["boot"](launch)
    center = os.environ["ORACLE_COMMAND_CENTER"]
    owner = next(owner for owner, name in bindings if name == center)
    os.environ["UNIVERSE_SERVER_DEV_USER"] = owner
    requests, launches = [], []
    answer = "ORACLE streamed response"

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert (
                self.headers.get("x-api-key") == "sk-oracle-local-only"
                or self.headers.get("Authorization") == "Bearer sk-oracle-local-only"
            )
            size = int(self.headers["Content-Length"])
            assert size <= 16 * 1024 * 1024
            body = json.loads(self.rfile.read(size))
            requests.append((self.path, body))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            if "/messages" in self.path:
                events = [
                    (
                        "message_start",
                        dict(
                            type="message_start",
                            message=dict(
                                id="msg_oracle",
                                type="message",
                                role="assistant",
                                content=[],
                                model=body["model"],
                                stop_reason=None,
                                stop_sequence=None,
                                usage=dict(input_tokens=12, output_tokens=0),
                            ),
                        ),
                    ),
                    (
                        "content_block_start",
                        dict(
                            type="content_block_start",
                            index=0,
                            content_block=dict(type="text", text=""),
                        ),
                    ),
                    (
                        "content_block_delta",
                        dict(
                            type="content_block_delta",
                            index=0,
                            delta=dict(type="text_delta", text=answer),
                        ),
                    ),
                    ("content_block_stop", dict(type="content_block_stop", index=0)),
                    (
                        "message_delta",
                        dict(
                            type="message_delta",
                            delta=dict(stop_reason="end_turn", stop_sequence=None),
                            usage=dict(output_tokens=4),
                        ),
                    ),
                    ("message_stop", dict(type="message_stop")),
                ]
            else:
                message = dict(
                    id="msg_oracle",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[dict(type="output_text", text=answer, annotations=[])],
                )
                response = dict(
                    id="resp_oracle",
                    object="response",
                    model="gpt-5",
                    status="completed",
                    output=[message],
                    usage=dict(input_tokens=12, output_tokens=4, total_tokens=16),
                )
                events = [
                    (
                        "response.created",
                        dict(
                            type="response.created",
                            response={**response, "status": "in_progress", "output": []},
                        ),
                    ),
                    (
                        "response.output_item.added",
                        dict(
                            type="response.output_item.added",
                            output_index=0,
                            item={**message, "status": "in_progress", "content": []},
                        ),
                    ),
                    (
                        "response.output_text.delta",
                        dict(
                            type="response.output_text.delta",
                            item_id="msg_oracle",
                            output_index=0,
                            content_index=0,
                            delta=answer,
                        ),
                    ),
                    (
                        "response.output_item.done",
                        dict(type="response.output_item.done", output_index=0, item=message),
                    ),
                    ("response.completed", dict(type="response.completed", response=response)),
                ]
            try:
                for event, payload in events:
                    time.sleep(0.15)
                    self.wfile.write(
                        ("event: " + event + "\ndata: " + json.dumps(payload) + "\n\n").encode()
                    )
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    from tinyassets import universe_egress

    base = f"http://stream.oracle.test:{server.server_port}"

    def checked(host, port):
        if host != "stream.oracle.test" or port != server.server_port:
            raise universe_egress.EgressRefused("oracle endpoint only")
        return ["127.0.0.1"]

    universe_egress._checked_addresses = checked
    # Replace only transport credentials/endpoint in the disposable launch snapshot.
    from tinyassets.providers import base as provider_base
    from tinyassets.providers import claude_provider, codex_provider

    original_env = provider_base.subprocess_env_for_provider

    def test_env(name, **kwargs):
        env = original_env(name, **kwargs)
        snapshot = kwargs.get("credential_snapshot_dir")
        if snapshot is None:
            raise RuntimeError("oracle requires real launch snapshot")
        from tinyassets.credential_vault import _write_exclusive_snapshot_file

        snapshot = Path(snapshot)
        key = "sk-oracle-local-only"
        for filename, data in [("oracle-key", key), ("oracle-url", base)]:
            if not (snapshot / filename).exists():
                _write_exclusive_snapshot_file(snapshot / filename, data.encode())
        if name == 'claude-code':
            env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
            env.update(ANTHROPIC_API_KEY=key, ANTHROPIC_BASE_URL=base)
        if name == "codex":
            config = snapshot / "config.toml"
            mode = config.stat().st_mode & 0o777
            config.chmod(mode | 0o200)
            config.write_text(
                'cli_auth_credentials_store = "file"\nmodel_provider = "oracle"\n'
                '[model_providers.oracle]\nname = "Oracle"\nbase_url = "'
                + base
                + '/v1"\nwire_api = "responses"\nenv_key = "OPENAI_API_KEY"\n'
            )
            config.chmod(mode)
            env["OPENAI_API_KEY"] = key
        return env

    provider_base.subprocess_env_for_provider = test_env
    claude_provider.subprocess_env_for_provider = test_env
    codex_provider.subprocess_env_for_provider = test_env
    from tinyassets import role_provider_discovery

    original_config = role_provider_discovery.cell_config

    def observe_config(argv, env, view_env, snapshot, data_root, **kwargs):
        result = original_config(argv, env, view_env, snapshot, data_root, **kwargs)
        system = argv[argv.index("--system-prompt") + 1] if "--system-prompt" in argv else ""
        launches.append(
            dict(
                executable=Path(argv[0]).name,
                argc=len(argv),
                config_bytes=len(result),
                system_bytes=len(system.encode()),
                engine_route=kwargs.get("engine_port") is not None,
                mcp_config="--mcp-config" in argv,
            )
        )
        return result

    role_provider_discovery.cell_config = observe_config
    from tinyassets import universe_server

    # Run the real server startup, engine routes, background services and HTTP surface.
    threading.Thread(target=universe_server.main, daemon=True).start()
    import urllib.error
    import urllib.request

    for _ in range(150):
        try:
            urllib.request.urlopen("http://127.0.0.1:8001/mcp/pulse", timeout=1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                break
            raise
        except OSError:
            time.sleep(1)
    import asyncio

    from fastmcp import Client

    async def chat():
        results = {}
        async with Client("http://127.0.0.1:8001/mcp", auth="oracle-local", timeout=180) as client:
            for provider in ("claude-code", "codex"):
                before = len(requests)
                arguments = {"message": "Say hello.", "graph_id": center}
                if provider == "codex":
                    arguments["model_choice"] = {
                        "version": 2,
                        "mode": "explicit",
                        "saved_default": {"provider_ref": provider, "model_id": ""},
                        "fallbacks": [],
                        "efforts": [],
                    }
                result = await client.call_tool("converse", arguments)
                document = result.data
                assert document.get("reply") == answer, document
                assert document["execution"]["provider"] == provider, document
                assert len(requests) > before, "reply did not reach the local streaming endpoint"
                results[provider] = dict(reply=document["reply"], requests=len(requests) - before)
        print(
            "PRODUCTION CHAT PASS " + json.dumps(dict(providers=results, launches=launches)),
            flush=True,
        )

    asyncio.run(chat())


if __name__ == "__main__":
    main()
