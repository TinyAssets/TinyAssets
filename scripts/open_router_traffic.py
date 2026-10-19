"""Host deployment step: consume rollback eligibility before opening admission."""
import subprocess
import sys
from pathlib import Path


def main():
    harness = Path("/opt/tinyassets/boxhostd/harness-current").resolve(strict=True)
    sys.path.insert(0, str(harness / "app"))
    from tinyassets.cutover_control import admit

    mount = subprocess.run(["docker", "volume", "inspect", "--format", "{{.Mountpoint}}",
                            "tinyassets-data"], check=True, capture_output=True,
                           text=True, timeout=10).stdout.strip()
    root = Path(mount)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("shared data volume unavailable")
    admit(Path("/var/lib/tinyassets-deploy/cutover"), root)
    print("ordinary admission opened after durable eligibility consumption")


if __name__ == "__main__":
    main()
