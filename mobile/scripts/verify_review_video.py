"""Assert the native launch artwork appears in the actual recording, not a mock.

XCTest launch() returns after splash dismissal, so inspect the recording itself.
Match the central mark at multiple scales AND the surrounding native background.
"""

from __future__ import annotations

import sys
from pathlib import Path


def verify_splash(video: Path, output: Path) -> bool:
    import cv2
    import numpy as np

    source = cv2.imread(str(Path(__file__).parents[1] / "resources/splash.png"))
    size = source.shape[0]
    mark = source[int(size * 0.35) : int(size * 0.65), int(size * 0.35) : int(size * 0.65)]
    capture = cv2.VideoCapture(str(video))
    fps = capture.get(cv2.CAP_PROP_FPS) or 30
    index = 0
    # The simulator records XCTest setup too; launch may occur several minutes in.
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        index += 1
        if index % max(1, int(fps / 6)):
            continue
        height, width = frame.shape[:2]
        small = cv2.resize(frame, (240, int(height * 240 / width)))
        h, w = small.shape[:2]
        # Native background is #0f1020; both side margins must match. This rejects
        # the app's tiny header logo, SpringBoard icons and unrelated content.
        margins = np.concatenate(
            (
                small[int(h * 0.2) : int(h * 0.8), :12].reshape(-1, 3),
                small[int(h * 0.2) : int(h * 0.8), -12:].reshape(-1, 3),
            )
        )
        if np.abs(margins.astype(float) - np.array([32, 16, 15])).mean() > 8:
            continue
        for edge in range(60, min(w, h) - 10, 4):
            template = cv2.resize(mark, (edge, edge))
            scores = cv2.matchTemplate(small, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            if score > 0.94 and abs(x + edge / 2 - w / 2) < 12 and abs(y + edge / 2 - h / 2) < 30:
                output.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(output / "review-01-native-splash.png"), frame)
                capture.release()
                print("Native splash verified in recording")
                return True
    capture.release()
    return False


if __name__ == "__main__":
    if not verify_splash(Path(sys.argv[1]), Path(sys.argv[2])):
        raise SystemExit("::error::Native splash missing from recording")
