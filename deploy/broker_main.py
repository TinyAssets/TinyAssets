"""Isolated broker entry point; the image owns the complete import tree."""
import sys

sys.path.insert(0, "/app")

from tinyassets.broker.process import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
