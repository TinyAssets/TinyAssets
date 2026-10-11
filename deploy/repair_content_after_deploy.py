"""Verify the newest full offsite backup, then repair using the healthy image.

Run on the host with its backup environment. No data is extracted or rewritten;
the archived bytes are streamed to verify GitHub's SHA-256 before the operator
container may relabel content. Refusals leave the serving container alone.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone


class AssetRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header('Authorization')
        return redirected


def newest_full_asset(releases, *, now=None):
    candidates = [
        asset for release in releases
        if not release.get('draft') and not release.get('prerelease')
        for asset in release.get('assets', [])
        if re.fullmatch(r'tinyassets-(?:data|snapshot)-\d{4}-\d{2}-\d{2}'
                        r'T\d{2}-\d{2}-\d{2}Z\.tar\.gz',
                        asset.get('name', '')) and asset.get('state') == 'uploaded'
    ]
    if not candidates:
        raise RuntimeError('no completed full backup in the newest 100 releases')
    asset = max(candidates, key=lambda item: item['name'].split('-', 2)[2])
    timestamp = datetime.strptime(asset['name'].split('-', 2)[2],
                                  '%Y-%m-%dT%H-%M-%SZ.tar.gz').replace(tzinfo=timezone.utc)
    age = (now or datetime.now(timezone.utc)) - timestamp
    if not timedelta(0) <= age <= timedelta(hours=26):
        raise RuntimeError('newest full backup is stale or future-dated; refusing repair')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', asset.get('digest') or ''):
        raise RuntimeError('newest full backup has no SHA-256; refusing repair')
    if not isinstance(asset.get('id'), int) or asset.get('size', 0) <= 0:
        raise RuntimeError('newest full backup has invalid asset metadata')
    return asset


def verify_bytes(stream, asset):
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    if size != asset['size'] or 'sha256:' + digest.hexdigest() != asset['digest']:
        raise RuntimeError('full backup size or SHA-256 mismatch; refusing repair')


def main(image):
    if not re.fullmatch(r'ghcr\.io/[a-z0-9_./-]+@sha256:[a-f0-9]{64}', image):
        raise RuntimeError('repair requires the immutable deployed image')
    token = os.environ.get('GH_TOKEN', '').strip()
    repo = os.environ.get('BACKUP_GH_REPO', 'Jonnyton/tinyassets-backups')
    if not token or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise RuntimeError('backup GitHub credential or repository unavailable')
    base = 'https://api.github.com/repos/' + repo
    opener = urllib.request.build_opener(AssetRedirect())

    def request(url, accept):
        return opener.open(urllib.request.Request(url, headers={
            'Authorization': 'Bearer ' + token, 'Accept': accept,
            'X-GitHub-Api-Version': '2022-11-28',
        }), timeout=60)

    with request(base + '/releases?per_page=100', 'application/vnd.github+json') as response:
        asset = newest_full_asset(json.load(response))
    with request(base + '/releases/assets/' + str(asset['id']),
                 'application/octet-stream') as response:
        verify_bytes(response, asset)
    print('verified full backup: ' + asset['name'] + ' ' + asset['digest'], flush=True)
    command = [
        'docker', 'run', '--rm', '--user', '0', '--cap-drop', 'ALL',
        '--cap-add', 'CHOWN', '--cap-add', 'FOWNER', '--cap-add', 'DAC_OVERRIDE',
        '--network', 'none', '-v', 'tinyassets-data:/data',
        '--entrypoint', '/opt/venv/bin/python', image,
        '-I', '-B', '/usr/local/libexec/ta-migrate.py',
        '--repair-content-online', '--backup', asset['name'],
    ]
    applied = run_repair(command + ['--apply'], 'apply')
    if applied:
        return applied
    # A second inspection proves convergence; a racing old writer is visible.
    return run_repair(command, 'verify')


def run_repair(command, phase):
    # The CLI's detailed report contains owner filenames. Public Actions logs
    # receive only fixed status/count fields, including on a CLI refusal.
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    summary = {'phase': phase, 'rc': result.returncode}
    try:
        report = json.loads(result.stdout)
        for name in ('centers', 'changed'):
            if type(report.get(name)) is int:
                summary[name] = report[name]
        skipped = report.get('skipped_symlinks')
        if isinstance(skipped, list):
            summary['skipped_symlinks'] = len(skipped)
    except (ValueError, AttributeError):
        summary['report'] = 'unavailable; use manual inspection for details'
    print(json.dumps(summary, sort_keys=True), flush=True)
    return result.returncode


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1]))
    except Exception as exc:
        # Transport exceptions can carry credentials/URLs. Keep their type only.
        reason = str(exc) if type(exc) is RuntimeError else type(exc).__name__
        print('online content repair refused: ' + reason, file=sys.stderr)
        sys.exit(1)
