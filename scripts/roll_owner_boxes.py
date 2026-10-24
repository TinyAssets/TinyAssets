"""Publish one image, drain through ordinary sleep, then accept the public canary."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OUTCOMES = ("slept", "asleep", "incomplete")


def install_image(image_ref, image_id, root):
    """Extract the trusted digest without starting it or mounting any data."""
    target = root / ("harness-" + image_id[7:])
    if target.is_dir() and not target.is_symlink():
        if (target / ".box-image-id").read_text().strip() != image_id:
            raise ValueError("harness identity mismatch")
        return target
    with tempfile.TemporaryDirectory(prefix=".roll-stage-", dir=root) as temporary:
        stage = Path(temporary)
        tree = stage / "rootfs"
        tree.mkdir()
        container = subprocess.run(
            ["docker", "create", "--network", "none", "--entrypoint", "/bin/true", image_ref],
            check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        try:
            subprocess.run(["docker", "export", "--output", str(stage / "image.tar"), container],
                           check=True, capture_output=True, timeout=180)
            subprocess.run(["tar", "-xf", str(stage / "image.tar"), "-C", str(tree)],
                           check=True, capture_output=True, timeout=180)
        finally:
            subprocess.run(["docker", "rm", container], check=True, capture_output=True, timeout=30)
        for name in ("data", ".broker", ".platform", ".runtime"):
            path = tree / name
            if path.is_symlink():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        for name in ("home/owner", "tmp", "run/boxhostd"):
            (tree / name).mkdir(parents=True, exist_ok=True)
        (tree / ".box-image-id").write_text(image_id + "\n")
        for base, directories, files in os.walk(tree, followlinks=False):
            for name in (*directories, *files):
                path = Path(base) / name
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode & ~0o222)
        tree.rename(target)
        target.chmod(target.stat().st_mode & ~0o222)
    return target


def run(control, canary, prepare, ready=lambda: None):
    receipt = {"counts": dict.fromkeys(OUTCOMES, 0),
               "canary": "failed", "control": "failed"}
    try:
        # A public request first makes the canary awake. Successful fleet drain
        # then proves its ordinary sleep before the acceptance request wakes it.
        prepare()
        counts = control()
        if (set(counts) != set(OUTCOMES)
                or any(type(n) is not int or n < 0 for n in counts.values())):
            raise ValueError("invalid deployment drain result")
        receipt["counts"] = counts
        if counts["incomplete"]:
            return receipt, 1
        receipt["control"] = "passed"
        ready()
        if canary() is True:
            receipt["canary"] = "passed"
    except Exception:
        pass
    return receipt, int(receipt["canary"] != "passed")


def host_control(image_ref):
    """Executed only by the authorized deploy caller, over the root control socket."""
    if not re.fullmatch(r"[A-Za-z0-9./_-]+@sha256:[0-9a-f]{64}", image_ref):
        raise ValueError("immutable image reference required")
    image = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image_ref],
                           check=True, capture_output=True, text=True, timeout=10).stdout.strip()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise ValueError("invalid image identity")
    harness = Path("/opt/tinyassets/boxhostd/harness-current").resolve(strict=True)
    sys.path.insert(0, str(harness / "app"))
    from tinyassets.broker.lifecycle_control import request

    selected = "harness-" + image[7:]
    install_image(image_ref, image, harness.parent)
    result = request("deploy", image=selected)
    link = harness.parent / ".image-current"
    link.unlink(missing_ok=True)
    link.symlink_to(selected)
    os.replace(link, harness.parent / "harness-current")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--control", action="store_true")
    parser.add_argument("--ssh-target")
    parser.add_argument("--ssh-key")
    parser.add_argument("--expected-sha")
    parser.add_argument("--origin", default="https://tinyassets.io")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    if args.control:
        try:
            print(json.dumps(host_control(args.image_ref)))
            return 0
        except Exception:
            print('{"error":"image_drain_failed"}')
            return 1
    if not all((args.ssh_target, args.ssh_key, args.expected_sha, args.receipt)):
        parser.error("deploy mode needs SSH target/key, expected SHA and receipt path")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.owner_box_canary import probe, wake

    def control():
        command = ["sudo", "python3", "-", "--control",
                   "--image-ref", args.image_ref]
        result = subprocess.run(
            ["ssh", "-i", args.ssh_key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             args.ssh_target, shlex.join(command)],
            input=Path(__file__).read_text(encoding="utf-8"),
            check=True, capture_output=True, text=True, timeout=610)
        return json.loads(result.stdout)

    def ready():
        from scripts.wait_boxhostd_ready import WAIT_TIMEOUT_S

        subprocess.run(
            ["ssh", "-i", args.ssh_key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             args.ssh_target,
             "sudo env PYTHONPATH=/opt/tinyassets/boxhostd python3 -"],
            input=(Path(__file__).with_name("wait_boxhostd_ready.py")
                   .read_text(encoding="utf-8")),
            check=True, capture_output=True, text=True,
            timeout=WAIT_TIMEOUT_S + 60)

    receipt, code = run(control, lambda: probe(args.origin, args.expected_sha),
                        lambda: wake(args.origin), ready)
    args.receipt.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
