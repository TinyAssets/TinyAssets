"""Actual owner-delete admission, two-pass fences and foreign mutation refusals."""
import role_service_bootstrap_probe as probe

SETUP = r'''
for owner,uid in identities.items():
    center=root/('decoder-'+owner)
    subprocess.run(['setfacl','-m','u:1001:rwx,d:u:1001:rwx',str(center)],check=True)
    for name in ('owner.json','.credential-vault.json'):
        path=center/name; os.chown(path,1001,1001); path.chmod(0o600)
    work=center/'private-work'; work.mkdir(); os.chown(work,uid,uid)
    value=work/'value'; value.write_bytes(b'owner work'); os.chown(value,uid,uid)
    value.chmod(0); work.chmod(0)
    daemon=center/'daemon-dir'; daemon.mkdir(); os.chown(daemon,1001,1001)
    subprocess.run(['setfacl','--set',f'u::rwx,u:{uid}:rwx,g::-,m::rwx,o::-',
                    str(daemon)],check=True)
    mixed=daemon/'owner-child'; mixed.write_bytes(b'mixed owner work')
    os.chown(mixed,uid,uid); mixed.chmod(0)
    marker=daemon/'keep'; marker.write_bytes(b'daemon kept'); os.chown(marker,1001,1001)
    marker.chmod(0o600)
    abort_center=root/('abort-'+owner); abort_center.mkdir()
    os.chown(abort_center,uid,uid); abort_center.chmod(0o770)
    subprocess.run(['setfacl','-m','u:1001:rwx',str(abort_center)],check=True)
    bindings[(owner,abort_center.name)]=uid
    deep=abort_center
    for _ in range(70):
        deep=deep/'d'; deep.mkdir(); os.chown(deep,uid,uid); deep.chmod(0o700)
delete_sentinel=root/'foreign-sentinel'; delete_sentinel.write_bytes(b'foreign unchanged')
os.chown(delete_sentinel,identities['bob'],identities['bob']); delete_sentinel.chmod(0o640)
subprocess.run(['setfacl','-m','u:1001:r',str(delete_sentinel)],check=True)
os.link(delete_sentinel,root/'decoder-alice/foreign-hardlink')
os.symlink(delete_sentinel,root/'decoder-bob/owner-symlink')
os.lchown(root/'decoder-bob/owner-symlink',identities['bob'],identities['bob'])
'''

PROBE = r'''
from tinyassets import role_owner_delete as deletion
from tinyassets.owner_launcher_client import OwnerLaunchRefused
def refused(call):
    try: call()
    except (PermissionError,OwnerLaunchRefused): pass
    else: raise AssertionError('unsafe deletion admission accepted')
for owner in identities:
    center=root/('decoder-'+owner)
    identity=owner_identity(root,principal=owner)
    token=('a' if owner=='alice' else 'b')*32
    with identity_context(Identity(owner,owner)):
        abort_center=root/('abort-'+owner)
        grant_universe_access(root,universe_id=abort_center.name,actor_id=owner,
                              permission='admin',granted_by=owner)
        try: deletion.begin(abort_center,token='d'*32)
        except RuntimeError as exc: assert 'deletion' in str(exc),str(exc)
        else: raise AssertionError('over-depth deletion reported success')
        refused(lambda:role_decoder.decode(out.getvalue(),'image/png',center))
        deletion.abort(abort_center,token='d'*32)
        assert role_decoder.decode(out.getvalue(),'image/png',center).returncode==0
        other='bob' if owner=='alice' else 'alice'
        refused(lambda:deletion.begin(root/('decoder-'+other),token=token))
        # A live owner cell prevents pass-one admission before any deletion.
        with client.start_cell(kind='image-decoder',principal=owner,
                command_center=center.name,identity=identity,
                extra={'mime':'image/png'}) as held:
            refused(lambda:deletion.begin(center,token=token))
            held.cancel(); held.wait(5)
        fd=os.open(center,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            # EOF/cancellation before the fixed delete request retains the fence.
            with client.start_cell(kind='owner-delete',principal=owner,
                    command_center=center.name,identity=identity,directory_fd=fd,
                    extra={'delete_token':token}) as held:
                held.cancel(); held.wait(5)
        finally: os.close(fd)
        refused(lambda:role_decoder.decode(out.getvalue(),'image/png',center))
        refused(lambda:deletion.begin(center,token='c'*32))
        if owner=='alice':
            try: deletion.begin(center,token=token)
            except RuntimeError as exc: assert 'foreign-hardlink' in str(exc),str(exc)
            else: raise AssertionError('foreign hardlink deletion succeeded')
            assert delete_sentinel.read_bytes()==b'foreign unchanged'
            assert (center/'foreign-hardlink').stat().st_ino==delete_sentinel.stat().st_ino
            # Test-only removal of the planted name; foreign target stays intact.
            (center/'foreign-hardlink').unlink()
            with identity_context(Identity('bob','bob')):
                done=role_decoder.decode(out.getvalue(),'image/png',root/'decoder-bob')
                assert done.returncode==0
        result=deletion.begin(center,token=token)
        assert result['retained']>=4,result
        assert not (center/'private-work').exists()
        assert not (center/'daemon-dir/owner-child').exists()
        assert not (center/'owner-symlink').exists()
        assert (center/'daemon-dir/keep').read_bytes()==b'daemon kept'
        assert delete_sentinel.read_bytes()==b'foreign unchanged'
        refused(lambda:deletion.finish(center,token='c'*32))
        refused(lambda:role_decoder.decode(out.getvalue(),'image/png',center))
        # Synthetic daemon pass: every remaining entry is verified UID1001.
        for path in (center/'owner.json',center/'.credential-vault.json',center/'daemon-dir/keep'):
            assert path.lstat().st_uid==1001
            path.unlink()
        (center/'daemon-dir').rmdir()
        assert list(center.iterdir())==[]
        deletion.finish(center,token=token)
        assert role_decoder.decode(out.getvalue(),'image/png',center).returncode==0
        print(owner+': actual owner pass, daemon pass, quiescence/cancel/retry/stale '
              'fence, foreign mutation denial and reuse PASS; ZERO FOREIGN_BYTES',flush=True)
assert delete_sentinel.read_bytes()==b'foreign unchanged'
print('owner-delete acceptance PASS; startup OFF; ZERO FOREIGN_BYTES',flush=True)
'''


if __name__ == '__main__':
    setup = "run=Path(tempfile.mkdtemp(prefix='role-services-',dir='/run'))"
    marker = "if os.environ.get('TA_ORACLE_HTTPS')=='1':\n    stream['probe'](root)"
    assert probe.CONTAINER.count(setup) == probe.CONTAINER.count(marker) == 1
    probe.CONTAINER = probe.CONTAINER.replace(setup, SETUP + '\n' + setup).replace(marker, PROBE)
    raise SystemExit(probe.main())
