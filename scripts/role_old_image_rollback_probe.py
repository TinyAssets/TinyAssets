"""Actual old-image egress rollback receipt on a disposable Docker volume.

No production mounts/network. This proves the existing D12 relocation substep,
NOT full owner-tree migration, restrictive engine-file rollback or old CMD boot.
Both images are pinned before execution; only the newly created volume is removed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import uuid

PREPARE = r'''
import json,os
from pathlib import Path
root=Path('/proof/data'); root.mkdir(mode=0o755); os.chown(root,1001,1001)
'''

OLD_COMMON = r'''
import json,os,sqlite3,sys
from pathlib import Path
sys.path.insert(0,'/app')
from tinyassets.storage.outbound_connections import ConnectionLedger
root=Path('/proof/data')
assert os.getresuid()==(1001,1001,1001) and os.getresgid()==(1001,1001,1001)
assert not set(os.getgroups()) & {1100,1101,1102}
fields=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines())
assert all(int(fields[k],16)==0 for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb'))
assert int(fields['NoNewPrivs'])==1
def insert(name):
    ConnectionLedger(root/'outbound.db').create_connection(
        connection_id=name,owner_user_id='alice',connection_class='http',
        connection_type='http',auth_scheme='bearer',scopes=('POST',),provider='http',
        destination='compute:synthetic',credential_ref='vault://http/synthetic',
        allowed_endpoints=[{'host':'models.example.com','path_template':'/v1/chat',
                            'methods':['POST']}])
'''

SEED = OLD_COMMON + r'''
(root/'.layout.lock').touch(mode=0o666)
(root/'.layout.json').write_text(json.dumps({'layout':2,'state':'stable',
    'moves':{'consents_outside_command_centers':'done'}}))
insert('old-before')
pid=os.fork()
if pid==0:
    db=sqlite3.connect(root/'outbound.db')
    db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA wal_autocheckpoint=0')
    db.execute('CREATE TABLE rollback_sentinel(value TEXT)')
    db.execute("INSERT INTO rollback_sentinel VALUES ('committed-old-WAL')")
    db.commit(); os._exit(0)
assert os.waitpid(pid,0)[1]==0
assert (root/'outbound.db-wal').stat().st_size>0
(root/'.outbound-proxy').mkdir(mode=0o700)
(root/'.outbound-proxy/audit').write_bytes(b'OLD-PROXY-BYTES')
print(json.dumps(dict(phase='old-image-seed',uid=os.getuid(),caps='zero',uncheckpointed_wal=True)))
'''

MIGRATE = r'''
import json,os,runpy,sys
from pathlib import Path
sys.path.insert(0,'/app/scripts')
from role_image_oracle import snapshot,child,insert
root=Path('/proof/data')
migrate=runpy.run_path('/usr/local/libexec/ta-egress-migration.py')['relocate']
before=snapshot(root); assert migrate(root,dry_run=True); assert snapshot(root)==before
migrate(root); stable=snapshot(root); assert migrate(root)==[]; assert snapshot(root)==stable
child(1002,[1102],lambda:insert(root/'.broker/outbound.db','new-broker-write'))
before=snapshot(root); assert migrate(root,reverse=True,dry_run=True); assert snapshot(root)==before
migrate(root,reverse=True); stable=snapshot(root)
assert migrate(root,reverse=True)==[]; assert snapshot(root)==stable
assert json.loads((root/'.layout.json').read_text())['state']=='migrating'
print(json.dumps(dict(phase='candidate-relocate-reverse',forward_dry_repeat=True,
    reverse_dry_repeat=True,broker_write=True,full_startup_admitted=False)))
'''

VERIFY = OLD_COMMON + r'''
insert('old-after')
with sqlite3.connect(root/'outbound.db') as db:
    assert db.execute('SELECT value FROM rollback_sentinel').fetchall()==[('committed-old-WAL',)]
    names={row[0] for row in db.execute('SELECT connection_id FROM outbound_connections')}
    assert {'old-before','new-broker-write','old-after'}<=names,names
    assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    db.execute("INSERT INTO rollback_sentinel VALUES ('old-image-write')")
    db.commit()
    assert (root/'outbound.db-wal').is_file() and (root/'outbound.db-shm').is_file()
audit=root/'.outbound-proxy/audit'; assert audit.read_bytes()==b'OLD-PROXY-BYTES'
with audit.open('ab') as handle: handle.write(b'-RESTORED-WRITE')
created=root/'.outbound-proxy/old-image-created'; created.mkdir(mode=0o700)
(created/'private').write_bytes(b'old image delete control')
(created/'private').unlink(); created.rmdir()
print(json.dumps(dict(phase='actual-old-image-rollback',uid=os.getuid(),groups=os.getgroups(),
    capabilities='zero',retained_wal=True,retained_broker_write=True,
    ledger_read_write=True,journals=True,proxy_read_write_delete=True,
    owner_tree_rollback=False,old_cmd_boot=False)))
'''


def pin(image):
    return subprocess.run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'],
                          check=True, capture_output=True, text=True).stdout.strip()


def run(image, volume, program, *, root=False):
    command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--user',
               '0:0' if root else '1001:1001', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges=true',
               '--mount', f'type=volume,src={volume},dst=/proof',
               '--entrypoint', '/opt/venv/bin/python']
    if root:
        for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID', 'SETPCAP', 'KILL'):
            command += ['--cap-add', cap]
    subprocess.run([*command, image, '-I', '-B', '-'], input=program, text=True,
                   check=True, timeout=90)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--old-image', required=True)
    args = parser.parse_args()
    candidate, old = pin(args.image), pin(args.old_image)
    volume = 'ta-role-rollback-' + uuid.uuid4().hex
    subprocess.run(['docker', 'volume', 'create', volume], check=True, capture_output=True)
    try:
        print(json.dumps(dict(candidate=candidate, old_image=old,
                              synthetic_volume=volume)), flush=True)
        run(candidate, volume, PREPARE, root=True)
        run(old, volume, SEED)
        run(candidate, volume, MIGRATE, root=True)
        run(old, volume, VERIFY)
    finally:
        subprocess.run(['docker', 'volume', 'rm', volume], check=True, capture_output=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
