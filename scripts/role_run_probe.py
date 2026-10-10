"""Production-copy run acceptance; concatenated into the chat probe by the oracle.

A real local HTTPS peer receives service requests through the real broker.
The stored workflow, grants, run admission, provider CLI and owner cells remain real.
"""


async def prove_runs(client, center, owner):
    import asyncio
    import copy
    import json
    import os
    import secrets
    from datetime import datetime, timezone
    from pathlib import Path

    from tinyassets import automations, daemon_server, runs
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    root = Path('/data')
    intake = next(row for row in daemon_server.list_branch_definitions(root, include_private=True)
                  if row['name'] == 'Patch Request Intake')
    intake = daemon_server.get_branch_definition(root, branch_def_id=intake['branch_def_id'])
    definitions = intake['node_defs']
    http_node = copy.deepcopy(next(node for node in definitions if node['node_id'] == 'file_issue'))

    def wire_count():
        import urllib.request

        with urllib.request.urlopen('https://api.github.com/__oracle_calls', timeout=5) as reply:
            return json.load(reply)['calls']

    async def call(name, **arguments):
        response = await client.call_tool(name, arguments)
        value = response.data
        if isinstance(value, str):
            value = json.loads(value)
        assert isinstance(value, dict) and not value.get('error'), (name, value)
        return value

    async def terminal(run_id):
        for _ in range(240):
            record = runs.get_run(root / center, run_id) or runs.get_run(root, run_id)
            if record and record['status'] not in ('queued', 'running', 'pending'):
                assert record['status'] == 'completed', (
                    run_id, record['status'], record.get('error'))
                return record
            await asyncio.sleep(0.5)
        raise AssertionError('oracle background run did not finish')

    http_node['node_id'] = 'http'
    http_node['display_name'] = 'Local GitHub HTTP fixture'
    spec = dict(name='Owner-cell oracle ' + secrets.token_hex(5), visibility='private',
        entry_point='code', state_schema=[{'name': name, 'type': 'str'} for name in
            ('issue_title', 'issue_body', 'label', 'github_issue_request')],
        node_defs=[dict(node_id='code', display_name='Code', input_keys=[],
            output_keys=['issue_title', 'issue_body', 'label'],
            source_code='def run(state):\n    return {"issue_title":"Synthetic oracle",'
                        '"issue_body":"Production copy only", "label":"bug"}'), http_node],
        edges=[{'from': 'code', 'to': 'http'}, {'from': 'http', 'to': 'END'}])
    created = await call('write_graph', target='branch', operation='create', graph_id=center,
                         payload_json=json.dumps(spec))
    bid = created['branch_def_id']
    before = wire_count()
    started = await call('run_graph', branch_def_id=bid, graph_id=center, inputs_json='{}')
    background = await terminal(started['run_id'])
    assert wire_count() == before + 1, 'background HTTP effect did not reach GitHub stub'

    # Register through HTTP, then drive the real due-consumer entry point.
    # Startup left the pump off so restored production schedules cannot run.
    os.environ['TINYASSETS_ASSIGNED_QUEUE_CONSUMER'] = '1'
    created = await call('write_graph', target='automation', operation='create',
        graph_id=center,
        payload_json=json.dumps(dict(name='Owner-cell oracle', branch_def_id=bid,
                                     interval_seconds=86400, inputs={})))
    automation = automations.AutomationStore(root).get(created['automation']['automation_id'])
    ids = []
    before = wire_count()
    reason = await asyncio.to_thread(automations.run_due_automation, root, automation,
        datetime.now(timezone.utc).isoformat(), consumer_id='oracle', on_run_started=ids.append)
    assert ids, ('automation did not start', reason)
    automated = await terminal(ids[0])
    assert wire_count() == before + 1, ('automation HTTP effect missing', reason)
    await call('write_graph', target='automation', operation='pause', graph_id=center,
               automation_id=automation.automation_id, expected_revision=automation.revision)

    before = wire_count()
    started = await call('run_graph', branch_def_id=intake['branch_def_id'], graph_id=center,
        inputs_json=json.dumps(dict(what_they_tried='Synthetic verification on production copy',
            what_was_missing_or_broken='Synthetic verification; no product action',
            request_type='bug', delivery_sender_id=owner, delivery_sender_universe_id=center)))
    patched = await terminal(started['run_id'])
    assert wire_count() == before + 1, 'Patch Request Intake never filed its stub issue'
    events = runs.list_events(root / center, patched['run_id'])
    if not events:
        events = runs.list_events(root, patched['run_id'])
    completed_nodes = [event['node_id'] for event in events if event['status'] == 'ran']
    assert 'file_issue' in completed_nodes, 'stored workflow did not execute file_issue'
    assert background['actor'] == automated['actor'] == patched['actor'] == 'universe:' + center

    # The legacy and renamed actor forms have the identical exact-center boundary.
    from tinyassets import node_sandbox, role_scope
    from tinyassets.branches import BranchDefinition

    branch = BranchDefinition.from_dict(daemon_server.get_branch_definition(
        root, branch_def_id=bid))
    for actor in ('universe:' + center, 'command_center:' + center):
        with identity_context(Identity(actor, actor)):
            assert role_scope.owner_principal(root / center) == owner
            started = runs.execute_branch_async(root, branch=branch, inputs={}, actor=actor,
                owner_user_id=owner, _enqueue_universe_id=center)
        await terminal(started.run_id)

    # The workspace provisioner also consumes the shared remote-git scope.
    # Exercise both real cells with an empty lockfile: no registry fixture
    # or external network is needed, and npm still acquires then installs.
    from tinyassets import role_remote_git, workspace_fs, workspace_owner_pool
    from tinyassets.workspace_provision import admit_node
    from tinyassets.workspace_provision_execution import execute_provision
    from tinyassets.workspace_resolver import ProvisionManifests

    actor = 'command_center:' + center
    parts = list(workspace_owner_pool.pool_parts('scratch', ''))
    name = secrets.token_hex(12)
    answer = role_remote_git.run(
        dict(op='create', timeout_s=60, options=[], storage='scratch',
             lease_parent=parts, lease_name=name),
        universe_dir=root / center, principal=actor, egress_socket=None)
    assert answer['ok'], answer
    lease_fd = workspace_fs.open_dir_nofollow(root / center / Path(*parts) / name)
    repo_fd = workspace_fs.open_subdir_nofollow(lease_fd, 'repo')
    try:
        plan = admit_node('{"name":"oracle","version":"1.0.0"}',
            '{"name":"oracle","version":"1.0.0","lockfileVersion":3,"packages":{}}')
        # Provisioning overlays the checkout's original manifest files.
        # Create that input in the actor's descriptor-bound workspace node;
        # the general tool cell correctly sees workspaces read-only.
        with identity_context(Identity(actor, actor)):
            seeded = node_sandbox.NodeSandbox(universe_dir=root / center).run_sync(
                'workspace-fixture', 'def run(state):\n'
                '    ws.write("package.json", state["package"])\n'
                '    ws.write("package-lock.json", state["lock"])\n'
                '    return {}',
                {'package': plan.normalized_package_json, 'lock': plan.normalized_lockfile},
                ['package', 'lock'], [], workspace=node_sandbox.WorkspaceMount(
                    f'/proc/self/fd/{repo_fd}', pass_fds=(repo_fd,)))
        assert seeded.success, seeded
        provisioned = execute_provision(ProvisionManifests(None, plan),
            lease_fd=lease_fd, repo_fd=repo_fd, universe_dir=root / center, principal=actor,
            max_transfer_bytes=1024 * 1024, storage_bound=128 * 1024 * 1024,
            timeout_s=120, cancelled=lambda: False)
        assert provisioned.failure is None and provisioned.bytes_to_charge == 0, provisioned
    finally:
        os.close(repo_fd)
        os.close(lease_fd)
    for actor in ('oracle-foreign-owner', 'universe:foreign', 'command_center:foreign'):
        with identity_context(Identity(actor, actor)):
            try:
                node_sandbox.NodeSandbox(universe_dir=root / center).run_sync(
                    'foreign', 'def run(state): return {}', {}, [], [])
            except PermissionError:
                pass
            else:
                raise AssertionError('cross-owner scope admitted')
    return dict(background=background['status'], automation=automated['status'],
                patch_intake=patched['status'], github_calls=wire_count(),
                nodes=completed_nodes, cross_owner='refused',
                workspace_provision='completed')
