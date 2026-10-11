"""The online repair must never run with an unverified backup."""
import hashlib
import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml

from deploy import repair_content_after_deploy as repair


@pytest.fixture(autouse=True)
def backup_clock(monkeypatch):
    original = repair.newest_full_asset
    monkeypatch.setattr(repair, 'newest_full_asset', lambda releases: original(
        releases, now=datetime(2026, 10, 11, tzinfo=timezone.utc)))


def asset(data=b'full backup'):
    return dict(id=42, name='tinyassets-data-2026-10-10T23-20-50Z.tar.gz',
                state='uploaded', size=len(data),
                digest='sha256:' + hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize('failure', ['digest', 'size', 'missing', 'brain', 'truncated'])
def test_unverified_backup_never_launches_operator(monkeypatch, failure):
    metadata = asset()
    payload = b'full backup'
    if failure == 'digest':
        metadata['digest'] = 'sha256:' + '0' * 64
    elif failure == 'size':
        metadata['size'] += 1
    elif failure == 'missing':
        metadata.pop('digest')
    elif failure == 'brain':
        metadata['name'] = metadata['name'].replace('-data-', '-brain-')
    else:
        payload = b'partial'
    responses = iter([json.dumps([{'assets': [metadata]}]).encode(), payload])
    monkeypatch.setenv('GH_TOKEN', 'test-token')
    monkeypatch.setattr(repair.urllib.request, 'build_opener', lambda *a: SimpleNamespace(
        open=lambda *a, **kw: io.BytesIO(next(responses))))
    launched = []
    monkeypatch.setattr(repair.subprocess, 'run', lambda *a, **kw: launched.append(a))
    with pytest.raises(RuntimeError):
        repair.main('ghcr.io/example/daemon@sha256:' + 'a' * 64)
    assert launched == []


@pytest.mark.parametrize('apply_rc,expected_calls', [(0, 2), (2, 1), (1, 1)])
def test_verified_backup_applies_and_checks_convergence(monkeypatch, apply_rc, expected_calls):
    older = asset()
    older['name'] = older['name'].replace('2026-10-10', '2026-10-09')
    responses = iter([json.dumps([{'assets': [older, asset()]}]).encode(), b'full backup'])
    monkeypatch.setenv('GH_TOKEN', 'test-token')
    monkeypatch.setattr(repair.urllib.request, 'build_opener', lambda *a: SimpleNamespace(
        open=lambda *a, **kw: io.BytesIO(next(responses))))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=apply_rc if '--apply' in command else 0,
                               stdout=json.dumps({'changed': 0, 'skipped_symlinks': []}))

    monkeypatch.setattr(repair.subprocess, 'run', run)
    assert repair.main('ghcr.io/example/daemon@sha256:' + 'a' * 64) == apply_rc
    assert len(calls) == expected_calls
    assert calls[0][-1] == '--apply'
    assert calls[0][-2] == asset()['name']
    assert '--network' in calls[0] and 'none' in calls[0]
    if apply_rc == 0:
        assert '--apply' not in calls[1]


def test_repair_failure_is_visible_without_rollback():
    from pathlib import Path

    steps = yaml.safe_load((Path(__file__).resolve().parents[1] /
                           '.github/workflows/deploy-prod.yml').read_text())['jobs']['deploy']['steps']
    by_id = {step['id']: step for step in steps if 'id' in step}
    repair_step = by_id['content_repair']
    assert steps.index(repair_step) == steps.index(by_id['deploy']) + 1
    assert repair_step['continue-on-error'] is True
    alert = next(step for step in steps
                 if step.get('name') == 'Alert on incomplete owner content repair')
    assert "always() && steps.content_repair.outcome == 'failure'" in alert['if']
    assert 'gh issue create' in alert['run'] and 'GITHUB_STEP_SUMMARY' in alert['run']
    rollback = next(step for step in steps
                    if step.get('name') == 'Roll back if the public canary is red')
    assert 'content_repair' not in rollback['if']


def test_activity_refusal_logs_path_and_reason(tmp_path, monkeypatch, caplog):
    from domains.fantasy_daemon.phases import _activity

    def refuse(*args, **kwargs):
        raise PermissionError('owner-content refused')

    monkeypatch.setattr(_activity, 'write_data_path', refuse)
    _activity.activity_log({'_universe_path': str(tmp_path)}, 'private message')
    assert caplog.records[-1].levelname == 'WARNING'
    assert str(tmp_path / 'activity.log') in caplog.text
    assert 'owner-content refused' in caplog.text


@pytest.mark.parametrize('date', ['2026-10-08', '2026-10-12'])
def test_stale_or_future_backup_refuses(date):
    metadata = asset()
    metadata['name'] = metadata['name'].replace('2026-10-10', date)
    with pytest.raises(RuntimeError, match='stale or future'):
        repair.newest_full_asset([{'assets': [metadata]}])


@pytest.mark.parametrize('stdout', [json.dumps({'centers': 2, 'changed': 3,
                                              'skipped_symlinks': ['private/file']}),
                                   'refused private/file'])
def test_workflow_report_never_exposes_owner_filenames(monkeypatch, capsys, stdout):
    monkeypatch.setattr(repair.subprocess, 'run', lambda *a, **kw: SimpleNamespace(
        returncode=2, stdout=stdout, stderr='private/file'))
    assert repair.run_repair(['operator'], 'apply') == 2
    output = capsys.readouterr()
    assert 'private' not in output.out + output.err
    assert '"rc": 2' in output.out
