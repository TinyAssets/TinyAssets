"""Open encoded image bytes, with no filename or caller-supplied stream input."""
from __future__ import annotations

import io


def open_image_bytes(data: bytes, *, formats):
    """Caller owns the returned Pillow image and must close it after decoding."""
    if type(data) is not bytes:
        raise TypeError('image decoding requires encoded bytes')
    from PIL import Image

    return Image.open(io.BytesIO(data), formats=formats)
