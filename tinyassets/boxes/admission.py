"""Root relay configuration; lifecycle state is supplied by the broker."""
from pathlib import Path


class OwnerRegistry:
    def __init__(self, data_root: Path, broker_socket: Path) -> None:
        self.data_root, self.broker_socket = data_root, broker_socket
