"""Fixed scratch-only ffprobe/ffmpeg entry, imported after owner confinement."""
from __future__ import annotations

import json
import math
import os
import resource
import subprocess
import sys

from tinyassets.role_video import MAX_FRAME_BYTES, MAX_FRAMES, MAX_SOURCE_BYTES


def cell_main():
    for limit, cap in ((resource.RLIMIT_AS, 1024 ** 3), (resource.RLIMIT_CPU, 130),
                       (resource.RLIMIT_CORE, 0), (resource.RLIMIT_NOFILE, 64),
                       (resource.RLIMIT_NPROC, 32), (resource.RLIMIT_FSIZE, MAX_SOURCE_BYTES)):
        resource.setrlimit(limit, (cap, cap))
    data = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
    if not 0 < len(data) <= MAX_SOURCE_BYTES:
        raise ValueError('invalid video input size')
    os.mkdir('/tmp/video', 0o700)
    os.chdir('/tmp/video')
    with open('source', 'xb') as target:
        target.write(data)
    del data
    # No input name, demuxer options or executable is supplied by the caller.
    # A playlist may address only files in the already-confined filesystem.
    with open('duration', 'xb') as duration_file:
        probe = subprocess.run(['/usr/bin/ffprobe', '-v', 'quiet', '-threads', '1',
            '-protocol_whitelist', 'file,pipe', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', 'source'],
            stdin=subprocess.DEVNULL, stdout=duration_file, stderr=subprocess.DEVNULL,
            close_fds=True, timeout=25, check=False)
    with open('duration', 'rb') as duration_file:
        raw = duration_file.read(257)
    if probe.returncode or len(raw) > 256:
        raise RuntimeError('video metadata extraction failed')
    duration = float(raw)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError('invalid video duration')
    count = min(MAX_FRAMES, max(1, int(duration / 10)))
    subprocess.run(['/usr/bin/ffmpeg', '-v', 'error', '-nostdin', '-threads', '1',
        '-protocol_whitelist', 'file,pipe', '-i', 'source', '-an', '-sn', '-dn',
        '-vf', "fps=1/10,scale='min(1024,iw)':-1", '-filter_threads', '1',
        '-threads', '1', '-frames:v', str(count), '-y', 'frame_%03d.png'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, timeout=120, check=True)
    frames = []
    for index in range(1, count + 1):
        try:
            with open(f'frame_{index:03d}.png', 'rb') as frame_file:
                frame = frame_file.read(MAX_FRAME_BYTES + 1)
        except FileNotFoundError:
            break
        if not 0 < len(frame) <= MAX_FRAME_BYTES:
            raise ValueError('video frame exceeds its bound')
        frames.append(frame)
    if not frames:
        raise RuntimeError('video yielded no frames')
    sys.stdout.buffer.write(json.dumps(dict(duration=duration,
        sizes=[len(frame) for frame in frames])).encode() + b'\n')
    for frame in frames:
        sys.stdout.buffer.write(frame)
    return 0
