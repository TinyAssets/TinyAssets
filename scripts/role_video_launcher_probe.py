"""Actual installed ffmpeg extraction under two owner identities, synthetic bytes only."""
from __future__ import annotations

import argparse
import subprocess

from role_service_bootstrap_probe import CONTAINER

SETUP = r'''
video_path=root/'oracle-source.mkv'
subprocess.run(['/usr/bin/ffmpeg','-v','error','-f','lavfi','-i',
    'color=c=blue:s=32x32:r=1:d=20','-threads','1','-c:v','mpeg4',
    '-f','matroska',str(video_path)],capture_output=True,check=True)
video=video_path.read_bytes()
assert len(video)>100
'''

VIDEO = r'''
from tinyassets.ingestion.extractors import extract_text
from tinyassets.role_video import frames
for owner in identities:
    center=root/('decoder-'+owner)
    with identity_context(Identity(owner,owner)):
        captured=[]
        def describe(name,data,*,premise):
            assert Image.open(io.BytesIO(data)).size==(32,32)
            captured.append((owner,name))
            return owner+' frame description'
        text=extract_text('../../source.mkv',video,universe_dir=center,describe_frame=describe)
        assert 'Frames analyzed: 2' in text and owner+' frame description' in text
        assert len(captured)==2
        other='bob' if owner=='alice' else 'alice'
        try: frames(video,root/('decoder-'+other))
        except PermissionError: pass
        else: raise AssertionError('foreign application scope admitted')
        for malformed in (b'', b'not-a-video',
                b'#EXTM3U\n#EXTINF:10,\n/data/bob/.credential-vault.json\n'):
            try: frames(malformed,center)
            except (ValueError,RuntimeError): pass
            else: raise AssertionError('invalid or foreign playlist accepted')
        duration,images=frames(video,center)
        assert duration==20 and len(images)==2
        print(owner+': actual ffprobe/ffmpeg and application callback PASS; '
              'foreign scope/input paths DENIED; post-refusal reuse PASS',flush=True)
print('ingestion-video: ZERO FOREIGN_BYTES',flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    marker = "if os.environ.get('TA_ORACLE_HTTPS')=='1':\n    stream['probe'](root)"
    setup_marker = "run=Path(tempfile.mkdtemp(prefix='role-services-',dir='/run'))"
    assert CONTAINER.count(marker) == CONTAINER.count(setup_marker) == 1
    script = ('DEATH=""\nFAIL=False\nGIT=False\nSNAPSHOTS=False\n'
              + CONTAINER.replace(marker, VIDEO + '\n' + marker)
                         .replace(setup_marker, SETUP + '\n' + setup_marker))
    command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--user', '0:0',
               '--cap-drop', 'ALL']
    for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID', 'SETPCAP', 'KILL'):
        command += ['--cap-add', cap]
    for option in ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
                   'systempaths=unconfined'):
        command += ['--security-opt', option]
    command += ['--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
    print('production image:', digest, flush=True)
    return subprocess.run(command, input=script, text=True, timeout=360).returncode


if __name__ == '__main__':
    raise SystemExit(main())
