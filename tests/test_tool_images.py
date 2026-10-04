"""The harness `read` shows an image to the model, bounded (pi's read behaviour).

The agent could not see what it made -- art it rendered, a screenshot of a UI
it built (founder's village, 2026-10-02). These pin the one shared rule every
file-reading tool uses (tinyassets.tool_images) and the engine `read` returning
it as image content through the real MCP middleware stack.
"""

from __future__ import annotations

import asyncio
import io
import struct
import zlib
from pathlib import Path

import pytest
from PIL import Image

from tests.engine_authority_helpers import mock_engine_admission
from tinyassets import tool_images, universe_tools
from tinyassets.tool_images import ToolImage, bound_image
from tinyassets.universe_tools import ToolRun


def _encode(image: Image.Image, fmt: str, **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def _png(width: int, height: int, color=(40, 120, 200)) -> bytes:
    return _encode(Image.new("RGB", (width, height), color), "PNG")


def _shown(result: ToolImage) -> Image.Image:
    return Image.open(io.BytesIO(result.data))


# --------------------------------------------------------------------------- #
# the shared bound
# --------------------------------------------------------------------------- #


def test_a_small_image_is_shown_at_its_size_with_exact_pixels():
    data = _png(64, 32)
    shown = bound_image(data, "art/tile.png")
    assert isinstance(shown, ToolImage)
    assert shown.mime_type == "image/png"
    assert (shown.width, shown.height) == (64, 32)
    assert shown.text == "art/tile.png: 64x32 image/png, shown as 64x32 image/png"
    # Re-encoded from decoded pixels, never passed through -- and lossless.
    assert _shown(shown).convert("RGB").getpixel((10, 10)) == (40, 120, 200)


def test_a_large_image_is_scaled_to_the_long_edge():
    shown = bound_image(_png(4000, 2000), "shot.png")
    assert isinstance(shown, ToolImage)
    assert (shown.width, shown.height) == (tool_images.MAX_EDGE, tool_images.MAX_EDGE // 2)
    assert _shown(shown).size == (shown.width, shown.height)
    assert len(shown.data) <= tool_images.MAX_IMAGE_BYTES
    assert "4000x2000 image/png, shown as 1568x784 image/" in shown.text


def test_transparency_survives_scaling_and_noise_is_squeezed_under_the_byte_bound():
    import random

    rgba = Image.new("RGBA", (2000, 2000), (0, 0, 0, 0))
    shown = bound_image(_encode(rgba, "PNG"), "sprite.png")
    assert isinstance(shown, ToolImage) and shown.mime_type == "image/png"
    assert _shown(shown).mode == "RGBA"
    # Noisy RGBA does not fit as PNG at 1568 px: it is scaled further, never
    # flattened to JPEG (Codex, 2026-10-02).
    noisy = Image.frombytes("RGBA", (1600, 1600), random.Random(1).randbytes(1600 * 1600 * 4))
    kept = bound_image(_encode(noisy, "PNG"), "noisy-sprite.png")
    assert isinstance(kept, ToolImage) and kept.mime_type == "image/png", kept
    assert _shown(kept).mode == "RGBA" and kept.width < 1568
    assert len(kept.data) <= tool_images.MAX_IMAGE_BYTES
    import random

    noise = Image.frombytes("RGB", (1500, 1500), random.Random(0).randbytes(1500 * 1500 * 3))
    loud = bound_image(_encode(noise, "PNG"), "noise.png")
    assert isinstance(loud, ToolImage), loud
    assert len(loud.data) <= tool_images.MAX_IMAGE_BYTES


@pytest.mark.parametrize("fmt, mime", [("JPEG", "image/jpeg"), ("WEBP", "image/webp"),
                                       ("GIF", "image/gif")])
def test_each_supported_format_is_recognised_by_its_bytes(fmt, mime):
    data = _encode(Image.new("RGB", (40, 30), (200, 10, 10)), fmt)
    shown = bound_image(data, "x." + fmt.lower())
    assert isinstance(shown, ToolImage) and f"40x30 {mime}, shown as 40x30" in shown.text


def test_an_animated_gif_shows_its_first_frame():
    frames = [Image.new("RGB", (20, 20), c) for c in ((255, 0, 0), (0, 255, 0))]
    data = _encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=50)
    shown = bound_image(data, "walk.gif")
    assert isinstance(shown, ToolImage) and "(first frame)" in shown.text
    red, green, _ = _shown(shown).convert("RGB").getpixel((5, 5))
    assert red > 240 and green < 16, "the first frame, not the second"


def test_a_declared_giant_canvas_is_refused_before_it_is_decoded():
    """A few hundred bytes can declare a gigapixel PNG; decoding it would take
    the daemon's memory, so the header is checked first."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    bomb = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 40000, 40000, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b""))
    assert len(bomb) < 200
    refused = bound_image(bomb, "bomb.png")
    assert isinstance(refused, str) and "pixel bound" in refused, refused
    # Between Pillow's own guard and ours, the header check is what refuses.
    mid = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 6000, 6000, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) + chunk(b"IEND", b""))
    refused = bound_image(mid, "mid.png")
    assert isinstance(refused, str) and "6000x6000" in refused, refused


def test_the_extension_is_not_trusted():
    refused = bound_image(b"#!/bin/sh\necho not an image\n", "evil.png")
    assert isinstance(refused, str) and "its bytes say otherwise" in refused
    refused = bound_image(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "x.png")
    assert isinstance(refused, str)


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_a_truncated_image_is_refused_even_when_small(fmt):
    """Codex 2026-10-02: `verify()` passed a small JPEG whose scan was cut; every
    pixel is now decoded before anything is shown."""
    import random

    noise = Image.frombytes("RGB", (64, 64), random.Random(2).randbytes(64 * 64 * 3))
    data = _encode(noise, fmt)
    refused = bound_image(data[: len(data) * 2 // 3], "cut." + fmt.lower())
    assert isinstance(refused, str) and "could not be decoded" in refused, refused


def test_jpeg_magic_cannot_reach_another_decoder():
    """Codex 2026-10-02: Pillow probes every plugin; a JPEG prefix with a PCD
    header at 2048 could open as PCD. The sniffed type's decoder is the only one."""
    polyglot = bytearray(b"\xff\xd8\xff\x02" + b"\x00" * 4096)
    polyglot[2048:2055] = b"PCD_IPI"
    refused = bound_image(bytes(polyglot), "pcd.jpg")
    assert isinstance(refused, str), refused


def test_exif_orientation_is_applied():
    image = Image.new("RGB", (40, 20), (0, 200, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 CW to display
    shown = bound_image(_encode(image, "JPEG", exif=exif.tobytes()), "photo.jpg")
    assert isinstance(shown, ToolImage)
    assert (shown.width, shown.height) == (20, 40)
    assert _shown(shown).getexif().get(0x0112) is None, "metadata is dropped"


@pytest.mark.parametrize("mime, header, size", [
    ("image/gif", b"GIF89a" + (5000).to_bytes(2, "little") + (6000).to_bytes(2, "little"),
     (5000, 6000)),
])
def test_declared_size_is_read_without_a_decoder(mime, header, size):
    assert tool_images.declared_size(header + b"\x00" * 16, mime) == size
    refused = bound_image(header + b"\x00" * 16, "big.gif")
    assert isinstance(refused, str) and "5000x6000" in refused


def test_a_webp_canvas_is_refused_before_pillow_opens_it():
    """libwebp allocates two full canvases while opening an animated WebP; the
    VP8X header is read first (Codex, 2026-10-02)."""
    vp8x = (b"RIFF" + (30).to_bytes(4, "little") + b"WEBP" + b"VP8X"
            + (10).to_bytes(4, "little") + b"\x02\x00\x00\x00"
            + (9999).to_bytes(3, "little") + (9999).to_bytes(3, "little"))
    assert tool_images.declared_size(vp8x, "image/webp") == (10000, 10000)
    refused = bound_image(vp8x, "anim.webp")
    assert isinstance(refused, str) and "10000x10000" in refused


# --------------------------------------------------------------------------- #
# universe_tools.read_file: the jailed read
# --------------------------------------------------------------------------- #


class _Spy:
    def __init__(self, run: ToolRun) -> None:
        self.calls: list[dict] = []
        self._run = run

    def __call__(self, universe_dir, inner, **kwargs):
        self.calls.append({"inner": list(inner), **kwargs})
        return self._run


def _universe(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "u-alpha"
    path.mkdir(parents=True)
    return path


def test_an_image_is_read_whole_inside_the_jail_with_its_own_output_cap(tmp_path, monkeypatch):
    data = _png(10, 10)
    spy = _Spy(ToolRun(0, data, None, 0.0))
    monkeypatch.setattr(universe_tools, "RUNNER", spy)
    shown = universe_tools.read_file(
        _universe(tmp_path), "previews/village.png", agent_id="main",
    )
    assert isinstance(shown, ToolImage) and (shown.width, shown.height) == (10, 10)
    call = spy.calls[0]
    assert call["inner"][-1] == "/u/previews/village.png"
    assert "cat --" in call["inner"][2]
    # An image read is attributed like every other jailed call.
    assert call["agent_id"] == "main"
    assert call["limits"].output_bytes == tool_images.MAX_IMAGE_SOURCE_BYTES
    # Every other limit is the default: only the output cap is raised.
    assert call["limits"].memory_bytes == universe_tools.DEFAULT_LIMITS.memory_bytes
    assert call["limits"].wall_seconds == universe_tools.DEFAULT_LIMITS.wall_seconds


def test_text_reads_keep_the_default_cap(tmp_path, monkeypatch):
    spy = _Spy(ToolRun(0, b"hello\n", None, 0.0))
    monkeypatch.setattr(universe_tools, "RUNNER", spy)
    assert universe_tools.read_file(
        _universe(tmp_path), "notes/a.md", agent_id="main",
    ) == "hello\n"
    assert spy.calls[0]["limits"].output_bytes == universe_tools.DEFAULT_LIMITS.output_bytes


@pytest.mark.parametrize("run, needle", [
    (ToolRun(None, b"x" * 10, "output_limit", 0.0), "too large to show"),
    (ToolRun(1, b"no such file: /u/a.png\n", None, 0.0), "no such file"),
    (ToolRun(0, b"plain text, misnamed", None, 0.0), "its bytes say otherwise"),
])
def test_an_unreadable_image_is_an_error_line(tmp_path, monkeypatch, run, needle):
    monkeypatch.setattr(universe_tools, "RUNNER", _Spy(run))
    out = universe_tools.read_file(_universe(tmp_path), "a.png", agent_id="main")
    assert isinstance(out, str) and out.startswith("error:") and needle in out


# --------------------------------------------------------------------------- #
# the engine `read`: image content through the real middleware stack
# --------------------------------------------------------------------------- #


def test_the_engine_read_returns_image_content_the_client_accepts(tmp_path, monkeypatch):
    from fastmcp import Client

    import tinyassets.api.helpers as helpers
    from tinyassets import engine_mcp_server as s

    root = tmp_path / "data"
    (root / "u-a").mkdir(parents=True)
    monkeypatch.setattr(helpers, "_base_path", lambda: root)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    monkeypatch.setattr(s, "_ACTOR_ID", "actor-a")
    monkeypatch.setattr(s, "_GRAPH_ID", "u-a")
    mock_engine_admission(monkeypatch, {"u-a"})
    image = _png(3000, 1000)

    def runner(universe_dir, inner, **kwargs):
        return ToolRun(0, image if inner[-1].endswith(".png") else b"text body\n", None, 0.0)

    monkeypatch.setattr(universe_tools, "RUNNER", runner)

    async def calls():
        async with Client(s.mcp) as client:
            tool = next(t for t in await client.list_tools() if t.name == "read")
            shot = await client.call_tool("read", {"path": "previews/v.png"},
                                          raise_on_error=False)
            text = await client.call_tool("read", {"path": "notes/a.md"},
                                          raise_on_error=False)
            return tool, shot, text

    tool, shot, text = asyncio.run(calls())
    assert tool.outputSchema is None
    assert not shot.is_error, shot
    kinds = [block.type for block in shot.content]
    assert kinds == ["text", "image"], kinds
    assert shot.content[1].mimeType in ("image/png", "image/jpeg")
    assert "3000x1000 image/png, shown as 1568x523" in shot.content[0].text
    assert not text.is_error and text.content[0].text.startswith("text body")


def test_the_http_loop_keeps_the_exact_result_and_shows_the_model_a_line():
    """The engine-owned HTTP loop's model connection is text-only. An image
    result must neither hold the turn nor be lost from the journal: the record
    keeps the exact result and classifies it presentable; the codec gives the
    model one line saying the image was not shown (Codex, 2026-10-02)."""
    import json

    from mcp.types import AudioContent, CallToolResult, TextContent

    from tinyassets.providers import agent_chat_codec as codec
    from tinyassets.storage import agent_turn_records as records

    shown = bound_image(_png(8, 8), "a.png")
    result = CallToolResult(content=shown.content_blocks(), isError=False)
    raw, kind, is_error = records.result_json(result)
    assert kind == "text_only" and is_error is False
    assert json.loads(raw)["content"][1]["data"] == shown.base64, "journal keeps the image"
    outcome = codec.tool_outcome(codec.ToolRequest("c1", "read", "{}"), result)
    model_view = json.loads(outcome.result_json)["content"]
    assert model_view == [{"type": "text", "text": shown.text},
                          {"type": "text", "text": codec.IMAGE_NOT_SHOWN}]
    # Any other non-text block still holds the turn, as before.
    audio = CallToolResult(content=[AudioContent(type="audio", data="AAAA",
                                                 mimeType="audio/wav")], isError=False)
    assert records.result_json(audio)[1] == "non_text"
    plain = CallToolResult(content=[TextContent(type="text", text="x")], isError=False)
    assert records.result_json(plain)[1] == "text_only"


def test_metadata_and_colour_profile_do_not_leave():
    from PIL import ImageCms

    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    image = Image.new("RGBA", (30, 30), (10, 200, 30, 128))
    data = _encode(image, "PNG", icc_profile=srgb)
    shown = bound_image(data, "profiled.png")
    assert isinstance(shown, ToolImage)
    reopened = _shown(shown)
    assert "icc_profile" not in reopened.info, "the profile is converted, then dropped"
    assert reopened.getpixel((5, 5))[1] > 150


def test_an_apng_poster_is_skipped_for_the_first_animation_frame():
    poster = Image.new("RGB", (16, 16), (0, 0, 0))
    first = Image.new("RGB", (16, 16), (250, 0, 0))
    second = Image.new("RGB", (16, 16), (0, 250, 0))
    data = _encode(poster, "PNG", save_all=True, append_images=[first, second],
                   default_image=True, duration=50)
    shown = bound_image(data, "walk.png")
    assert isinstance(shown, ToolImage) and "(first frame)" in shown.text
    red, green, _ = _shown(shown).convert("RGB").getpixel((5, 5))
    assert red > 240 and green < 16, "the animation's first frame, not the poster"


@pytest.mark.skipif(__import__("os").name != "posix", reason="rlimits are POSIX")
def test_the_decode_runs_in_a_child_whose_memory_limit_bites(monkeypatch):
    """The daemon never decodes: a child does, under an address-space limit. With
    the limit shrunk below what a legitimate 4000x4000 decode needs, it is
    refused -- proving the limit is applied, not just declared."""
    data = _png(4000, 4000)
    assert isinstance(bound_image(data, "ok.png"), ToolImage)
    monkeypatch.setattr(tool_images, "DECODE_MEMORY_BYTES", 200 * 1024 * 1024)
    # Control: the child starts and decodes a small image under the same limit,
    # so the refusal below is the decode's memory, not a child that never ran.
    assert isinstance(bound_image(_png(8, 8), "small.png"), ToolImage)
    refused = bound_image(data, "big.png")
    assert isinstance(refused, str) and refused.startswith("error:"), refused


def test_a_version_one_image_row_keeps_the_rule_it_was_written_under():
    """Codex round 3 (P1): reclassifying stored rows made them fail re-validation.
    A result written before images were presentable is version 1 and stays
    `non_text` (its turn was held); new image results are version 2."""
    import json

    from mcp.types import CallToolResult

    from tinyassets.storage import agent_turn_records as records

    shown = bound_image(_png(8, 8), "a.png")
    raw, kind, _ = records.result_json(CallToolResult(content=shown.content_blocks(),
                                                      isError=False))
    assert json.loads(raw)["version"] == 2 and kind == "text_only"
    legacy = json.loads(raw)
    legacy["version"] = 1
    _, legacy_kind, _ = records.load_result(json.dumps(legacy, separators=(",", ":")))
    assert legacy_kind == "non_text"
    text_only = json.loads(raw)
    text_only["content"] = text_only["content"][:1]
    with pytest.raises(ValueError):  # version 2 is only ever an image result
        records.load_result(json.dumps(text_only, separators=(",", ":")))


def test_unprojected_image_bytes_cannot_reach_a_model_body():
    """Codex round 3 (P2): only tool_outcome maps an image to a line; the body
    builder and history validation still accept text only."""
    import json

    from tinyassets.providers import agent_chat_codec as codec

    raw = {"content": [{"type": "image", "data": "YWJj", "mimeType": "image/png"}],
           "structuredContent": None, "isError": False}
    with pytest.raises(codec.ProtocolDecodeError, match="non-text"):
        codec._result_projection(json.loads(json.dumps(raw)))


def test_a_cmyk_jpeg_is_shown_in_rgb():
    image = Image.new("CMYK", (20, 20), (0, 255, 255, 0))  # red in CMYK
    shown = bound_image(_encode(image, "JPEG"), "print.jpg")
    assert isinstance(shown, ToolImage)
    red, green, blue = _shown(shown).convert("RGB").getpixel((5, 5))
    assert red > 200 and green < 60 and blue < 60
