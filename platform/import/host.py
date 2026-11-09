#!/usr/bin/env python3
"""The import window's host: one disposable Droplet a run, one run at a time.

`import_host_research.md`, "The design"; tasks.md §9 item 7. The window never touches the
production droplet: each run's modes run on its own import host, `tinyassets-import-<run>`,
which `census` makes and `clean` destroys. Run by `.github/workflows/import-window.yml` on
the runner, never on a host, with the DigitalOcean calls of `scripts/do_api_request.py`
(bounded, the token never in a message). Deleted at the cutover with platform/import/.

  host.py hosts   --summary <file>
      Every `import-window` Droplet, named with its age, a warning past 7 days (an orphan
      bills about $1.71 a day). Every dispatch runs it.
  host.py ensure  --run <id> --mode <mode> --deploy-key <public key file> --output <file>
      This run's host, found by its tag `ta-import-<run>` and held to its name and tags, with
      the reserved IP (IMPORT_RESERVED_IP) on it. `census` makes one when there is none, and
      refuses while any other `import-window` Droplet exists: `sfo3`, `s-4vcpu-8gb`, Debian
      (`select_do_image.py`), no IPv6, the deploy key, a host key made here (its private half
      in `user_data` alone, its SHA-256 in the tag `ta-hostkey-<hex>`), and a minimal
      cloud-init (`cloud_init`: docker.io, rclone, xfsprogs and curl; the default route
      through the reserved IP's anchor gateway and the metadata service dropped for Docker's
      containers, each kept across boots; then the marker /etc/tinyassets-import-host,
      holding the run's id). Then the reserved IP is assigned to it. Every other mode refuses
      when the run has none (`clean` has nothing to do), or when the reserved IP is not on it;
      `clean` alone takes such a host, unreached, to destroy it. Writes `id`, `address` (the
      reserved IP, which the host is reached by; empty for a host not to be reached) and
      `hostkey`.
  host.py pin     --run <id> --hostkey <hex> --known-hosts <file> --identity <key>
      The host's key, pinned: `ssh-keyscan` is only a transport, and the one key it offers
      whose SHA-256 is the tag's is written to the known-hosts file, so every SSH session
      after it checks that key strictly. Then the host's set-up is awaited: its marker
      naming the run.
  host.py egress  --run <id> --mode <mode> --known-hosts <file> --identity <key>
      What Cloudflare sees: `https://www.cloudflare.com/cdn-cgi/trace` fetched from a
      container on the host's Docker bridge, as every job leaves, whose `ip=` must be the
      reserved IP; and the metadata service (which serves user_data, the host's private key)
      unreachable from it. The tokens are pinned to that address, so no credential leaves the
      runner until both hold. census and every mode that hands the host a credential run it
      first, in the run's own Node by the digest census pinned (`egress_command`).
  host.py destroy --run <id> --id <droplet>
      The Droplet held to this run's name and tags (as dr-drill.yml holds its retained
      Droplet), then destroyed; its reserved IP goes back to unassigned. A failed delete is an
      error that names the Droplet.

Each refusal is one line, `refused: ...`, and exit 2; nothing prints a credential.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from do_api_request import DigitalOceanRequestError, api_request  # noqa: E402
from select_do_image import ImageSelectionError, select_image_slug  # noqa: E402

API = "https://api.digitalocean.com/v2"
REGION = "sfo3"
SIZE = "s-4vcpu-8gb"
TAG = "import-window"
MARKER = "/etc/tinyassets-import-host"
RUN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# A Droplet's name is its hostname, one DNS label: 63 characters at most.
MAX_RUN_IN_NAME = 63 - len("tinyassets-import-")
HOSTKEY = re.compile(r"^ta-hostkey-([0-9a-f]{64})$")
IPV4 = re.compile(r"^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$")
ORPHAN_DAYS = 7
TRACE = "https://www.cloudflare.com/cdn-cgi/trace"
# The metadata service serves user_data, which holds the host's private key and cannot be
# changed after creation: no container may reach it (the boot script drops it for Docker).
METADATA = "http://169.254.169.254/metadata/v1/id"
# The egress check's container: on Docker's default bridge, as every networked job. It
# prints the trace, then `metadata=blocked` only when the metadata service never answers.
TRACE_JS = (f"fetch({json.dumps(TRACE)}).then((r) => r.text())"
            ".then((t) => process.stdout.write(t))"
            f".then(() => fetch({json.dumps(METADATA)}, {{ signal: AbortSignal.timeout(5000) }})"
            ".then(() => 'reachable', () => 'blocked'))"
            ".then((m) => process.stdout.write(`\\nmetadata=${m}\\n`))"
            ".catch((e) => { console.error(String(e)); process.exit(1); })")

# The host's set-up. The route moves to the anchor gateway after the packages are installed
# (before it, nothing leaves through the reserved IP), and the marker is written last, only
# when the tools are there and the route is set.
ROUTE_SCRIPT = """#!/bin/sh
# The import host's default route through its anchor gateway, so it leaves through the
# reserved IP (DigitalOcean: reserved IPs, outbound traffic). Run at every boot.
set -eu
gw="$(curl -fsS http://169.254.169.254/metadata/v1/interfaces/public/0/anchor_ipv4/gateway)"
case "$gw" in ''|*[!0-9.]*) echo "no anchor gateway" >&2; exit 1 ;; esac
ip route replace default via "$gw" dev eth0
# No container reaches the metadata service: it serves user_data, the host's private key.
# DOCKER-USER is Docker's own chain for this, which it makes if absent and never flushes.
iptables -N DOCKER-USER 2>/dev/null || true
iptables -C DOCKER-USER -d 169.254.169.254/32 -j DROP 2>/dev/null \\
  || iptables -I DOCKER-USER -d 169.254.169.254/32 -j DROP
"""
ROUTE_UNIT = """[Unit]
Description=The import host's default route through the reserved IP's anchor gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/tinyassets-anchor-route

[Install]
WantedBy=multi-user.target
"""
FINISH = ("command -v docker && command -v rclone && command -v mkfs.xfs && command -v curl"
          " && systemctl daemon-reload"
          " && systemctl enable --now tinyassets-anchor-route.service"
          f" && printf '%s\\n' \"$1\" > {MARKER}")


def cloud_init(run: str, private_key: str, public_key: str) -> str:
    """The host's user_data: a cloud-config (JSON is YAML) and nothing else."""
    return "#cloud-config\n" + json.dumps({
        "ssh_deletekeys": True,
        "ssh_genkeytypes": [],
        "ssh_keys": {"ed25519_private": private_key, "ed25519_public": public_key},
        "package_update": True,
        "packages": ["docker.io", "rclone", "xfsprogs", "curl"],
        "write_files": [
            {"path": "/usr/local/sbin/tinyassets-anchor-route", "permissions": "0755",
             "content": ROUTE_SCRIPT},
            {"path": "/etc/systemd/system/tinyassets-anchor-route.service",
             "permissions": "0644", "content": ROUTE_UNIT},
        ],
        "runcmd": [["sh", "-c", FINISH, "sh", run]],
    }, indent=1) + "\n"


class Refused(Exception):
    """A refusal: one line, never a credential."""


# The seams the test replaces (guards/fake_do.py): the API, the clock and sleep.
def transport(method: str, url: str, data: bytes | None) -> bytes:
    return api_request(method=method, url=url, token=os.environ.get("DO_TOKEN", ""), data=data)


def now() -> datetime:
    return datetime.now(timezone.utc)


sleep: Callable[[float], None] = time.sleep


def call(method: str, path: str, body: object | None = None) -> dict:
    raw = transport(method, f"{API}{path}",
                    None if body is None else json.dumps(body).encode())
    return json.loads(raw) if raw.strip() else {}


def name_of(run: str) -> str:
    return f"tinyassets-import-{run}"


def import_hosts() -> list[dict]:
    listed = call("GET", f"/droplets?tag_name={TAG}&per_page=200")
    if (listed.get("links") or {}).get("pages", {}).get("next"):
        raise Refused("DigitalOcean lists more import-window Droplets than one page holds")
    return list(listed.get("droplets") or [])


def age(droplet: dict) -> float:
    """The Droplet's age in days."""
    created = datetime.fromisoformat(str(droplet["created_at"]).replace("Z", "+00:00"))
    return (now() - created).total_seconds() / 86400


def describe(droplet: dict) -> str:
    days = age(droplet)
    up = f"{days * 24:.1f} hours" if days < 2 else f"{days:.1f} days"
    return f"{droplet.get('name')} (Droplet {droplet.get('id')}), up {up}"


def held(droplet: dict, run: str) -> None:
    """The Droplet is this run's import host by name and tags, or refused."""
    tags = set(droplet.get("tags") or [])
    if (droplet.get("name") != name_of(run)
            or not {"tinyassets", TAG, f"ta-import-{run}"}.issubset(tags)):
        raise Refused(f"Droplet {droplet.get('id')} is tagged for this run but is not its "
                      "import host by name and tags")


def hostkey_of(droplet: dict) -> str:
    keys = [m.group(1) for tag in droplet.get("tags") or [] if (m := HOSTKEY.match(tag))]
    if len(keys) != 1:
        raise Refused(f"Droplet {droplet.get('id')} records no one host key")
    return keys[0]


def reserved_ip() -> str:
    address = os.environ.get("IMPORT_RESERVED_IP", "")
    if not IPV4.match(address):
        raise Refused("IMPORT_RESERVED_IP is no IPv4 address")
    return address


def reserved_on(address: str) -> int | None:
    held_ip = call("GET", f"/reserved_ips/{address}")["reserved_ip"]
    if (held_ip.get("region") or {}).get("slug") != REGION:
        raise Refused(f"the reserved IP is not in {REGION}")
    droplet = held_ip.get("droplet")
    return None if not droplet else int(droplet["id"])


def wait_active(droplet_id: int) -> dict:
    for _ in range(60):
        droplet = call("GET", f"/droplets/{droplet_id}")["droplet"]
        if droplet.get("status") == "active":
            return droplet
        sleep(10)
    raise Refused(f"Droplet {droplet_id} did not become active")


def assign(address: str, droplet_id: int) -> None:
    on = reserved_on(address)
    if on == droplet_id:
        return
    if on is not None:
        raise Refused(f"the reserved IP is on Droplet {on}, which is not this run's import host")
    call("POST", f"/reserved_ips/{address}/actions", {"type": "assign", "droplet_id": droplet_id})
    for _ in range(30):
        if reserved_on(address) == droplet_id:
            return
        sleep(10)
    raise Refused(f"the reserved IP did not reach Droplet {droplet_id}")


def blob_of(public_key: str) -> bytes:
    fields = public_key.split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise Refused("a host key is no ed25519 public key")
    return base64.b64decode(fields[1], validate=True)


def deploy_key_id(public_key_file: str) -> int:
    fields = Path(public_key_file).read_text(encoding="utf-8").split()
    if len(fields) < 2:
        raise Refused("the deploy key's public half could not be read")
    md5 = hashlib.md5(base64.b64decode(fields[1], validate=True)).hexdigest()  # noqa: S324
    fingerprint = ":".join(md5[i:i + 2] for i in range(0, 32, 2))
    keys = call("GET", "/account/keys?per_page=200").get("ssh_keys") or []
    found = [key["id"] for key in keys if key.get("fingerprint") == fingerprint]
    if not found:
        raise Refused("the deploy key (DO_SSH_KEY) is not registered with DigitalOcean")
    return int(found[0])


def host_keypair(run: str) -> tuple[str, str]:
    """An ed25519 host key made here: its two halves, the files gone once read."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "host"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name_of(run),
                        "-f", str(path)], check=True, stdin=subprocess.DEVNULL)
        return path.read_text(encoding="utf-8"), Path(f"{path}.pub").read_text(
            encoding="utf-8").strip()


def create(run: str, address: str, deploy_key: str) -> dict:
    others = import_hosts()
    if others:
        raise Refused("another import host is up, and the window runs one at a time: "
                      + "; ".join(describe(each) for each in others)
                      + ". Clean its run first")
    on = reserved_on(address)
    if on is not None:
        raise Refused(f"the reserved IP is on Droplet {on}, which is no import host")
    key_id = deploy_key_id(deploy_key)
    try:
        image = select_image_slug(region=REGION,
                                  fetch_page=lambda url: transport("GET", url, None))
    except ImageSelectionError as exc:
        raise Refused(f"no Debian image: {exc}") from None
    private, public = host_keypair(run)
    hostkey = hashlib.sha256(blob_of(public)).hexdigest()
    made = call("POST", "/droplets", {
        "name": name_of(run), "region": REGION, "size": SIZE, "image": image,
        "ssh_keys": [key_id], "ipv6": False, "monitoring": False, "backups": False,
        "user_data": cloud_init(run, private, public),
        "tags": ["tinyassets", TAG, f"ta-import-{run}", f"ta-hostkey-{hostkey}"],
    })["droplet"]
    print(f"made the run's import host {name_of(run)} (Droplet {made['id']}) in {REGION}")
    return made


def ensure(args: argparse.Namespace) -> int:
    run = args.run
    if len(run) > MAX_RUN_IN_NAME:
        raise Refused(f"a run that has an import host has an id of at most {MAX_RUN_IN_NAME} "
                      "characters (its Droplet's name is one hostname label)")
    address = reserved_ip()
    mine = call("GET", f"/droplets?tag_name=ta-import-{run}&per_page=200").get("droplets") or []
    if len(mine) > 1:
        raise Refused(f"{len(mine)} Droplets are tagged for this run: delete the extra by hand")
    if not mine:
        if args.mode == "clean":
            print("the run has no import host: nothing to clean")
            write_outputs(args.output, {"id": "", "address": "", "hostkey": ""})
            return 0
        if args.mode != "census":
            raise Refused("this run has no import host: run census first")
        droplet = create(run, address, args.deploy_key)
    else:
        droplet = mine[0]
        print(f"the run's import host is {describe(droplet)}")
    held(droplet, run)
    droplet_id = int(droplet["id"])
    if args.mode == "clean":
        # clean destroys the host whatever state it is in (its disk holds the copy, and goes
        # with it), held to its name and tags alone. One whose key or reserved IP is not as
        # made is never reached: only destroyed.
        try:
            hostkey = hostkey_of(droplet)
        except Refused as exc:
            print(f"{exc}: the host is not reached, and its copy goes with it")
            hostkey = ""
        if hostkey and reserved_on(address) != droplet_id:
            print("the reserved IP is not on this run's import host: the host is not reached, "
                  "and its copy goes with it")
            hostkey = ""
        write_outputs(args.output, {"id": str(droplet_id), "address": address if hostkey else "",
                                    "hostkey": hostkey})
        return 0
    hostkey = hostkey_of(droplet)
    if args.mode == "census":
        wait_active(droplet_id)
        assign(address, droplet_id)
    elif reserved_on(address) != droplet_id:
        raise Refused("the reserved IP is not on this run's import host: run census again")
    write_outputs(args.output, {"id": str(droplet_id), "address": address, "hostkey": hostkey})
    return 0


def write_outputs(path: str, values: dict[str, str]) -> None:
    with open(path, "a", encoding="utf-8") as out:
        for name, value in values.items():
            out.write(f"{name}={value}\n")


def ssh(known_hosts: str, identity: str, address: str, command: str,
        timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", "-i", identity, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
         f"root@{address}", command],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout, check=False)


def pin(args: argparse.Namespace) -> int:
    address = reserved_ip()
    if not re.fullmatch(r"[0-9a-f]{64}", args.hostkey):
        raise Refused("the host key's tag is no SHA-256")
    pinned = None
    for _ in range(90):
        scan = subprocess.run(["ssh-keyscan", "-T", "10", "-t", "ed25519", address],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              check=False)
        for line in scan.stdout.splitlines():
            fields = line.split()
            if len(fields) < 3 or line.startswith("#") or fields[1] != "ssh-ed25519":
                continue
            try:
                blob = base64.b64decode(fields[2], validate=True)
            except ValueError:
                continue
            if hashlib.sha256(blob).hexdigest() == args.hostkey:
                pinned = f"{address} ssh-ed25519 {fields[2]}\n"
        if pinned:
            break
        sleep(10)
    if not pinned:
        raise Refused("the host offered no key whose SHA-256 is its tag's")
    Path(args.known_hosts).write_text(pinned, encoding="utf-8")
    print("the import host's key is pinned from its tag")
    for _ in range(90):
        marker = ssh(args.known_hosts, args.identity, address, f"cat {MARKER}")
        if marker.returncode == 0 and marker.stdout.strip() == args.run:
            print("the import host is set up for this run")
            return 0
        sleep(10)
    raise Refused("the import host's set-up did not finish (its marker never named the run)")


def egress_command(run: str, mode: str) -> str:
    """The host's shell line: the trace in the run's own Node, by the digest census pinned.

    Only census falls back to the tag, node:22: its first check runs before window.sh has
    pulled it and kept the run's digest (a census of a run that has a copy uses the copy's).
    Every later mode refuses a run whose copy names no digest, as window.sh's `use_image`.
    """
    fallback = "image=node:22" if mode == "census" else "echo image=none; exit 3"
    kept = shlex.quote(f"/var/tmp/ta-import-{run}/image")
    return (f"image=\"$(cat {kept} 2>/dev/null || true)\"; "
            "printf '%s\\n' \"$image\" | grep -Eqx 'node@sha256:[0-9a-f]{64}' "
            f"|| {{ {fallback}; }}; "
            + shlex.join(["docker", "run", "--rm", "--network", "bridge"]) + ' "$image" '
            + shlex.join(["node", "-e", TRACE_JS]))


def egress(args: argparse.Namespace) -> int:
    address = reserved_ip()
    trace = ssh(args.known_hosts, args.identity, address, egress_command(args.run, args.mode),
                timeout=600)
    if "image=none" in trace.stdout.splitlines():
        raise Refused("this run's copy names no Node image: run clean, then census")
    seen = [line[3:].strip() for line in trace.stdout.splitlines() if line.startswith("ip=")]
    if trace.returncode != 0 or len(seen) != 1:
        raise Refused("the egress check could not read what Cloudflare sees")
    if seen[0] != address:
        raise Refused("Cloudflare sees the import host's containers from another address than "
                      "the reserved IP: no credential is handed to it")
    if "metadata=blocked" not in trace.stdout.splitlines():
        raise Refused("a container on the import host reaches the metadata service, which "
                      "serves its host key: no credential is handed to it")
    print("Cloudflare sees the import host's containers from the reserved IP")
    return 0


def destroy(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", args.id):
        raise Refused("a Droplet's id is a positive number")
    droplet = call("GET", f"/droplets/{args.id}")["droplet"]
    if int(droplet.get("id", 0)) != int(args.id):
        raise Refused(f"Droplet {args.id} answered as another")
    held(droplet, args.run)
    try:
        call("DELETE", f"/droplets/{args.id}")
    except DigitalOceanRequestError as exc:
        print(f"::error::the import host's deletion failed: Droplet {args.id} may still be "
              f"billing; delete it by hand ({exc})")
        return 1
    print(f"the run's import host (Droplet {args.id}) is destroyed")
    return 0


def hosts(args: argparse.Namespace) -> int:
    found = import_hosts()
    lines = [f"- {describe(each)}" for each in found] or ["- none"]
    print("import hosts up:\n" + "\n".join(lines))
    old = [each for each in found if age(each) > ORPHAN_DAYS]
    for each in old:
        print(f"::warning::an import host has been up past {ORPHAN_DAYS} days: "
              f"{describe(each)}. Clean its run, or delete it")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as out:
            out.write("### Import hosts up\n" + "\n".join(lines) + "\n")
            for each in old:
                out.write(f"\n**Past {ORPHAN_DAYS} days:** {describe(each)}\n")
    return 0


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="host.py")
    sub = parser.add_subparsers(dest="command", required=True)
    each = sub.add_parser("hosts")
    each.add_argument("--summary", default="")
    each = sub.add_parser("ensure")
    each.add_argument("--run", required=True)
    each.add_argument("--mode", required=True)
    each.add_argument("--deploy-key", required=True)
    each.add_argument("--output", required=True)
    each = sub.add_parser("pin")
    each.add_argument("--run", required=True)
    each.add_argument("--hostkey", required=True)
    each.add_argument("--known-hosts", required=True)
    each.add_argument("--identity", required=True)
    each = sub.add_parser("egress")
    each.add_argument("--run", required=True)
    each.add_argument("--mode", required=True)
    each.add_argument("--known-hosts", required=True)
    each.add_argument("--identity", required=True)
    each = sub.add_parser("destroy")
    each.add_argument("--run", required=True)
    each.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    if "run" in args and not RUN.match(args.run):
        raise Refused("a run's id is lowercase letters, digits and dashes, at most 64")
    return args


COMMANDS = {"hosts": hosts, "ensure": ensure, "pin": pin, "egress": egress,
            "destroy": destroy}


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse(sys.argv[1:] if argv is None else argv)
        return COMMANDS[args.command](args)
    except Refused as exc:
        print(f"::error::refused: {exc}")
        return 2
    except DigitalOceanRequestError as exc:
        print(f"::error::refused: a DigitalOcean request failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
