"""Package and provider-exec cells outlive every former fixed class deadline (660s).

Slow by design (about twelve minutes); run it when a lifetime claim needs proof.
"""
from __future__ import annotations

import argparse
import sys

import role_package_probe
import role_provider_discovery_probe
import role_service_bootstrap_probe as probe

SECONDS = 700

LONG = f'''
import time
print('ready',flush=True)
time.sleep({SECONDS})
print('alive',flush=True)
'''

PACKAGE = r'''
import time
from tinyassets import role_packages
owner='alice'
center=root/('decoder-'+owner)
with identity_context(Identity(owner,owner)):
    started=time.monotonic()
    with role_packages.start(center,packages[owner]['long']) as cell:
        with cell.stream.makefile('rb') as reader:
            assert reader.readline()==b'ready\n'
            assert reader.readline()==b'alive\n'
        assert cell.wait(30)==0
    elapsed=time.monotonic()-started
    assert elapsed>660,elapsed
print(f'package cell ran {elapsed:.0f}s past the former 660s deadline and exited 0 PASS',
      flush=True)
'''

PROVIDER = r'''
import asyncio,time
from tinyassets.providers.owned_process import aspawn_owned
from tinyassets.providers.provider_jail import provider_launch_scope
async def long_turn():
    owner='alice'
    center=root/('decoder-'+owner)
    snapshot=center/'.runtime/provider-launch-credentials/fixture'
    with identity_context(Identity(owner,owner)):
        with provider_launch_scope(center,credential_dir=snapshot):
            started=time.monotonic()
            proc=await aspawn_owned(['/usr/local/bin/python3.11','-I','-S','-c',LONG],env={},
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            stdout,stderr=await proc.communicate()
    elapsed=time.monotonic()-started
    assert proc.returncode==0 and stdout==b'ready\nalive\n',(proc.returncode,stdout,stderr)
    assert elapsed>660,elapsed
    print(f'provider-exec cell ran {elapsed:.0f}s past the former 660s deadline and exited 0 PASS',
          flush=True)
asyncio.run(long_turn())
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--kind', choices=('package', 'provider'), required=True)
    args = parser.parse_args()
    setup = "run=Path(tempfile.mkdtemp(prefix='role-services-',dir='/run'))"
    marker = "if os.environ.get('TA_ORACLE_HTTPS')=='1':\n    stream['probe'](root)"
    assert probe.CONTAINER.count(setup) == probe.CONTAINER.count(marker) == 1
    if args.kind == 'package':
        prepared = role_package_probe.SETUP.replace(
            "    packages[owner]={}\n",
            "    definitions['long']=('python','main.py',LONG)\n    packages[owner]={}\n", 1)
        assert prepared != role_package_probe.SETUP
        body = PACKAGE
        prefix = (f'PYTHON={role_package_probe.PYTHON!r}\nNODE={role_package_probe.NODE!r}\n'
                  f'BROKER={role_package_probe.BROKER!r}\n'
                  f'EGRESS={role_package_probe.EGRESS!r}\n'
                  f'NO_EGRESS={role_package_probe.NO_EGRESS!r}\n')
    else:
        prepared, body, prefix = role_provider_discovery_probe.SETUP, PROVIDER, ''
    probe.CONTAINER = (f'LONG={LONG!r}\n' + prefix + probe.CONTAINER
                       .replace(setup, prepared + '\n' + setup)
                       .replace(marker, body))
    # Stream mode supplies the egress relay provider-exec pins, and unlike the
    # plain runner it does not cap the container at 180 seconds.
    sys.argv = [sys.argv[0], '--image', args.image, '--stream']
    return probe.main()


if __name__ == '__main__':
    raise SystemExit(main())
