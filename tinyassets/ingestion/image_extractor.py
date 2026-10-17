"""Extract factual image metadata without model inference."""

import io
from pathlib import Path


def extract_image_description(filename: str, data: bytes) -> str:
    """Return the uploaded image's name, size, dimensions and format."""
    info_parts = [f"Image file: {filename}"]
    info_parts.append(f"Size: {len(data):,} bytes")

    # Try to get dimensions from Pillow
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        w, h = img.size
        info_parts.append(f"Dimensions: {w}x{h}")
        info_parts.append(f"Format: {img.format or 'unknown'}")
        if img.mode:
            info_parts.append(f"Color mode: {img.mode}")
    except Exception:
        ext = Path(filename).suffix.lower()
        info_parts.append(f"Format: {ext}")

    return "\n".join(info_parts)
