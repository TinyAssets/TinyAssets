"""Video extractor -- ffmpeg keyframe extraction + image pipeline.

Handles: .mp4, .mov, .avi, .webm, .mkv

Pipeline:
1. Extract keyframes via ffmpeg (1 per 10 seconds, max 10 frames)
2. Feed each frame through the image extractor for description
3. Concatenate descriptions with timestamps into a visual reference doc

Graceful fallback: if ffmpeg is not found, returns a placeholder
description.  Never crashes.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
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

    Writes the video to a temp file, extracts keyframes via ffmpeg,
    describes each frame via the image extractor, and concatenates
    the descriptions with timestamps.

    Parameters
    ----------
    filename : str
        Video filename.
    data : bytes
        Raw video bytes.
    premise : str
        Story premise for context in vision prompts.

    Returns
    -------
    str
        Text description of the video (one section per keyframe),
        or a placeholder if ffmpeg is unavailable.
    """
    from tinyassets import role_decoder
    from tinyassets.broker.supervisor import broker_selected

    if role_decoder._bounded_client is not None or broker_selected():
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
        return (f'# Visual Reference: {filename}\n\n'
                f'Video duration: {_format_timestamp(int(duration))} | '
                f'Frames analyzed: {len(images)}\n\n---\n\n'
                + '\n\n---\n\n'.join(descriptions))
    ffmpeg_path = _find_ffmpeg()
    if not ffmpeg_path:
        return _placeholder_description(filename, data)

    try:
        return _extract_with_ffmpeg(
            ffmpeg_path, filename, data, premise=premise,
        )
    except Exception as e:
        logger.warning("Video extraction failed for %s: %s", filename, e)
        return _placeholder_description(filename, data)


def _find_ffmpeg() -> str:
    """Find the ffmpeg binary on the system PATH.

    Returns the path to ffmpeg, or empty string if not found.
    """
    path = shutil.which("ffmpeg")
    if path:
        logger.debug("Found ffmpeg at %s", path)
        return path
    logger.info("ffmpeg not found on PATH; video extraction unavailable")
    return ""


def _get_video_duration(ffmpeg_path: str, video_path: str) -> float:
    """Get video duration in seconds using ffprobe or ffmpeg.

    Returns 0.0 if duration cannot be determined.
    """
    # Try ffprobe first (more reliable)
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path:
        try:
            result = subprocess.run(
                [
                    ffprobe_path,
                    "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    video_path,
                ],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass

    # Fallback: parse ffmpeg stderr output
    try:
        result = subprocess.run(
            [ffmpeg_path, "-i", video_path],
            capture_output=True, text=True, timeout=30,
        )
        import re
        match = re.search(
            r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)",
            result.stderr,
        )
        if match:
            h, m, s, cs = match.groups()
            return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100
    except (subprocess.TimeoutExpired, OSError):
        pass

    return 0.0


def _extract_with_ffmpeg(
    ffmpeg_path: str,
    filename: str,
    data: bytes,
    *,
    premise: str = "",
) -> str:
    """Extract keyframes and describe them.

    Writes video to temp dir, runs ffmpeg to extract frames,
    then feeds each frame through the image extractor.
    """
    from tinyassets.ingestion.image_extractor import (
        extract_image_description,
    )
    from tinyassets.universe_files import read_universe_file, write_universe_file

    with tempfile.TemporaryDirectory(prefix="fa_video_") as tmpdir:
        tmp = Path(tmpdir)
        video_file = tmp / 'source'
        write_universe_file(tmp, 'source', data)

        # Get video duration to calculate frame count
        duration = _get_video_duration(ffmpeg_path, str(video_file))
        if duration <= 0:
            # Unknown duration -- extract a few frames anyway
            n_frames = min(MAX_FRAMES, 5)
            fps_filter = f"fps=1/{FRAME_INTERVAL_SECONDS}"
        else:
            n_frames = min(
                MAX_FRAMES,
                max(1, int(duration / FRAME_INTERVAL_SECONDS)),
            )
            fps_filter = f"fps=1/{FRAME_INTERVAL_SECONDS}"

        # Extract frames
        frame_pattern = str(tmp / "frame_%03d.png")
        cmd = [
            ffmpeg_path,
            "-i", str(video_file),
            "-vf", f"{fps_filter},scale='min(1024,iw):-1'",
            "-frames:v", str(n_frames),
            "-y",  # overwrite
            frame_pattern,
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.warning(
                    "ffmpeg exited with code %d: %s",
                    result.returncode, result.stderr[:500],
                )
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg timed out extracting frames from %s", filename)
            return _placeholder_description(filename, data)

        # Collect extracted frame files
        frame_files = sorted(tmp.glob("frame_*.png"))
        if not frame_files:
            logger.warning("No frames extracted from %s", filename)
            return _placeholder_description(filename, data)

        # Describe each frame
        descriptions: list[str] = []
        for i, frame_file in enumerate(frame_files):
            timestamp = i * FRAME_INTERVAL_SECONDS
            ts_str = _format_timestamp(timestamp)
            frame_data = read_universe_file(tmp, frame_file.name)
            frame_name = f"{filename}_frame_{i:03d}.png"

            desc = extract_image_description(
                frame_name, frame_data, premise=premise,
            )
            descriptions.append(
                f"## [{ts_str}] Frame {i + 1}\n\n{desc}"
            )

            logger.debug(
                "Described frame %d/%d from %s (%d chars)",
                i + 1, len(frame_files), filename, len(desc),
            )

    # Assemble final document
    header = (
        f"# Visual Reference: {filename}\n\n"
        f"Video duration: {_format_timestamp(int(duration))} | "
        f"Frames analyzed: {len(descriptions)}\n\n"
        f"---\n"
    )
    body = "\n\n---\n\n".join(descriptions)

    result_text = f"{header}\n{body}"
    logger.info(
        "Video extraction complete: %s, %d frames, %d chars",
        filename, len(descriptions), len(result_text),
    )
    return result_text


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


def _placeholder_description(filename: str, data: bytes) -> str:
    """Generate a placeholder when ffmpeg is unavailable."""
    size_mb = len(data) / (1024 * 1024)
    ext = Path(filename).suffix.lower()
    return (
        f"[Video awaiting frame analysis]\n\n"
        f"Video file: {filename}\n"
        f"Size: {size_mb:.1f} MB\n"
        f"Format: {ext}\n\n"
        f"This video has been stored in canon/sources/ but could not be "
        f"analyzed because ffmpeg is not installed. Install ffmpeg and "
        f"re-ingest to extract visual reference frames."
    )
