"""Bounded owner-box roll followed by public canary acceptance; one safe receipt."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OUTCOMES = ("rolled", "deferred_busy", "asleep", "rolled_back")


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


def run(control, canary, *, concurrency=2):
    if not 1 <= concurrency <= 2:
        raise ValueError("roll concurrency must be one or two")
    receipt = {"counts": dict.fromkeys((*OUTCOMES, "failed"), 0),
               "failing_uids": [], "canary": "failed", "control": "passed"}
    try:
        inventory = control(None)
        uids = inventory["uids"]
        if (set(inventory) != {"uids", "asleep"} or not isinstance(uids, list)
                or type(inventory["asleep"]) is not int or inventory["asleep"] < 0
                or len(uids) != len(set(uids))
                or any(type(uid) is not int or not 300001 <= uid <= 399999 for uid in uids)):
            raise ValueError("invalid roll census")
        receipt["counts"]["asleep"] = inventory["asleep"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(control, uid): uid for uid in uids}
            for future in concurrent.futures.as_completed(futures):
                uid = futures[future]
                try:
                    result = future.result()
                    outcome = result.get("outcome")
                    expected = {"uid", "outcome", "reason"} if outcome == "rolled_back" else {
                        "uid", "outcome"}
                    if (set(result) != expected or result["uid"] != uid
                            or outcome not in OUTCOMES
                            or (outcome == "rolled_back" and result["reason"] != "health_failed")):
                        raise ValueError("invalid roll result")
                except Exception:
                    outcome = "failed"
                receipt["counts"][outcome] += 1
                if outcome in {"rolled_back", "failed"}:
                    receipt["failing_uids"].append(uid)
    except Exception:
        receipt["control"] = "failed"
    try:
        if canary() is not True:
            raise RuntimeError("canary did not pass")
        receipt["canary"] = "passed"
    except Exception:
        pass
    receipt["failing_uids"].sort()
    failed = (receipt["control"] == "failed" or receipt["canary"] == "failed"
              or bool(receipt["failing_uids"]))
    return receipt, int(failed)


def host_control(image_ref, uid):
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

    fields = {"image": "harness-" + image[7:]}
    if uid is None:
        install_image(image_ref, image, harness.parent)
    if uid is not None:
        fields["uid"] = uid
    result = request("roll", **fields)
    if uid is None:
        link = harness.parent / ".roll-current"
        link.unlink(missing_ok=True)
        link.symlink_to(fields["image"])
        os.replace(link, harness.parent / "harness-current")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-ref", required=True)
    parser.add_argument("--control", action="store_true")
    parser.add_argument("--uid", type=int)
    parser.add_argument("--ssh-target")
    parser.add_argument("--ssh-key")
    parser.add_argument("--expected-sha")
    parser.add_argument("--origin", default="https://tinyassets.io")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    if args.control:
        try:
            print(json.dumps(host_control(args.image_ref, args.uid)))
            return 0
        except Exception:
            print('{"error":"roll_control_failed"}')
            return 1
    if not all((args.ssh_target, args.ssh_key, args.expected_sha, args.receipt)):
        parser.error("deploy mode needs SSH target/key, expected SHA and receipt path")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.owner_box_canary import probe

    def control(uid):
        command = ["sudo", "python3", "-", "--control",
                   "--image-ref", args.image_ref]
        if uid is not None:
            command.extend(["--uid", str(uid)])
        result = subprocess.run(
            ["ssh", "-i", args.ssh_key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
             args.ssh_target, shlex.join(command)],
            input=Path(__file__).read_text(encoding="utf-8"),
            check=True, capture_output=True, text=True, timeout=610)
        return json.loads(result.stdout)

    receipt, code = run(control, lambda: probe(args.origin, args.expected_sha))
    args.receipt.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
