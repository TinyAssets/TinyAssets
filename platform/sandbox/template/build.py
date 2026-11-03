"""The sandbox's versioned E2B template (slice S; engines-replan.md section 2.1 step 3).

Built from the records as data: every served npm engine record's CLI, installed from the lock
committed beside it (`platform/sources/locks/<id>/`, its digest checked against the record's
`lock_sha256` before anything is sent), each engine's own unprivileged user (`ta-<id>`, home
`/home/ta-<id>`, mode 0700, in group `ta`, never in `sudo`), the shared workspace
(`/workspace`, root:ta 2775), the runner (`runner/runner.ts` bundled, `/opt/ta/runner`,
root-only) and its command (`/usr/local/bin/ta-runner`, 0700), and the root-only state
directory (`/var/lib/ta`, 0700). The resolver is pinned by the runner's `prepare` at every
start and resume (E2B rewrites `/etc/resolv.conf` at boot, probed), as `/proc`'s `hidepid=2`.

The tag is the digest of what the template is made of (the runner's bundle, the records, their
locks and this script), so a change to any of them is a new template version; the sandbox
Worker's `E2B_TEMPLATE` names it (`sandbox/guards/template.test.ts` holds the two equal).

    python3 -m venv v && v/bin/pip install e2b==2.62.0
    E2B_API_KEY=... v/bin/python platform/sandbox/template/build.py [--tag-only]
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLATFORM = HERE.parent.parent
SOURCES = PLATFORM / "sources"
NAME = "tinyassets-sandbox"
# Node from nodejs.org, pinned by its published SHA-256, on E2B's own base image (its
# provisioning already in it); the engines' locks need Node 22.19 or later.
NODE = "v22.22.0"
NODE_TARBALL = f"node-{NODE}-linux-x64.tar.gz"
NODE_SHA256 = "c33c39ed9c80deddde77c960d00119918b9e352426fd604ba41638d6526a4744"
CPU = 2
MEMORY_MB = 1024


def engines():
    """Every served CLI record installed by npm, by id, with its install pins."""
    out = []
    for path in sorted(SOURCES.glob("*.json")):
        record = json.loads(path.read_text())
        cli = record.get("cli")
        if cli is None or record.get("unwired") or cli["install"]["manager"] != "npm":
            continue
        install = cli["install"]
        lock = SOURCES / "locks" / record["id"] / "package-lock.json"
        digest = hashlib.sha256(lock.read_bytes()).hexdigest()
        if digest != install["lock_sha256"]:
            raise SystemExit(f"{record['id']}: its lock is not the record's ({digest})")
        out.append({"id": record["id"], "bin": install["bin"],
                    "scripts": install.get("lifecycle_scripts") is True})
    return out


def bundle(out: Path) -> bytes:
    subprocess.run(["node", str(HERE / "bundle.mjs"), str(out)], check=True,
                   stdout=subprocess.DEVNULL)
    return (out / "runner.mjs").read_bytes()


def tag(runner: bytes) -> str:
    """The template's version: the digest of the bundle, the records, their locks, this script."""
    digest = hashlib.sha256()
    parts = [("runner.mjs", runner), ("build.py", Path(__file__).read_bytes()),
             ("ta-runner", (HERE / "ta-runner").read_bytes())]
    parts += [(str(p.relative_to(SOURCES)), p.read_bytes())
              for p in sorted(SOURCES.rglob("*")) if p.is_file()]
    for name, data in parts:
        digest.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
    return "s" + digest.hexdigest()[:16]


def template(stage: Path):
    """The template, its copied files staged in `stage` (E2B copies from its file context)."""
    import shutil

    from e2b import Template

    built = engines()
    shutil.copy(HERE / "ta-runner", stage / "ta-runner")
    for e in built:
        shutil.copytree(SOURCES / "locks" / e["id"], stage / "locks" / e["id"])
    users = " ".join(f"ta-{e['id']}" for e in built)
    t = (Template(file_context_path=stage).from_base_image()
         .set_user("root")
         .run_cmd(f"cd /tmp && curl -fsSLO https://nodejs.org/dist/{NODE}/{NODE_TARBALL} && "
                  f"echo '{NODE_SHA256}  {NODE_TARBALL}' | sha256sum -c - && "
                  # The base image's own Node 20 and npm, under /usr/local, gone first: an
                  # npm extracted over another is broken.
                  "rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx "
                  "/usr/local/bin/corepack /usr/local/include/node && "
                  # Owned by root, whatever uid the tarball names (1001, the first engine
                  # user's, would own the runner's own Node), and written by root alone.
                  f"tar -xzf {NODE_TARBALL} -C /usr/local --strip-components=1 --no-same-owner "
                  f"&& rm {NODE_TARBALL} && chown -R root:root /usr/local && "
                  f"chmod -R go-w /usr/local && node --version | grep -qx {NODE}")
         .run_cmd("groupadd -r ta && "
                  f"for u in {users}; do useradd -m -d /home/$u -s /bin/bash -G ta $u "
                  "&& chmod 700 /home/$u; done && "
                  f"for u in {users} user; do gpasswd -d $u sudo 2>/dev/null || true; done")
         .run_cmd("mkdir -p /var/lib/ta/inbox /opt/ta/runner /opt/ta/engines /workspace && "
                  "chmod 700 /var/lib/ta /opt/ta/runner && chown root:ta /workspace && "
                  "chmod 2775 /workspace"))
    for e in built:
        t = t.copy(f"locks/{e['id']}/package.json", f"/opt/ta/engines/{e['id']}/package.json")
        t = t.copy(f"locks/{e['id']}/package-lock.json",
                   f"/opt/ta/engines/{e['id']}/package-lock.json")
        scripts = "" if e["scripts"] else " --ignore-scripts"
        t = t.run_cmd(f"cd /opt/ta/engines/{e['id']} && (/usr/local/bin/npm ci --omit=dev "
                      f"--no-audit --no-fund{scripts} || (cat /root/.npm/_logs/*.log | tail -n 60; "
                      f"exit 1)) && test -x node_modules/.bin/{e['bin']}")
    t = (t.copy("runner.mjs", "/opt/ta/runner/runner.mjs", user="root", mode=0o600)
         .copy("ta-runner", "/usr/local/bin/ta-runner", user="root", mode=0o700)
         # Root's alone, everything the runner or a CLI runs from; checked, never assumed
         # (an engine's user that could write either would run as root).
         .run_cmd("chown -R root:root /opt/ta /usr/local && chmod -R go-w /opt/ta /usr/local "
                  "&& chmod 600 /opt/ta/runner/runner.mjs && chmod 700 /opt/ta/runner "
                  "/usr/local/bin/ta-runner && "
                  "test $(stat -c %a /usr/local/bin/ta-runner) = 700 && "
                  "test -z \"$(find /opt/ta /usr/local -perm /022 ! -type l)\""))
    return t


def main():
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        runner = bundle(stage)
        name = f"{NAME}:{tag(runner)}"
        if "--tag-only" in sys.argv:
            print(name)
            return
        from e2b import Template
        info = Template.build(template(stage), name, cpu_count=CPU, memory_mb=MEMORY_MB,
                              on_build_logs=lambda entry: print(str(entry)[:2000], flush=True))
        print(json.dumps({"template": name, "template_id": getattr(info, "template_id", None),
                          "build_id": getattr(info, "build_id", None)}))


if __name__ == "__main__":
    os.chdir(PLATFORM)
    main()
