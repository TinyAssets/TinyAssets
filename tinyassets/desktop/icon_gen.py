"""The desktop tray's public icon helpers."""

import sys
from pathlib import Path

if __name__ == "__main__" and not __package__:
    # A script run by its path sees this folder alone on Python's import path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from WebSite.brand.mark import create_icon_image, draw_mark, mark_svg, write_ico  # noqa: E402

__all__ = ["create_icon_image", "draw_mark", "generate_icon", "mark_svg"]


def generate_icon(output_path: str | Path | None = None) -> Path:
    """Write the multi-size tray icon, defaulting to app.ico beside this module."""
    path = Path(__file__).parent / "app.ico" if output_path is None else Path(output_path)
    return write_ico(path)


if __name__ == "__main__":  # pragma: no cover
    print(generate_icon())
