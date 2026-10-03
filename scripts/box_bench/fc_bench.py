#!/usr/bin/env python3
"""Firecracker measurements for target-architecture slice S0 (runs as root on a throwaway droplet).

Measures, under the host's (possibly nested) KVM:
  * cold boot to guest agent ready;
  * exec round-trip latency over vsock (the BoxProvider exec path's transport);
  * idle CPU of one awake microVM;
  * full snapshot create time and size; restore + resume; wake (restore to first exec answered);
  * packing: N microVMs awake at once - host memory, steal, exec latency under load.

Prints one JSON object on stdout. Inputs come from kvm_validation.sh.
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

WORK = Path(sys.argv[1])
KERNEL = WORK / "vmlinux"
BASE_IMAGE = WORK / "rootfs.ext4"
PACK = int(sys.argv[2]) if len(sys.argv) > 2 else 10
MEM_MIB = int(sys.argv[3]) if len(sys.argv) > 3 else 512
VSOCK_PORT = 5000


class UHTTP(http.client.HTTPConnection):
    def __init__(self, path: str) -> None:
        super().__init__("localhost")
        self.path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(self.path)


def api(sock: str, method: str, path: str, body: dict | None = None) -> None:
    conn = UHTTP(sock)
    conn.request(method, path, json.dumps(body) if body is not None else None,
                 {"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = resp.read().decode()
    if resp.status >= 300:
        raise RuntimeError(f"{method} {path} -> {resp.status} {data}")


class VM:
    def __init__(self, name: str) -> None:
        self.name = name
        self.dir = WORK / "vms" / name
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)
        self.api_sock = str(self.dir / "api.sock")
        self.vsock_uds = str(self.dir / "v.sock")
        self.console = self.dir / "console.log"
        self.proc: subprocess.Popen | None = None

    def start_process(self) -> None:
        log = open(self.console, "w")
        self.proc = subprocess.Popen(
            ["firecracker", "--api-sock", self.api_sock, "--level", "Error"],
            stdout=log, stderr=subprocess.STDOUT)
        for _ in range(500):
            if os.path.exists(self.api_sock):
                return
            time.sleep(0.01)
        raise RuntimeError("firecracker API socket never appeared")

    def boot(self, image: Path) -> float:
        self.start_process()
        api(self.api_sock, "PUT", "/machine-config", {"vcpu_count": 1, "mem_size_mib": MEM_MIB})
        api(self.api_sock, "PUT", "/boot-source", {
            "kernel_image_path": str(KERNEL),
            "boot_args": "console=ttyS0 reboot=k panic=1 pci=off quiet init=/sbin/box-init"})
        api(self.api_sock, "PUT", "/drives/rootfs", {
            "drive_id": "rootfs", "path_on_host": str(image),
            "is_root_device": True, "is_read_only": False})
        api(self.api_sock, "PUT", "/vsock", {"guest_cid": 3, "uds_path": self.vsock_uds})
        started = time.perf_counter()
        api(self.api_sock, "PUT", "/actions", {"action_type": "InstanceStart"})
        self.wait_exec_ready(120)
        return time.perf_counter() - started

    def exec(self, command: str, timeout: float = 30) -> tuple[int, bytes]:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(timeout)
        s.connect(self.vsock_uds)
        s.sendall(f"CONNECT {VSOCK_PORT}\n".encode())
        ack = b""
        while not ack.endswith(b"\n"):
            chunk = s.recv(1)
            if not chunk:
                raise ConnectionError("vsock closed during CONNECT")
            ack += chunk
        if not ack.startswith(b"OK"):
            raise ConnectionError(f"vsock CONNECT refused: {ack!r}")
        s.sendall(command.encode() + b"\n")
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
        s.close()
        head, _, body = data.partition(b"\n")
        return int(head or b"-1"), body

    def wait_exec_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.exec("true", timeout=2)[0] == 0:
                    return
            except OSError:
                pass
            time.sleep(0.01)
        raise TimeoutError(f"{self.name}: guest agent not ready; console: "
                           f"{self.console.read_text()[-500:]}")

    def rss_mib(self) -> float:
        if self.proc is None:
            return 0.0
        for line in Path(f"/proc/{self.proc.pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
        return 0.0

    def cpu_ticks(self) -> int:
        fields = Path(f"/proc/{self.proc.pid}/stat").read_text().rsplit(")", 1)[1].split()
        return int(fields[11]) + int(fields[12])

    def kill(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait()
            self.proc = None


def exec_latencies(vm: VM, n: int) -> list[float]:
    out = []
    for _ in range(n):
        t = time.perf_counter()
        code, _ = vm.exec("true")
        out.append((time.perf_counter() - t) * 1000)
        assert code == 0
    return out


def pct(values: list[float], p: float) -> float:
    values = sorted(values)
    return round(values[min(len(values) - 1, int(len(values) * p))], 2)


def host_steal() -> tuple[int, int]:
    cpu = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    nums = [int(x) for x in cpu]
    return nums[7], sum(nums)  # steal, total


def host_mem_available_mib() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return 0


def main() -> None:
    hz = os.sysconf("SC_CLK_TCK")
    result: dict = {"mem_mib_per_vm": MEM_MIB}

    # one VM: boot, exec latency, idle CPU, snapshot, restore, wake
    image = WORK / "vm0.ext4"
    shutil.copyfile(BASE_IMAGE, image)
    vm = VM("vm0")
    result["cold_boot_to_exec_ready_s"] = round(vm.boot(image), 3)
    lat = exec_latencies(vm, 50)
    result["exec_rtt_ms"] = {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "n": len(lat)}
    t0 = vm.cpu_ticks()
    time.sleep(20)
    result["idle_awake_vm_cpu_pct_of_core"] = round(100 * (vm.cpu_ticks() - t0) / hz / 20, 2)
    result["awake_vm_rss_mib"] = round(vm.rss_mib(), 1)
    api(vm.api_sock, "PATCH", "/vm", {"state": "Paused"})
    t = time.perf_counter()
    api(vm.api_sock, "PUT", "/snapshot/create", {
        "snapshot_type": "Full", "snapshot_path": str(vm.dir / "snap.vmstate"),
        "mem_file_path": str(vm.dir / "snap.mem")})
    result["snapshot_create_s"] = round(time.perf_counter() - t, 3)
    result["snapshot_mem_mib"] = round((vm.dir / "snap.mem").stat().st_size / 2**20)
    vm.kill()

    wakes, loads = [], []
    for i in range(5):
        r = VM(f"restore{i}")
        r.start_process()
        # the snapshot re-binds the ORIGINAL vsock uds path; free it first
        if os.path.exists(vm.vsock_uds):
            os.unlink(vm.vsock_uds)
        t = time.perf_counter()
        api(r.api_sock, "PUT", "/snapshot/load", {
            "snapshot_path": str(vm.dir / "snap.vmstate"),
            "mem_backend": {"backend_type": "File", "backend_path": str(vm.dir / "snap.mem")},
            "resume_vm": True})
        loads.append((time.perf_counter() - t) * 1000)
        # the restored guest's vsock device is reachable through the ORIGINAL uds path
        r.vsock_uds = vm.vsock_uds
        r.wait_exec_ready(30)
        wakes.append((time.perf_counter() - t) * 1000)
        result.setdefault("restored_rss_mib", round(r.rss_mib(), 1))
        r.kill()
    result["restore_load_ms"] = {"p50": pct(loads, 0.5), "max": round(max(loads), 2)}
    result["wake_to_first_exec_ms"] = {"p50": pct(wakes, 0.5), "max": round(max(wakes), 2)}

    # packing: N awake at once
    avail0 = host_mem_available_mib()
    steal0, total0 = host_steal()
    vms = []
    boots = []
    for i in range(PACK):
        img = WORK / f"pack{i}.ext4"
        subprocess.run(["cp", "--sparse=always", str(BASE_IMAGE), str(img)], check=True)
        p = VM(f"pack{i}")
        boots.append(p.boot(img))
        vms.append(p)
    time.sleep(10)
    lat_load = []
    for p in vms:
        lat_load += exec_latencies(p, 10)
    steal1, total1 = host_steal()
    result["packing"] = {
        "vms": PACK,
        "boot_s": {"p50": pct(boots, 0.5), "max": round(max(boots), 3)},
        "host_mem_available_drop_mib": avail0 - host_mem_available_mib(),
        "sum_vm_rss_mib": round(sum(p.rss_mib() for p in vms), 1),
        "exec_rtt_ms_under_load": {"p50": pct(lat_load, 0.5), "p95": pct(lat_load, 0.95)},
        "host_steal_pct": round(100 * (steal1 - steal0) / max(1, total1 - total0), 2),
    }
    t_cpu = [p.cpu_ticks() for p in vms]
    time.sleep(20)
    result["packing"]["idle_cpu_pct_of_core_total"] = round(
        100 * sum(p.cpu_ticks() - c for p, c in zip(vms, t_cpu)) / hz / 20, 2)
    for p in vms:
        p.kill()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
