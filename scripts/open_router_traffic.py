"""Host-root RPC to the router; the deploy never writes the traffic store."""
import socket
import subprocess
import sys
from pathlib import Path


def main():
    # The accepted immutable harness supplies the canonical RPC implementation,
    # exactly as the owner-box roll's host control client does.
    harness = Path("/opt/tinyassets/boxhostd/harness-current").resolve(strict=True)
    sys.path.insert(0, str(harness / "app"))
    from tinyassets import rpc_frames as rf
    from tinyassets.router_credentials import credential_socket

    mount = subprocess.run(["docker", "volume", "inspect", "--format", "{{.Mountpoint}}",
                            "tinyassets-data"], check=True, capture_output=True,
                           text=True, timeout=10).stdout.strip()
    root = Path(mount)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("router data volume is unavailable")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(30)
        channel.connect(str(credential_socket(root)))
        channel.sendall(rf.control(rf.CONNECTION, {"op": "OPEN_TRAFFIC"}))
        reply = rf.read_frame_blocking(channel)
        if reply is None or reply.control() != {"op": "TRAFFIC_OPENED", "traffic_opened": True}:
            raise RuntimeError("router traffic acceptance failed")
    print("router traffic acceptance committed")


if __name__ == "__main__":
    main()
