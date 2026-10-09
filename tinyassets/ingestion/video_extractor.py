"""Video extractor -- keyframe extraction in the owner's video cell.

Handles: .mp4, .mov, .avi, .webm, .mkv

Pipeline:
1. Extract keyframes in the owner's ingestion-video cell (ffprobe/ffmpeg run
   as the owner, never as the daemon) -- 1 per 10 seconds, max 10 frames
2. Feed each frame through the caller's owner-scoped vision callback
3. Concatenate descriptions with timestamps into a visual reference doc

There is no daemon-uid fallback: ffmpeg never runs outside the cell, and a
cell that cannot be entered raises rather than returning a placeholder that
reads like a real description.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Maximum number of frames to extract from a video.
MAX_FRAMES = 10

# Extract one frame every N seconds.
FRAME_INTERVAL_SECONDS = 10


def extract_video_description(
    filename: str,
    data: bytes,
    *,
    premise: str = "",
    universe_dir: Path | None = None,
    describe_frame=None,
) -> str:
    """Extract a text description from a video file.

    The bytes go to the owner's video cell, which returns the duration and the
    PNG keyframes; each frame is then described through *describe_frame*, the
    caller's owner-scoped vision callback.

    Parameters
    ----------
    filename : str
        Video filename.
    data : bytes
        Raw video bytes.
    premise : str
        Story premise for context in vision prompts.
    universe_dir : Path
        The admitted command center the cell runs for.
    describe_frame : callable
        ``(name, png_bytes, premise=...) -> str``; required.

    Returns
    -------
    str
        Text description of the video, one section per keyframe.
    """
    from tinyassets.role_video import frames

    if not callable(describe_frame):
        raise RuntimeError(
            'selected video description requires an owner-scoped vision callback')
    duration, images = frames(data, universe_dir)
    descriptions = [
        f'## [{_format_timestamp(index * FRAME_INTERVAL_SECONDS)}] Frame {index + 1}\n\n'
        + describe_frame(f'{filename}_frame_{index:03d}.png', image, premise=premise)
        for index, image in enumerate(images)
    ]
    logger.info("Video extraction complete: %s, %d frames", filename, len(images))
    return (f'# Visual Reference: {filename}\n\n'
            f'Video duration: {_format_timestamp(int(duration))} | '
            f'Frames analyzed: {len(images)}\n\n---\n\n'
            + '\n\n---\n\n'.join(descriptions))


def _format_timestamp(seconds: int) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    if seconds < 0:
        seconds = 0
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
