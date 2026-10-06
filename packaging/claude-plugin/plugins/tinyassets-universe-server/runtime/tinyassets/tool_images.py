"""An image a tool reads, bounded so it can be shown to the model.

The harness ``read`` tool shows an image file to the model the way pi.dev's does,
so the agent can look at what it made: art it rendered, a screenshot of a UI it
built (founder's village, 2026-10-02: the agent had no way to see its screen).
Every tool surface that reads files -- the engine-MCP ``read`` and the thin agent
loop's box ``read`` -- goes through :func:`bound_image`, so the bound is one rule.

The bytes are untrusted (anything in the agent's folder), and the daemon decoding
them is shared by every command center, so nothing about them is taken on trust:

* **The format** comes from the magic bytes, never the extension, and Pillow is
  told to use exactly that decoder (``formats=``) and checked afterwards -- its
  plugin probing would otherwise try every format it knows.
* **The canvas** is read from the header by this module, before Pillow sees the
  file: libwebp allocates two full canvases while merely OPENING an animated
  WebP, and a 1 KB PNG can declare a gigapixel image (Codex, 2026-10-02).
* **The decode** runs in a short-lived child process with a memory, CPU and wall
  limit (``python -m tinyassets.tool_images``), at most two at a time, so even a
  decoder pathology the header check does not foresee costs a bounded child, not
  the daemon.
* **Everything is re-encoded** from fully decoded pixels -- never passed through --
  with EXIF orientation applied and metadata dropped, at most ``MAX_EDGE`` pixels
  on the long edge and ``MAX_IMAGE_BYTES`` encoded. Transparency is kept: an image
  too large as PNG is scaled down further, not flattened.
"""

from __future__ import annotations

import base64
import io
import json
import struct
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Extensions ``read`` treats as images; everything else stays text.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
#: Raw bytes a reader may take from the file before bounding (the jail's output
#: cap for an image read).
MAX_IMAGE_SOURCE_BYTES = 20 * 1024 * 1024
#: Pixels decoded at most, by the header's own declaration.
MAX_SOURCE_PIXELS = 25_000_000
#: Long edge shown to the model; larger images are scaled down to it.
MAX_EDGE = 1568
#: Encoded size shown to the model.
MAX_IMAGE_BYTES = 1_500_000
#: The decoding child: address space, CPU seconds, wall clock.
DECODE_MEMORY_BYTES = 1024 * 1024 * 1024
DECODE_CPU_SECONDS = 15
DECODE_WALL_SECONDS = 30.0
#: Decodes running at once in this process; a third waits briefly, then refuses.
_DECODE_SLOTS = threading.BoundedSemaphore(2)

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
#: The Pillow decoder each sniffed type may use, and the formats it may report
#: (a JPEG with an MPF segment opens as Pillow's MPO, which is still JPEG data).
_PILLOW = {
    "image/png": (["PNG"], {"PNG"}),
    "image/jpeg": (["JPEG"], {"JPEG", "MPO"}),
    "image/gif": (["GIF"], {"GIF"}),
    "image/webp": (["WEBP"], {"WEBP"}),
}


def is_image_path(path: str) -> bool:
    return str(path or "").lower().endswith(IMAGE_SUFFIXES)


def sniff(data: bytes) -> str | None:
    """The image type the bytes ARE, whatever the file is called."""
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def declared_size(data: bytes, mime: str) -> tuple[int, int] | None:
    """The canvas the header declares, read without any decoder; None if the
    header is not one this module can read (refused, never guessed)."""
    try:
        if mime == "image/png":
            if data[12:16] != b"IHDR":
                return None
            return struct.unpack(">II", data[16:24])
        if mime == "image/gif":
            return struct.unpack("<HH", data[6:10])
        if mime == "image/webp":
            chunk = data[12:16]
            if chunk == b"VP8X":
                width = int.from_bytes(data[24:27], "little") + 1
                height = int.from_bytes(data[27:30], "little") + 1
                return width, height
            if chunk == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if chunk == b"VP8 ":
                width, height = struct.unpack("<HH", data[26:30])
                return width & 0x3FFF, height & 0x3FFF
            return None
        if mime == "image/jpeg":
            return _jpeg_size(data)
    except (struct.error, IndexError):
        return None
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """The largest frame any SOF marker declares (a file may carry several)."""
    position, found = 2, None
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            return found
        marker = data[position + 1]
        if marker == 0xFF:
            position += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            position += 2
            continue
        if marker in (0xD9, 0xDA):
            return found
        length = struct.unpack(">H", data[position + 2:position + 4])[0]
        if marker in sof:
            height, width = struct.unpack(">HH", data[position + 5:position + 9])
            if found is None or width * height > found[0] * found[1]:
                found = (width, height)
        position += 2 + length
    return found


@dataclass(frozen=True)
class ToolImage:
    """A bounded image plus the one line of text that accompanies it."""

    text: str
    mime_type: str
    data: bytes
    width: int
    height: int

    @property
    def base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")

    def content_blocks(self) -> list[Any]:
        """The MCP content of a tool result: the line, then the image."""
        from mcp.types import ImageContent, TextContent

        return [TextContent(type="text", text=self.text),
                ImageContent(type="image", data=self.base64, mimeType=self.mime_type)]

    def tool_result(self) -> Any:
        """A FastMCP ``ToolResult``; the tool must be registered without an
        output schema, or the client reports an image result as an error."""
        from fastmcp.tools.base import ToolResult

        return ToolResult(content=self.content_blocks())


def bound_image(data: bytes, path: str = "", *,
                universe_dir: Path | None = None) -> ToolImage | str:
    """``data`` as a :class:`ToolImage` the model can take, or a refusal line."""
    name = path or "image"
    if len(data) > MAX_IMAGE_SOURCE_BYTES:
        return f"error: {name} is over {MAX_IMAGE_SOURCE_BYTES} bytes; too large to show"
    mime = sniff(data)
    if mime is None:
        return (f"error: {name} is not a PNG, JPEG, GIF or WebP image "
                "(its bytes say otherwise); read it with bash if it is something else")
    size = declared_size(data, mime)
    if size is None or size[0] < 1 or size[1] < 1:
        return f"error: {name} has no readable {mime} header"
    width, height = size
    if width * height > MAX_SOURCE_PIXELS:
        return (f"error: {name} is {width}x{height} pixels, over the "
                f"{MAX_SOURCE_PIXELS} pixel bound; scale it down first")
    if not _DECODE_SLOTS.acquire(timeout=DECODE_WALL_SECONDS):
        return f"error: {name} could not be shown now: image decoding is busy, try again"
    try:
        from tinyassets.broker.supervisor import broker_selected

        answer = _decode_in_child(data, mime, **(
            {"universe_dir": universe_dir} if broker_selected() else {}))
    finally:
        _DECODE_SLOTS.release()
    if isinstance(answer, str):
        return f"error: {name} {answer}"
    meta, encoded = answer
    note = (f"{name}: {meta['source_width']}x{meta['source_height']} {mime}, shown as "
            f"{meta['width']}x{meta['height']} {meta['mime_type']}"
            + (" (first frame)" if meta["animated"] else ""))
    return ToolImage(note, meta["mime_type"], encoded, meta["width"], meta["height"])


def _limit_self(memory_bytes: int, cpu_seconds: int) -> None:
    """Child side, first thing: cap this process before it reads a byte.

    Applied in the fresh interpreter, never as a ``preexec_fn``: that runs
    between fork and exec in a threaded daemon, where Python documents it can
    deadlock -- before the parent's wall clock even starts (Codex, 2026-10-02).
    """
    try:
        import resource
    except ImportError:  # Windows dev hosts: the wall clock is the only bound
        return
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _decode_in_child(data: bytes, mime: str, *, universe_dir=None) -> tuple[dict, bytes] | str:
    from tinyassets.broker.supervisor import broker_selected

    try:
        if broker_selected():
            from tinyassets.role_decoder import decode

            done = decode(data, mime, universe_dir)
        else:
            done = subprocess.run(
                [sys.executable, "-m", "tinyassets.tool_images", mime,
                 str(DECODE_MEMORY_BYTES), str(DECODE_CPU_SECONDS)],
                input=data, capture_output=True, timeout=DECODE_WALL_SECONDS,
                cwd=str(Path(__file__).resolve().parents[1]),
            )
    except (subprocess.TimeoutExpired, TimeoutError):
        return f"took longer than {DECODE_WALL_SECONDS:.0f} s to decode"
    except (OSError, RuntimeError, ValueError):
        if broker_selected():
            return "could not enter the admitted image decoder cell"
        raise
    head, _, body = done.stdout.partition(b"\n")
    try:
        meta = json.loads(head.decode("utf-8")) if head else {}
    except ValueError:
        meta = {}
    if done.returncode != 0 or "error" in meta or "mime_type" not in meta:
        return meta.get("error") or "could not be decoded within the decoder's limits"
    if len(body) != meta.get("bytes"):
        return "could not be decoded: the decoder's answer was cut short"
    return meta, body


def _shown(data: bytes, mime: str) -> tuple[dict, bytes]:
    """Child side: decode with exactly one decoder, orient, scale, re-encode."""
    from PIL import Image, ImageOps

    from tinyassets.image_bytes import open_image_bytes

    formats, allowed = _PILLOW[mime]
    Image.MAX_IMAGE_PIXELS = MAX_SOURCE_PIXELS
    with open_image_bytes(data, formats=formats) as image:
        if image.format not in allowed:
            raise ValueError(f"opened as {image.format}, not {mime}")
        source = image.size
        if source[0] * source[1] > MAX_SOURCE_PIXELS:
            raise ValueError(f"is {source[0]}x{source[1]} pixels once opened")
        animated = bool(getattr(image, "is_animated", False))
        # An APNG may carry a poster image that is not part of the animation;
        # Pillow exposes it as frame 0. Show the animation's first frame.
        image.seek(1 if animated and getattr(image, "default_image", False) else 0)
        if mime == "image/jpeg":
            image.draft("RGB", (MAX_EDGE, MAX_EDGE))  # decode at a reduced scale
        image.load()  # every pixel, so a corrupt stream fails HERE, not later
        frame = ImageOps.exif_transpose(image)
        alpha = frame.mode in ("RGBA", "LA", "PA") or "transparency" in frame.info
        icc = frame.info.get("icc_profile")
        frame = _to_srgb(frame, icc, "RGBA" if alpha else "RGB")
        frame.info = {}  # no metadata leaves: ICC, EXIF, text chunks, comments
    # The source size as the person sees it: EXIF orientation may turn it.
    turned = (frame.size[0] >= frame.size[1]) != (source[0] >= source[1])
    oriented = (source[1], source[0]) if turned else source
    scale = min(1.0, MAX_EDGE / max(frame.size))
    for _attempt in range(8):
        target = (max(1, round(frame.size[0] * scale)), max(1, round(frame.size[1] * scale)))
        shown = frame if target == frame.size else frame.resize(target, Image.LANCZOS)
        encoded, out_mime = _encode(shown, alpha)
        if encoded is not None:
            return ({"mime_type": out_mime, "width": shown.size[0], "height": shown.size[1],
                     "source_width": oriented[0], "source_height": oriented[1],
                     "animated": animated, "bytes": len(encoded)}, encoded)
        scale *= 0.75  # keep transparency: smaller, never flattened
    raise ValueError(f"is still over {MAX_IMAGE_BYTES} bytes after scaling")


def _to_srgb(frame: Any, icc: bytes | None, mode: str) -> Any:
    """``frame`` in ``mode`` (RGB/RGBA), its embedded colour profile applied from
    its OWN colour mode (a CMYK profile needs CMYK pixels), so dropping the
    profile does not change how the image looks. A profile that cannot be
    applied falls back to a plain conversion."""
    if icc:
        try:
            from PIL import ImageCms

            source = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            native = frame if frame.mode in ("RGB", "RGBA", "CMYK", "L") else frame.convert(mode)
            if native.mode == "RGBA" or mode == "RGBA":
                native = native.convert("RGBA")
            return ImageCms.profileToProfile(native, source, ImageCms.createProfile("sRGB"),
                                             outputMode=mode)
        except Exception:  # noqa: BLE001 - a broken profile is ignored, not fatal
            pass
    return frame.convert(mode)


def _encode(image: Any, alpha: bool) -> tuple[bytes | None, str]:
    buffer = io.BytesIO()
    if alpha:
        image.save(buffer, "PNG", optimize=True)
        return (buffer.getvalue() if buffer.tell() <= MAX_IMAGE_BYTES else None), "image/png"
    image.save(buffer, "PNG", optimize=True)
    if buffer.tell() <= MAX_IMAGE_BYTES and buffer.tell() <= 400_000:
        return buffer.getvalue(), "image/png"  # small and lossless: keep it exact
    for quality in (85, 70, 50):
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality)
        if buffer.tell() <= MAX_IMAGE_BYTES:
            return buffer.getvalue(), "image/jpeg"
    return None, "image/jpeg"


__all__ = [
    "IMAGE_SUFFIXES",
    "MAX_EDGE",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_SOURCE_BYTES",
    "MAX_SOURCE_PIXELS",
    "ToolImage",
    "bound_image",
    "declared_size",
    "is_image_path",
    "sniff",
]


def main() -> int:  # pragma: no cover - exercised through bound_image
    args = sys.argv[1:]
    if len(args) != 3 or not args[1].isdigit() or not args[2].isdigit():
        sys.stdout.buffer.write(json.dumps({"error": "decoder misconfigured"}).encode() + b"\n")
        return 2
    _limit_self(int(args[1]), int(args[2]))
    mime = args[0]
    data = sys.stdin.buffer.read()
    out = sys.stdout.buffer
    if mime not in _PILLOW:
        out.write(json.dumps({"error": "is not a supported image type"}).encode() + b"\n")
        return 2
    try:
        meta, encoded = _shown(data, mime)
    except MemoryError:
        out.write(json.dumps({"error": "needs more memory than the decoder may use"})
                  .encode() + b"\n")
        return 3
    except Exception as exc:  # noqa: BLE001 - any decoder failure is a refusal line
        message = f"could not be decoded as {mime}: {str(exc)[:200]}"
        out.write(json.dumps({"error": message}).encode() + b"\n")
        return 3
    out.write(json.dumps(meta).encode() + b"\n" + encoded)
    out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
