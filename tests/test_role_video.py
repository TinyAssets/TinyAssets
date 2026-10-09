"""Selected video admission, bounded parser transport and refusal regressions."""
import json

import pytest

from tinyassets import role_decoder, role_video
from tinyassets.ingestion import extractors, video_extractor

# These drive the real bounded launcher and cells; no double is installed.
pytestmark = pytest.mark.role_split


def test_selected_video_cannot_fallback_to_daemon(monkeypatch, tmp_path):
    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    # There is no daemon-side ffmpeg left to fall back to.
    assert not hasattr(video_extractor, '_find_ffmpeg')
    assert not hasattr(video_extractor, '_placeholder_description')
    with pytest.raises(RuntimeError, match='owner-scoped vision'):
        video_extractor.extract_video_description('x.mp4', b'video')
    def refused(*args, **kwargs):
        raise PermissionError('foreign owner')
    monkeypatch.setattr(role_video, 'frames', refused)
    with pytest.raises(PermissionError, match='foreign owner'):
        extractors.extract_text('x.mp4', b'video', universe_dir=tmp_path,
                                describe_frame=lambda *a, **k: 'unused')


def test_selected_video_routes_scope_and_verbatim_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(role_decoder, '_bounded_client', object())
    payload = b'\x00video\xff'
    def frames(data, center):
        assert data is payload and center == tmp_path
        return 20, [b'png-one', b'png-two']
    monkeypatch.setattr(role_video, 'frames', frames)
    calls = []
    def describe(name, data, *, premise):
        calls.append((name, data, premise))
        return 'Owner description'
    result = extractors.extract_text('source.mp4', payload, universe_dir=tmp_path,
                                     describe_frame=describe)
    assert '0:10' in result and 'Frames analyzed: 2' in result
    assert calls == [('source.mp4_frame_000.png', b'png-one', ''),
                     ('source.mp4_frame_001.png', b'png-two', '')]


@pytest.mark.parametrize('meta,content', [
    ({'duration': float('nan'), 'sizes': [8]}, b'\x89PNG\r\n\x1a\n'),
    ({'duration': -1, 'sizes': [8]}, b'\x89PNG\r\n\x1a\n'),
    ({'duration': 1, 'sizes': [True]}, b'x'),
    ({'duration': 1, 'sizes': []}, b''),
    ({'duration': 1, 'sizes': [8]}, b'\x89PNG\r\n\x1a\nextra'),
    ({'duration': 1, 'sizes': [8]}, b'not-png!'),
])
def test_untrusted_video_response_is_bounded(meta, content):
    with pytest.raises(ValueError):
        role_video.parse_result(json.dumps(meta).encode() + b'\n' + content)


def test_video_frame_transport_preserves_bytes():
    png = b'\x89PNG\r\n\x1a\n\x00\xff'
    payload = json.dumps({'duration': 10, 'sizes': [len(png)]}).encode() + b'\n' + png
    assert role_video.parse_result(payload) == (10, [png])
