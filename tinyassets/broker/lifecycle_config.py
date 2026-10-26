"""C15: broker policy for awake slots and idle sleep."""
import os
from dataclasses import dataclass, field


@dataclass
class LifecycleConfig:
    idle_limit: float = field(default_factory=lambda: float(
        os.environ.get("TINYASSETS_BOX_IDLE_SECONDS", "900")))
    # 2 GiB is each box's ceiling, not a reservation; boxhostd's MemoryMax=7G
    # still bounds the fleet. A cap of 3 left no slot to wake anyone while three
    # owners stayed busy (2026-10-25: the canary could not wake and every deploy
    # rolled back), so the cap stays at 6.
    awake_cap: int = field(default_factory=lambda: int(
        os.environ.get("TINYASSETS_BOX_AWAKE_CAP", "6")))
    wake_timeout: float = 60
    volume_concurrency: int = 3

    def __post_init__(self):
        if (not 0 < self.idle_limit <= 86400 or self.awake_cap < 1
                or self.wake_timeout <= 0 or self.volume_concurrency < 1):
            raise ValueError("positive owner lifecycle limits required")
