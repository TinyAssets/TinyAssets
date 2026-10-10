"""Real split-UID daemon writer regression test, run by role_image_oracle.

Uses an isolated admitted center. No ownership, launcher or I/O doubles: each
operation runs as the retired daemon and every owner-measure cell must succeed.
"""
from __future__ import annotations

import json
import os
import runpy
import secrets
import sys
from pathlib import Path


def exercise(base, center, principal):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.role_content import _owner_entry
    from tinyassets.role_storage import measure
    from tinyassets.universe_owner import owner_of

    root = base / center
    passed = []

    def custody(name, target):
        owner = owner_of(base, target.name)
        assert owner is not None, (name, target.name, 'missing recorded owner')
        machine = owner_identity(base, principal=owner).uid
        for child in target.iterdir():
            if not _owner_entry(child.name):
                continue
            for path in [child, *child.rglob('*')] if child.is_dir() else [child]:
                info = path.lstat()
                assert (info.st_uid, info.st_gid) == (machine, machine), (
                    name, target.name, path.relative_to(target).as_posix(),
                    info.st_uid, info.st_gid)
        for scope in ('jail', 'universe', 'workspaces'):
            measure(target, scope)

    def check(name, operation, *, produces=(), all_centers=False):
        result = operation()
        if isinstance(result, str) and result.startswith('{'):
            result = json.loads(result)
        if isinstance(result, dict):
            assert 'error' not in result, (name, result.get('error'))
            assert result.get('state') != 'blocked', name
        for relative in produces:
            assert (root / relative).is_file(), (name, 'missing output', relative)
        # Inspect EVERY descendant of visible content, including dotfiles below
        # visible directories. Only platform top-level entries are excluded.
        targets = ([path for path in base.iterdir()
                    if path.is_dir() and owner_of(base, path.name) is not None]
                   if all_centers else [root])
        assert root in targets
        for target in targets:
            custody(name, target)
        passed.append(name)
        print('OWNER WRITER PASS ' + name, flush=True)

    with identity_context(Identity(principal, principal,
            capabilities=['read', 'list', 'write', 'submit_request', 'costly'])):
        from tinyassets.soul_edit import _write_edit_snapshot, apply_soul_edit
        from tinyassets.universe_bundle import seed_okf_bundle
        from tinyassets.universe_soul import write_universe_soul

        # Admission seeds an empty owner soul_versions directory. Rename it
        # aside without deleting anything, to exercise genuinely missing parents.
        versions = root / 'soul_versions'
        if versions.exists():
            versions.rename(root / 'oracle-preserved-soul-versions')
        check('first-soul-snapshot', lambda: _write_edit_snapshot(
            root, files={'identity.md': 'ORACLE'}, source='oracle', context='custody',
            summary='owner publication', stamp='2026-10-10T00:00:00Z'),
            produces=('soul_versions/0001.md', 'soul_versions/index.md'))
        check('bundle-seed', lambda: seed_okf_bundle(root))
        check('soul-update', lambda: write_universe_soul(root, purpose='ORACLE custody'))
        check('soul-edit', lambda: apply_soul_edit(root,
            changes={'identity.md': '# Identity\nORACLE custody\n'}, agent_id='main',
            source='oracle', context='owner writer regression'))

        from tinyassets.auto_ship_ledger import _file_lock
        from tinyassets.bid.execution_log import append_execution_log_entry
        from tinyassets.branch_tasks import BranchTask, append_task
        from tinyassets.config import write_universe_config_fields
        from tinyassets.subscriptions import subscribe

        check('config', lambda: write_universe_config_fields(root, oracle=True))
        check('branch-tasks', lambda: append_task(root, BranchTask(
            branch_task_id='oracle', branch_def_id='oracle', universe_id=center,
            goal_id='oracle')))

        def auto_ship():
            with _file_lock(root):
                pass
        check('auto-ship-lock', auto_ship)

        from tinyassets.api.wiki import _scoped_wiki_root, _wiki_write
        with _scoped_wiki_root(root / 'wiki'):
            check('wiki-category', lambda: _wiki_write(category='research',
                filename='oracle-custody', content='ORACLE wiki custody'))

        from tinyassets.api.universe import _action_add_canon
        check('canon-upload', lambda: _action_add_canon(
            universe_id=center, filename='oracle.txt', text='ORACLE upload', provenance_tag=''))

        from domains.fantasy_daemon.phases._activity import activity_log, update_phase
        from domains.fantasy_daemon.phases.commit import _export_prose
        from domains.fantasy_daemon.phases.worldbuild import _write_canon_file
        state = {'_universe_path': str(root)}
        check('activity-log', lambda: activity_log(state, 'ORACLE'), produces=('activity.log',))
        check('activity-append', lambda: activity_log(state, 'ORACLE second line'))
        assert len((root / 'activity.log').read_text().splitlines()) == 2
        check('phase-status', lambda: update_phase(state, 'oracle'), produces=('status.json',))
        check('scene-prose', lambda: _export_prose(state, 'ORACLE prose'),
            produces=('output/book-1/chapter-01/scene-01.md',))
        check('worldbuild-canon', lambda: _write_canon_file(
            root / 'canon', 'oracle-world.md', 'ORACLE canon'),
            produces=('canon/oracle_world.md', 'canon/.oracle_world.md.reviewed'))

        from tinyassets.bid.node_bid import NodeBid
        from tinyassets.branches import NodeDefinition
        from tinyassets.executors.node_bid import execute_node_bid
        node = NodeDefinition(node_id='oracle', display_name='Oracle',
            source_code="def run(state):\n    return {'oracle': True}\n").mark_approved()
        check('node-bid-artifact', lambda: execute_node_bid(
            NodeBid(node_bid_id='oracle', node_def_id='oracle', status='open'),
            node_lookup_fn=lambda _: node, output_dir=root / 'output'),
            produces=('output/bid_outputs/oracle/output.json',))

    # The actual periodic maintenance bodies, synchronously, with empty queues
    # and the request/queue records above. A swallowed blocked result is a fail.
    from tinyassets.command_center_update_maintenance import tick as update_centers
    from tinyassets.delivery_runtime import reconcile_deliveries
    from tinyassets.provider_assignment import reconcile_served_budget_leases
    from tinyassets.request_continuations import tick as resume_requests
    from tinyassets.run_file_retention import reconcile_run_files
    from tinyassets.run_input_origins import reconcile_admitted_runs
    from tinyassets.workspace_staging import sweep_data_root

    for name, operation in (
        ('center-update-tick', update_centers),
        ('request-continuation-tick', resume_requests),
        ('budget-lease-tick', reconcile_served_budget_leases),
        ('file-retention-tick', reconcile_run_files),
        ('run-admission-tick', reconcile_admitted_runs),
        ('delivery-tick', reconcile_deliveries),
        ('workspace-sweep', sweep_data_root),
    ):
        check(name, lambda operation=operation: operation(base), all_centers=True)
    check('subscriptions', lambda: subscribe(root, 'oracle'))
    check('execution-log', lambda: append_execution_log_entry(root, {'oracle': True}))
    from tinyassets.auto_ship_ledger import ShipAttempt, record_attempt, update_attempt
    check('auto-ship-content', lambda: record_attempt(root, ShipAttempt(
        ship_attempt_id='oracle', created_at='', updated_at='', ship_status='skipped')))
    check('auto-ship-append', lambda: record_attempt(root, ShipAttempt(
        ship_attempt_id='oracle-second', created_at='', updated_at='', ship_status='skipped')))
    check('auto-ship-rewrite', lambda: update_attempt(root, 'oracle', error_message='ORACLE'))
    return passed


def main():
    sys.path.insert(0, '/app')
    launch = runpy.run_path('/usr/local/libexec/ta-launch.py')
    launch['boot'](launch)
    assert os.getuid() == 1001
    from tinyassets.daemon_server import grant_universe_ownership
    from tinyassets.platform_runtime_provenance import require_process_cloud_admission
    from tinyassets.role_center_admission import admit_center

    base = Path('/data')
    nonce = secrets.token_hex(6)
    principal, center = 'oracle-writer-owner-' + nonce, 'u-oracle-writers-' + nonce
    require_process_cloud_admission(surface='owner writer oracle')
    grant_universe_ownership(base, universe_id=center, owner_id=principal)
    admit_center(base, principal=principal, center=center)
    result = exercise(base, center, principal)
    print('OWNER WRITERS PASS ' + json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
