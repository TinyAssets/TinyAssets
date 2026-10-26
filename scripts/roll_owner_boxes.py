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

OUTCOMES = ("slept", "asleep", "busy", "unresponsive", "incomplete",
            "incomplete_status", "incomplete_sleep")
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,40}\Z")
_CONTROL_CODES = frozenset({
    "image_drain_failed", "image_refused", "image_install_failed",
    "image_drain_timeout", "broker_peer_refused", "harness_missing",
    "control_timeout",
})


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


def _public_code(exc):
    text = str(exc)
    if type(exc) is RuntimeError and _SAFE_CODE.fullmatch(text):
        return text
    return ""


def _control_error(exc):
    """A fixed code from the control SSH, never its text or a principal."""
    stdout = getattr(exc, "stdout", None)
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", "replace")
    if isinstance(stdout, str) and stdout.strip():
        try:
            payload = json.loads(stdout)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("error") in _CONTROL_CODES:
            return payload["error"]
    if isinstance(exc, subprocess.TimeoutExpired):
        return "control_timeout"
    return _public_code(exc)


def _host_error(exc):
    if isinstance(exc, subprocess.TimeoutExpired):
        return "image_drain_timeout"
    if isinstance(exc, subprocess.CalledProcessError):
        return "image_install_failed"
    if isinstance(exc, PermissionError):
        return "broker_peer_refused"
    if isinstance(exc, FileNotFoundError):
        return "harness_missing"
    if isinstance(exc, ValueError):
        return "image_refused"
    return "image_drain_failed"


#: The deploy's own capability probes run turns in the canary's box just before
#: this step; a busy canary is left serving its old image like any owner. Wait
#: for it to go idle, re-run the idempotent drain, and probe again.
CANARY_WAIT_SECONDS = 600
CANARY_RETRY_SECONDS = 30


def _counts(control):
    counts = control()
    if isinstance(counts, dict):
        # A broker from before these counts reports neither.
        counts = {"busy": 0, "unresponsive": 0, "incomplete_status": 0,
                  "incomplete_sleep": 0, **counts}
    if (not isinstance(counts, dict) or set(counts) != set(OUTCOMES)
            or any(type(n) is not int or n < 0 for n in counts.values())):
        return None
    return counts


def run(control, canary, prepare, ready=lambda: None, *, pause=None, clock=None):
    import time

    pause = pause or time.sleep
    clock = clock or time.monotonic
    receipt = {"counts": dict.fromkeys(OUTCOMES, 0),
               "canary": "failed", "control": "failed", "reason": "control_failed"}
    stage = "prepare_failed"
    try:
        # A public request first makes the canary awake. Successful fleet drain
        # then proves its ordinary sleep before the acceptance request wakes it.
        prepare()
        stage = "control_failed"
        counts = _counts(control)
        if counts is None:
            receipt["reason"] = "invalid_drain"
            return receipt, 1
        receipt["counts"] = counts
        if counts["incomplete"]:
            # The control call returned. One owner did not finish ordinary sleep.
            receipt["control"] = "incomplete"
            receipt["reason"] = "incomplete_drain"
            return receipt, 1
        # A busy owner never blocks a deploy: it was never drained, and it
        # starts the published image at its next ordinary wake. The canary
        # still decides acceptance on that image.
        receipt["control"] = ("deferred" if counts["busy"] or counts["unresponsive"]
                              else "passed")
        if counts["unresponsive"]:
            # Loud, not fatal: the rest of the fleet still gets its update.
            print(f"::warning::{counts['unresponsive']} owner box(es) awake but not answering "
                  "their status; left as they are", file=sys.stderr)
        stage = "readiness_failed"
        ready()
        stage = "canary_failed"
        deadline = clock() + CANARY_WAIT_SECONDS
        while True:
            try:
                accepted = canary() is True
                break
            except RuntimeError as exc:
                # Only "the canary answered on the old image" is retried; any
                # other refusal fails acceptance at once.
                # No retry starts once its pause would pass the budget, so one
                # final drain and readiness wait is the most that can follow.
                if (str(exc) != "canary_box_revision_mismatch"
                        or clock() + CANARY_RETRY_SECONDS >= deadline):
                    raise
            pause(CANARY_RETRY_SECONDS)
            stage = "control_failed"
            again = _counts(control)
            if again is None or again["incomplete"]:
                receipt["reason"] = "invalid_drain" if again is None else "incomplete_drain"
                receipt["control"] = "incomplete" if again else "failed"
                return receipt, 1
            receipt["counts"] = {k: receipt["counts"][k] + again[k] for k in OUTCOMES}
            stage = "readiness_failed"
            ready()
            stage = "canary_failed"
        if accepted:
            receipt["canary"] = "passed"
            receipt["reason"] = "passed"
        else:
            receipt["reason"] = "canary_probe_failed"
    except Exception as exc:
        if stage == "control_failed":
            receipt["reason"] = _control_error(exc) or "control_failed"
        else:
            receipt["reason"] = _public_code(exc) or stage
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
        except Exception as exc:
            print(json.dumps({"error": _host_error(exc)}))
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
