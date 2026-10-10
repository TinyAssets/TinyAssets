"""In-container production-backup chat acceptance, invoked by role_image_oracle.

The real bootstrap and HTTP server build every command, persona and MCP config.
Only auth transport material and the vendor endpoint are replaced locally. The
Docker network is internal and the real egress proxy admits only this endpoint.
No provider, authority check, launcher, model catalogue or engine route is mocked.
The isolated server uses its local development identity verifier for the snapshot's
owner; HTTP request-authority middleware is real, external OAuth verification is not tested.
"""


def advertised_tools(request):
    """Classic and Responses-lite envelopes, including nested namespaces."""
    roots = list(request.get('tools', []))
    for item in request.get('input', []):
        if isinstance(item, dict) and item.get('type') == 'additional_tools':
            roots.extend(item['tools'])

    def names(items, prefix=''):
        for item in items:
            name = prefix + item['name']
            if item.get('type') == 'namespace':
                yield from names(item['tools'], '' if name == 'functions' else name + '.')
            else:
                yield name

    return list(names(roots))


def main():
    import http.server
    import json
    import os
    import runpy
    import shlex
    import ssl
    import sys
    import threading
    import time
    from pathlib import Path

    sys.path.insert(0, "/app")
    if os.environ.get('ORACLE_ISOLATED_NETWORK') == '1':
        import socket

        assert {name for index, name in socket.if_nameindex()} == {'lo'}
    launch = runpy.run_path("/usr/local/libexec/ta-launch.py")
    bindings, services = launch["boot"](launch)
    center = os.environ["ORACLE_COMMAND_CENTER"]
    owner = next(owner for owner, name in bindings if name == center)
    os.environ["UNIVERSE_SERVER_DEV_USER"] = owner
    requests, launches = [], []
    answer = "ORACLE real read write edit bash succeeded"
    active = {}
    from tinyassets import role_tools

    real_run = role_tools.run

    def observe_run(*args, **kwargs):
        try:
            result = real_run(*args, **kwargs)
        except Exception as exc:
            from tinyassets.cell_diagnostics import failure_reason

            print('ORACLE FAILURE ' + failure_reason(exc, 'decoder'), flush=True)
            raise
        active['runs'].append(result)
        return result

    role_tools.run = observe_run

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b'ORACLE curl egress succeeded'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                self.respond()
            except Exception as exc:
                from tinyassets.cell_diagnostics import failure_reason

                print('ORACLE FAILURE ' + failure_reason(exc, 'decoder'), flush=True)
                os._exit(2)  # Stop this disposable container; never retry a false proof.

        def respond(self):
            assert (
                self.headers.get("x-api-key") == "sk-oracle-local-only"
                or self.headers.get("Authorization") == "Bearer sk-oracle-local-only"
            )
            size = int(self.headers["Content-Length"])
            assert size <= 16 * 1024 * 1024
            body = json.loads(self.rfile.read(size))
            requests.append((self.path, body))
            claude = '/messages' in self.path
            step = active['step']
            offered = advertised_tools(body)
            # The CLI also sends side requests (no tools, no oracle history);
            # only the conversation advances the scripted tool sequence.
            conversation = (f'oracle_{step - 1}' in json.dumps(body) if step
                            else any(name.split('__')[-1] == 'write' for name in offered))
            if not conversation:
                active['side'] = active.get('side', 0) + 1
            if step and conversation:
                call_id = f'oracle_{step - 1}'

                def outputs(value):
                    if isinstance(value, dict):
                        if (value.get('tool_use_id') == call_id
                                or value.get('call_id') == call_id):
                            if value.get('type') in ('tool_result', 'function_call_output'):
                                yield value
                        for item in value.values():
                            yield from outputs(item)
                    elif isinstance(value, list):
                        for item in value:
                            yield from outputs(item)

                returned = list(outputs(body))
                assert returned, 'oracle tool result missing'
                # edit reads then rewrites the file: two real jail runs.
                runs = active['runs'][active['mark']:]
                for run in runs:
                    if run.exit_code != 0 or run.killed is not None:
                        print('ORACLE TOOL FAILURE ' + repr((run.exit_code, run.killed,
                              run.output[-2000:])), flush=True)
                assert len(runs) == (2 if step == 3 else 1), 'oracle real tool execution missing'
                assert all(run.exit_code == 0 and run.killed is None for run in runs), (
                    'oracle tool execution failed')
                run = runs[-1]
                assert not any(item.get('is_error') for item in returned), 'oracle tool error'
                if step in (2, 4):
                    expected = active['before'] if step == 2 else active['after']
                    assert expected.encode() in run.output, 'oracle tool content mismatch'
                    assert expected in json.dumps(returned), 'oracle MCP result content mismatch'
            calls = [
                ('write', dict(path=active['path'], content=active['before'])),
                ('read', dict(path=active['path'])),
                ('edit', dict(path=active['path'], old_text=active['before'],
                              new_text=active['after'])),
                ('bash', dict(command='cat ' + active['path']
                    + '; test ! -e /data && test ! -e /app && test ! -e /snapshot'
                    + ' && test "$(id -u)" -ne 0'
                    + ' && curl --fail --silent --show-error --max-time 15 '
                    + base + '/curl && test -s /etc/ssl/certs/ca-certificates.crt'
                    + ' && printf %s ' + shlex.quote(certificate)
                    + ' > /tmp/oracle-ca.pem && curl --fail --silent --show-error --max-time 15'
                    + ' --cacert /tmp/oracle-ca.pem ' + secure_base + '/curl'
                    + '; status=$?; test "$status" -eq 0 || exit "$status"'
                    + '; if curl --fail --silent --max-time 5 --noproxy "" '
                    + f'http://127.0.0.1:{server.server_port}/curl; then exit 91; fi')),
            ]
            tool = None
            if conversation and step < len(calls):
                name, arguments = calls[step]
                candidates = [item for item in offered if item.split('__')[-1] == name]
                assert len(candidates) == 1, 'oracle expected one advertised platform tool'
                tool = dict(name=candidates[0], arguments=arguments,
                            id=f'oracle_{step}')
                active['step'] += 1
                active['mark'] = len(active['runs'])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            if claude:
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
                if tool:
                    events[1] = ('content_block_start', dict(type='content_block_start', index=0,
                        content_block=dict(type='tool_use', id=tool['id'],
                                           name=tool['name'], input={})))
                    events[2] = ('content_block_delta', dict(type='content_block_delta', index=0,
                        delta=dict(type='input_json_delta',
                                   partial_json=json.dumps(tool['arguments']))))
                    events[4][1]['delta']['stop_reason'] = 'tool_use'
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
                if tool:
                    item = dict(type='function_call', id='fc_' + tool['id'],
                        call_id=tool['id'], name=tool['name'],
                        arguments=json.dumps(tool['arguments']), status='completed')
                    response['output'] = [item]
                    events = [events[0],
                        ('response.output_item.added', dict(type='response.output_item.added',
                            output_index=0,
                            item={**item, 'arguments': '', 'status': 'in_progress'})),
                        ('response.function_call_arguments.delta',
                            dict(type='response.function_call_arguments.delta',
                                 item_id=item['id'], output_index=0, delta=item['arguments'])),
                        ('response.output_item.done', dict(type='response.output_item.done',
                                                          output_index=0, item=item)),
                        ('response.completed', dict(type='response.completed', response=response))]
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
    # Local TLS, with certificate validation enabled. The private key never
    # enters a cell; curl receives only this fixture's public certificate.
    import tempfile
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'stream.oracle.test')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('stream.oracle.test')]), False)
            .sign(key, hashes.SHA256()))
    certificate = cert.public_bytes(serialization.Encoding.PEM).decode()
    tls_files = tempfile.TemporaryDirectory(prefix='oracle-tls-')
    cert_path, key_path = Path(tls_files.name) / 'cert.pem', Path(tls_files.name) / 'key.pem'
    cert_path.write_text(certificate)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    secure = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    secure.socket = tls.wrap_socket(secure.socket, server_side=True)
    threading.Thread(target=secure.serve_forever, daemon=True).start()
    from tinyassets import universe_egress

    base = f"http://stream.oracle.test:{server.server_port}"
    secure_base = f'https://stream.oracle.test:{secure.server_port}'
    original_checked = universe_egress._checked_addresses

    def checked(host, port):
        if host != "stream.oracle.test" or port not in (server.server_port, secure.server_port):
            if host == '127.0.0.1':
                original_checked(host, port)  # the real SSRF refusal, not the fixture policy
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
                command=next((part for part in argv
                              if part in ('exec', 'app-server', 'debug')), ''),
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
    from role_run_probe import prove_runs

    async def chat():
        results = {}
        async with Client("http://127.0.0.1:8001/mcp", auth="oracle-local", timeout=180) as client:
            from tinyassets.custom_agents import create_binding, publish_definition

            definition = publish_definition(Path('/data'), author_id=owner, payload={
                'schema_version': 1, 'name': 'Oracle sub-agent', 'components': {
                    'identity': {'kind': 'soul', 'config': {
                        'instructions': 'Follow the test request.'}}}})
            binding = create_binding(Path('/data'), universe_id=center,
                definition_id=definition['agent_definition_id'], created_by=owner,
                payload={'schema_version': 1, 'name': 'Oracle sub-agent'})
            turns = (("claude-code", 'main'), ("codex", 'main'),
                     ('claude-code', binding['agent_binding_id']))
            if os.environ.get('ORACLE_RUNS_ONLY') == '1':
                turns = ()
                active.update(step=4, runs=[], path='/u/oracle-unused', before='', after='')
            for provider, agent in turns:
                import secrets

                nonce = secrets.token_hex(12)
                active.clear()
                active.update(step=0, runs=[], path=f'/u/oracle-{nonce}.txt',
                              before='before-' + nonce, after='after-' + nonce)
                before = len(requests)
                arguments = {"message": "Use write, read, edit and bash in /u; report success.",
                             "graph_id": center}
                if provider == "codex":
                    arguments["model_choice"] = {
                        "version": 2,
                        "mode": "explicit",
                        "saved_default": {"provider_ref": provider, "model_id": ""},
                        "fallbacks": [],
                        "efforts": [],
                    }
                arguments['agent_id'] = agent
                result = await client.call_tool("converse", arguments)
                document = result.data
                assert document.get("reply") == answer, 'oracle reply mismatch'
                assert document["execution"]["provider"] == provider, 'oracle provider mismatch'
                assert active['step'] == 4 and len(active['runs']) == 5, 'oracle tools incomplete'
                assert len(requests) > before, "reply did not reach the local streaming endpoint"
                assert b'ORACLE curl egress succeeded' in active['runs'][-1].output
                results[provider + ':' + agent] = dict(
                    reply=document["reply"], requests=len(requests) - before,
                    tools=['write', 'read', 'edit', 'bash'],
                    side_requests=active.get('side', 0),
                    exit_codes=[run.exit_code for run in active['runs']])
            graph_runs = await prove_runs(client, center, owner)
        print(
            "PRODUCTION CHAT PASS " + json.dumps(dict(providers=results, launches=launches,
                                                     graph_runs=graph_runs)),
            flush=True,
        )

    try:
        asyncio.run(chat())
    except Exception as exc:
        from tinyassets.cell_diagnostics import failure_reason

        print('ORACLE FAILURE ' + failure_reason(exc, 'decoder'), flush=True)
        print('ORACLE DETAIL ' + str(exc), flush=True)
        raise


if __name__ == "__main__":
    main()
