"""Exercise the installed owner migration in the production image, synthetic only.

This proves migration, not class admission, full startup or actual old CMD boot.
No host directory or production volume is mounted; the container is disposable.
"""

from __future__ import annotations

import argparse
import json
import subprocess

PROGRAM = r"""
import json,os,runpy,sys,tempfile
from pathlib import Path
sys.path.insert(0,'/app/scripts')
from role_image_oracle import child,snapshot,status
module=runpy.run_path('/usr/local/libexec/ta-owner-migration.py')
migrate=module['migrate']
assert int(status()['CapEff'],16)==sum(1<<cap for cap in (0,1,3,5,6,7,8))
def seed():
    root=Path(tempfile.mkdtemp(prefix='u2-owner-')); root.chmod(0o755)
    (root/'.layout.lock').touch()
    (root/'.layout.json').write_text(json.dumps({'layout':2,'state':'stable',
        'moves':{'consents_outside_command_centers':'done'}}))
    for name in ('alice','bob'):
        tree=root/name; (tree/'work/.venv').mkdir(parents=True)
        (tree/'work/own').write_bytes(name.encode())
        (tree/'work/execute').write_bytes(b'exec'); (tree/'work/execute').chmod(0o755)
        (tree/'work/.venv/python').symlink_to('/outside/unchanged')
        (tree/'.credential-vault.json').write_bytes(b'protected')
        (tree/'.credential-vault.json').chmod(0o600)
    for parent,dirs,files in os.walk(root):
        os.chown(parent,1001,1001)
        for name in files: os.chown(os.path.join(parent,name),1001,1001,follow_symlinks=False)
    os.link(root/'alice/work/own',root/'bob/work/alias')
    return root
def run(root,**kw):
    return migrate(root,bindings={'alice':300001,'bob':300002},
        work={'alice':['work'],'bob':['work']},**kw)
def denied(path):
    try: path.read_bytes()
    except PermissionError: return
    raise AssertionError('unexpected access: '+str(path))
root=seed()
before=snapshot(root); run(root,dry_run=True); assert snapshot(root)==before
run(root)
before=snapshot(root)
repeat=run(root)
assert repeat['changed']==0, repeat
after=snapshot(root)
assert after==before, {name:(before.get(name),after.get(name))
    for name in before.keys()|after.keys() if before.get(name)!=after.get(name)}
q=root/'.role-owner-migration/quarantine'
assert (q/'alice/work/own').stat().st_ino==(q/'bob/work/alias').stat().st_ino
assert (q/'alice/work/own').read_bytes()==b'alice'
def owner():
    assert (root/'alice/work/execute').read_bytes()==b'exec'
    denied(root/'bob/work/own'); denied(root/'alice/.credential-vault.json')
    denied(q/'alice/work/own')
    path=root/'alice/work/engine'; path.mkdir(mode=0o700)
    (path/'private').write_bytes(b'engine-created'); (path/'private').chmod(0o600)
child(300001,[],owner)
child(300002,[],lambda:denied(root/'alice/work/execute'))
child(1001,[],lambda:denied(q/'alice/work/own'))
before=snapshot(root); run(root,reverse=True,dry_run=True); assert snapshot(root)==before
run(root,reverse=True); before=snapshot(root)
assert run(root,reverse=True)['changed']==0; assert snapshot(root)==before
def legacy():
    path=root/'alice/work/engine/private'; assert path.read_bytes()==b'engine-created'
    path.write_bytes(b'restored'); path.unlink(); path.parent.rmdir()
child(1001,[],legacy)
for reverse in (False,True):
    steps=['journal','marker','ownership','complete-journal']
    if not reverse: steps.append('quarantine-name')
    for step in steps:
        fixture=seed()
        if reverse: run(fixture)
        def crash(current):
            if current==step: raise InterruptedError(step)
        try: run(fixture,reverse=reverse,after_step=crash)
        except InterruptedError: pass
        else: raise AssertionError('crash boundary not reached')
        result=run(fixture,reverse=reverse)
        assert len(result['quarantined'])==2
        assert run(fixture,reverse=reverse)['changed']==0
        assert (fixture/'.role-owner-migration/quarantine/alice/work/own').read_bytes()==b'alice'
print(json.dumps(dict(owner_migration=True,dry_run=True,repeat=True,reverse=True,
    crash_boundaries=9,quarantine_names_preserved=True,foreign_access=False,
    restrictive_owner_files_restored=True,old_cmd_boot=False,startup_active=False)))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    image = subprocess.run(
        ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    command = [
        "docker",
        "run",
        "--rm",
        "-i",
        "--network",
        "none",
        "--user",
        "0:0",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges=true",
        "--security-opt",
        "seccomp=unconfined",
        "--security-opt",
        "apparmor=unconfined",
        "--security-opt",
        "systempaths=unconfined",
    ]
    for capability in ("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID", "SETPCAP", "KILL"):
        command += ["--cap-add", capability]
    command += ["--entrypoint", "/opt/venv/bin/python", image, "-I", "-B", "-"]
    print(json.dumps({"image": image, "command": command}), flush=True)
    subprocess.run(command, input=PROGRAM, text=True, check=True, timeout=120)


if __name__ == "__main__":
    main()
