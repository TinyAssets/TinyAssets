"""C15: broker policy for waking boxes. A box sleeps when no work runs (ADR-026)."""
import os
from dataclasses import dataclass, field

_MIB = 1024 ** 2
# ADR-026: a wake reserves the thin class, and an awake box is charged what its
# cgroup uses, never less. Measured 2026-10-26: 95 to 145 MiB for an awake box
# app in gVisor. A CLI or Chromium is charged as it grows, from the next pass.
THIN_BYTES = 256 * _MIB


@dataclass
class LifecycleConfig:
    # Admission by memory, not a box count. boxhostd's MemoryMax=7G less 1 GiB
    # for boxhostd and growth since the last pass. A box past the budget is
    # killed by the kernel before the host (oomScoreAdj), never the host itself.
    memory_budget: int = field(default_factory=lambda: int(
        os.environ.get("TINYASSETS_BOX_MEMORY_BUDGET", str(6 * 1024 * _MIB))))
    wake_timeout: float = 60
    volume_concurrency: int = 3

    def __post_init__(self):
        if (self.memory_budget < THIN_BYTES or self.wake_timeout <= 0
                or self.volume_concurrency < 1):
            raise ValueError("positive owner lifecycle limits required")
