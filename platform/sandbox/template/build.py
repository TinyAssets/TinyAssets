"""The sandbox's versioned E2B template (slice S; engines-replan.md section 2.1 step 3).

Built from the records as data: every served npm engine record's CLI, installed from the lock
committed beside it (`platform/sources/locks/<id>/`, its digest checked against the record's
`lock_sha256` before anything is sent); the pool of run users (`ta-run-<n>`, one a live run,
as many as the most live runs an owner may hold, `RUNS_CEILING` in `owner/src/tier.ts`; each
its own group, in group `ta`, no home: the runner makes each run's home) and the sync agent's
user (`ta-sync`, its directory `/var/lib/ta-sync` 0700), none able to `sudo` (checked); the
shared workspace (`/workspace`, root:ta 2775); the runner's loader
(`/usr/local/bin/ta-runner`, 0700) and the empty directory it installs runners into
(`/opt/ta/runner`, root 0755, readable by the sync agent, which runs the runner, and written by
root alone); and the root-only state directory (`/var/lib/ta`, 0700, its `incoming` the
loader's uploads). The resolver is pinned by the runner's `prepare` at every start and resume
(E2B rewrites `/etc/resolv.conf` at boot, probed), as `/proc`'s `hidepid=2`.

The template holds no runner. The sandbox Worker ships it (`sandbox/src/runner-bytes.ts`, its
build's `runner.mjs`) and names it by its SHA-256 at every command, which the loader checks, so
a runner change is a Worker deploy, never a template build. The tag is the digest of what the
image is made of (this script, the loader, the run pool's size, the engines' locks and their
install pins), so only a change to those is a new template version; the sandbox Worker's
`E2B_TEMPLATE` names it (`sandbox/guards/template.test.ts` holds this script's tag and its own
equal).

    python3 -m venv v && v/bin/pip install e2b==2.62.0
    E2B_API_KEY=... v/bin/python platform/sandbox/template/build.py \
        [--tag-only | --check-pair | --check-logins | --route-probe | --check-matcher]

Behaviour is a pair's, the template and the runner: every build, and every pair check
(`--check-pair`: no build, the pinned template with this checkout's runner; the deploy kit runs
it before a deploy whose pair has not passed), runs three standing checks on throwaway
sandboxes, the runner uploaded and installed in each as the Worker installs it: every record's
sign-in up to its prompt (`check_logins`), every keyed engine's first model request reaching
the egress door's own path with nothing else leaving the VM (`route_probe`), and the
sync-ignore automaton's step within `STEP_NS` (`check_matcher`). Only once all pass is the pair
recorded: in `passed.json` beside this script (each pair that passed, with what its checks
found, and the runner's upload and install as measured), and at E2B as the template's tag
`<tag>-r<runner's first 16>` (`pair_tag`), which the deploy kit looks for.
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
TIER = PLATFORM / "owner" / "src" / "tier.ts"
NETWORK = PLATFORM / "owner" / "src" / "network.ts"
PASSED = HERE / "passed.json"


def runs_ceiling() -> int:
    """`RUNS_CEILING`, the most live runs an owner may hold: the pool's size."""
    import re
    found = re.search(r"export const RUNS_CEILING = ([0-9_]+);", TIER.read_text())
    if found is None:
        raise SystemExit("owner/src/tier.ts names no RUNS_CEILING")
    return int(found.group(1).replace("_", ""))


def network_constants():
    """The egress door's host and the pinned resolvers, as the owner's network plan names them
    (`EGRESS_HOST`, `DNS_SERVERS`): the production network's fixed parts."""
    import re
    text = NETWORK.read_text()
    host = re.search(r'export const EGRESS_HOST = "([^"]+)";', text)
    servers = re.search(r"export const DNS_SERVERS: readonly string\[\] = Object\.freeze\("
                        r"\[([^\]]*)\]\);", text)
    if host is None or servers is None:
        raise SystemExit("owner/src/network.ts names no EGRESS_HOST or DNS_SERVERS")
    return host.group(1), re.findall(r'"([^"]+)"', servers.group(1))


def engine_hosts(cli, region):
    """An engine's vendor hosts, its sign-in's and its model calls', and the chosen region's:
    what the sandbox Worker widens a sign-in's network by (`engineHosts`)."""
    chosen = (cli.get("regions") or {}).get(region, {}).get("hosts", {}) if region else {}
    return sorted({*cli["hosts"]["login"], *cli["hosts"]["provider"],
                   *chosen.get("login", []), *chosen.get("provider", [])})


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
    """The runner as the sandbox Worker ships it (`bundle.mjs`, its build's own command)."""
    subprocess.run(["node", str(HERE / "bundle.mjs"), str(out)], check=True,
                   stdout=subprocess.DEVNULL)
    return (out / "runner.mjs").read_bytes()


def tag() -> str:
    """The template's version: the digest of what the image is made of, and of nothing else
    (no runner: the Worker ships it). The template guard computes the same."""
    digest = hashlib.sha256()
    parts = [("build.py", Path(__file__).read_bytes()),
             ("ta-runner", (HERE / "ta-runner").read_bytes()),
             ("runs_ceiling", str(runs_ceiling()).encode()),
             ("engines", json.dumps(engines(), sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode())]
    parts += [(str(p.relative_to(SOURCES)), p.read_bytes())
              for p in sorted((SOURCES / "locks").rglob("*")) if p.is_file()]
    for name, data in parts:
        digest.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
    return "s" + digest.hexdigest()[:16]


def pair_tag(tag_: str, runner_sha: str) -> str:
    """The E2B tag a passed pair is marked with: the template's tag and the runner's."""
    return f"{tag_}-r{runner_sha[:16]}"


def install_runner(sandbox, runner: bytes) -> dict:
    """The runner uploaded and installed as the sandbox Worker does it (`control.ts`
    `#install`), timed: the cost of a sandbox that does not have it yet."""
    import time
    sha = hashlib.sha256(runner).hexdigest()
    began = time.monotonic()
    sandbox.files.write(f"/var/lib/ta/incoming/{sha}", runner, user="root")
    uploaded = time.monotonic()
    sandbox.commands.run(f"/usr/local/bin/ta-runner install {sha}", user="root", timeout=120)
    done = time.monotonic()
    return {"bytes": len(runner), "upload_ms": round((uploaded - began) * 1000),
            "install_ms": round((done - uploaded) * 1000)}


def runner_command(sha: str, command: str) -> str:
    return f"/usr/local/bin/ta-runner {sha} {command}"


def template(stage: Path):
    """The template, its copied files staged in `stage` (E2B copies from its file context)."""
    import shutil

    from e2b import Template

    built = engines()
    shutil.copy(HERE / "ta-runner", stage / "ta-runner")
    for e in built:
        shutil.copytree(SOURCES / "locks" / e["id"], stage / "locks" / e["id"])
    pool = runs_ceiling()
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
                  "useradd -r -M -d /nonexistent -s /usr/sbin/nologin -U ta-sync && "
                  f"for n in $(seq 0 {pool - 1}); do useradd -M -d /nonexistent -s /bin/bash "
                  "-U -G ta ta-run-$n; done && "
                  "gpasswd -d user sudo 2>/dev/null || true; rm -f /etc/sudoers.d/* && "
                  # No user but root may sudo: checked, never assumed.
                  "for u in user ta-sync ta-run-0; do sudo -l -U $u | grep -q 'not allowed'; done")
         .run_cmd("mkdir -p /var/lib/ta/inbox /var/lib/ta-sync /opt/ta/runner /opt/ta/engines "
                  "/workspace && chmod 700 /var/lib/ta && chown ta-sync:ta-sync /var/lib/ta-sync "
                  "&& chmod 700 /var/lib/ta-sync && chown root:ta /workspace && "
                  "chmod 2775 /workspace"))
    for e in built:
        t = t.copy(f"locks/{e['id']}/package.json", f"/opt/ta/engines/{e['id']}/package.json")
        t = t.copy(f"locks/{e['id']}/package-lock.json",
                   f"/opt/ta/engines/{e['id']}/package-lock.json")
        scripts = "" if e["scripts"] else " --ignore-scripts"
        t = t.run_cmd(f"cd /opt/ta/engines/{e['id']} && (/usr/local/bin/npm ci --omit=dev "
                      f"--no-audit --no-fund{scripts} || (cat /root/.npm/_logs/*.log | tail -n 60; "
                      f"exit 1)) && test -x node_modules/.bin/{e['bin']}")
    t = (t.copy("ta-runner", "/usr/local/bin/ta-runner", user="root", mode=0o700)
         # Root's alone to write, everything the runner or a CLI runs from; checked, never
         # assumed (a run's user that could write either would run as root). A runner the
         # loader installs holds no secret (the lease's are in /var/lib/ta), so the sync agent
         # may read it; the loader's uploads wait in the root-only state directory.
         .run_cmd("mkdir -p /var/lib/ta/incoming && chmod 700 /var/lib/ta/incoming && "
                  "chown -R root:root /opt/ta /usr/local && chmod -R go-w /opt/ta /usr/local "
                  "&& chmod 755 /opt/ta/runner && test -z \"$(ls -A /opt/ta/runner)\" && "
                  "chmod 700 /usr/local/bin/ta-runner && "
                  "test $(stat -c %a /usr/local/bin/ta-runner) = 700 && "
                  "test -z \"$(find /opt/ta /usr/local -perm /022 ! -type l)\""))
    return t


def check_logins(name, runner):
    """The standing check of every record's sign-in on the template just built (slice SIGNIN):
    on a throwaway sandbox, each record's login run up to its prompt (`ta-runner login-probe`,
    each region of a record with regions) and stopped, its URL the record's pinned page and
    never sending the browser to localhost. Each runs under the production egress allowlist
    (review L3): the strictest plan, the egress door alone, widened by that engine's own hosts
    as the sandbox Worker widens a sign-in's, all else denied, the resolver pinned; so a
    vendor that adds a sign-in host fails here too. A pinned CLI's version bump that changes
    its sign-in fails the build here, not in a user's sign-in. No account is needed; the
    sandbox is killed whatever happens."""
    from e2b import ALL_TRAFFIC, Sandbox

    egress, servers = network_constants()

    def plan(hosts):
        return {"allow_out": [*hosts, egress], "deny_out": [ALL_TRAFFIC], "dns_servers": servers}

    results = []
    sha = hashlib.sha256(runner).hexdigest()
    sandbox = Sandbox.create(template=name, timeout=900, network=plan([]))
    try:
        install_runner(sandbox, runner)
        sandbox.commands.run(runner_command(sha, "prepare " + " ".join(servers)),
                             user="root", timeout=300)
        for path in sorted(SOURCES.glob("*.json")):
            record = json.loads(path.read_text())
            cli = record.get("cli") or {}
            if cli.get("login") is None or record.get("unwired"):
                continue
            for region in sorted(cli.get("regions") or {}) or [None]:
                sandbox.update_network(plan(engine_hosts(cli, region)))
                command = runner_command(sha, f"login-probe {record['id']}")
                if region is not None:
                    command += f" {region}"
                try:
                    done = sandbox.commands.run(command, user="root", timeout=180)
                    out = done.stdout
                except Exception as error:  # a non-zero exit: its answer is still printed
                    out = getattr(error, "stdout", "") or ""
                results.append(json.loads(out) if out.strip() else
                               {"engine": record["id"], "region": region, "ok": False,
                                "reason": "no answer"})
    finally:
        sandbox.kill()
    print(json.dumps({"logins": results}), flush=True)
    if not all(result["ok"] for result in results):
        raise SystemExit("a record's sign-in is not its record's shape on this template")
    return results


def route_probe(name, runner):
    """The route probe (codexurl_research.md Q4): on a throwaway sandbox of the template just
    built, `strace` installed while its network is still open (the template itself unchanged),
    then the network denied to everything but the pinned resolvers, every record that runs on
    an API key run on every provider it keys as a run runs it (`ta-runner route-probe`: the
    real invocation and home, the door at its real path on a loopback capture server, a fake
    sign-in seeded in the engine's store). It passes only if each first model request reached
    the door's path with the run's key and secret, nothing connected outside, and no request
    carried the fake sign-in; otherwise the build fails loudly. The sandbox is killed whatever
    happens."""
    from e2b import ALL_TRAFFIC, Sandbox

    _egress, servers = network_constants()
    sha = hashlib.sha256(runner).hexdigest()
    sandbox = Sandbox.create(template=name, timeout=1800)
    try:
        install_runner(sandbox, runner)
        installed = sandbox.commands.run(
            "(command -v strace || (apt-get -qq update && apt-get -qq install -y strace)) "
            ">/dev/null 2>&1; command -v strace", user="root", timeout=600)
        if not installed.stdout.strip():
            raise SystemExit("the route probe's sandbox has no strace")
        sandbox.commands.run(runner_command(sha, "prepare " + " ".join(servers)),
                             user="root", timeout=300)
        sandbox.update_network({"deny_out": [ALL_TRAFFIC], "dns_servers": servers})
        try:
            done = sandbox.commands.run(runner_command(sha, "route-probe " + " ".join(servers)),
                                        user="root", timeout=1500)
            out, code = done.stdout, 0
        except Exception as error:  # a non-zero exit: its lines are still printed
            out, code = getattr(error, "stdout", "") or "", 1
    finally:
        sandbox.kill()
    results = [json.loads(line) for line in out.splitlines() if line.strip()]
    print(json.dumps({"routes": results}), flush=True)
    if code != 0 or not results or not all(result["ok"] for result in results):
        raise SystemExit("a keyed engine's model requests do not reach the egress door alone "
                         "on this template")
    return results


def step_ceiling() -> float:
    """`STEP_NS`, the most one sync-ignore automaton step may take in the sandbox."""
    import re
    found = re.search(r"export const STEP_NS = ([0-9.]+);",
                      (PLATFORM / "owner" / "src" / "sync-ignore.ts").read_text())
    if found is None:
        raise SystemExit("owner/src/sync-ignore.ts names no STEP_NS")
    return float(found.group(1))


def check_matcher(name, runner):
    """The sync-ignore automaton's step (`stepNanos`, a byte of text a 32-state word, at
    `AUTOMATON_STATES` with every state alive), timed on a throwaway sandbox of the template
    just built: past `STEP_NS`, the constant `AUTOMATON_STATES` is derived from, the build
    fails loudly. The sandbox is killed whatever happens."""
    from e2b import Sandbox

    sha = hashlib.sha256(runner).hexdigest()
    sandbox = Sandbox.create(template=name, timeout=300)
    try:
        installed = install_runner(sandbox, runner)
        done = sandbox.commands.run(runner_command(sha, "matcher-probe"), user="root",
                                    timeout=120)
    finally:
        sandbox.kill()
    result = json.loads(done.stdout.strip().splitlines()[-1])
    result["install"] = installed
    result["ceiling_ns"] = step_ceiling()
    result["ok"] = result["step_ns"] <= result["ceiling_ns"]
    print(json.dumps({"matcher": result}), flush=True)
    if not result["ok"]:
        raise SystemExit(f"the sync-ignore automaton's step takes {result['step_ns']:.2f} ns "
                         f"in the sandbox, past STEP_NS ({result['ceiling_ns']})")
    return result


def record_pass(name, runner, logins, routes, matcher):
    """The pair that passed every check, with what they found, kept in `passed.json` (one
    entry a pair, newest last; the template guard holds the pinned tag to one), and marked at
    E2B with its pair tag, which the deploy kit looks for before a deploy."""
    import datetime

    import httpx
    passed = json.loads(PASSED.read_text()) if PASSED.exists() else []
    tag_ = name.split(":", 1)[1]
    sha = hashlib.sha256(runner).hexdigest()
    passed = [entry for entry in passed
              if (entry["tag"], entry.get("runner")) != (tag_, sha)]
    passed.append({
        "tag": tag_,
        "runner": sha,
        "passed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "logins": [{"engine": r["engine"], "region": r["region"], "ok": r["ok"],
                    "host": r.get("host")} for r in logins],
        "routes": [{"engine": r["engine"], "provider": r["provider"], "ok": r["ok"],
                    "turns": r.get("turns"), "compacted": r.get("compacted")} for r in routes],
        "matcher": {"step_ns": round(matcher["step_ns"], 3), "ceiling_ns": matcher["ceiling_ns"],
                    "ok": matcher["ok"]},
        "install": matcher["install"],
    })
    PASSED.write_text(json.dumps(passed, indent=2) + "\n")
    # E2B's own tags (its OpenAPI `AssignTemplateTagsRequest`): the pair, on the build the
    # template's tag names.
    answer = httpx.post("https://api.e2b.app/templates/tags",
                        headers={"X-API-Key": os.environ["E2B_API_KEY"]},
                        json={"target": name, "tags": [pair_tag(tag_, sha)]}, timeout=60)
    if answer.status_code >= 300:
        raise SystemExit(f"E2B did not tag the passed pair ({answer.status_code})")


def pinned() -> str:
    """The template the sandbox Worker names (`wrangler.jsonc`'s `E2B_TEMPLATE`)."""
    import re
    found = re.search(r'"E2B_TEMPLATE": "([^"]+)"',
                      (PLATFORM / "sandbox" / "wrangler.jsonc").read_text())
    if found is None:
        raise SystemExit("sandbox/wrangler.jsonc names no E2B_TEMPLATE")
    return found.group(1)


def main():
    name = f"{NAME}:{tag()}"
    if "--tag-only" in sys.argv:
        print(name)
        return
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        runner = bundle(stage / "runner")
        if "--check-pair" in sys.argv:
            # The pinned template with this checkout's runner: no build.
            name = pinned()
        if "--check-logins" in sys.argv:
            check_logins(name, runner)
            return
        if "--route-probe" in sys.argv:
            route_probe(name, runner)
            return
        if "--check-matcher" in sys.argv:
            check_matcher(name, runner)
            return
        info = None
        if "--check-pair" not in sys.argv:
            from e2b import Template
            info = Template.build(template(stage), name, cpu_count=CPU, memory_mb=MEMORY_MB,
                                  on_build_logs=lambda entry: print(str(entry)[:2000],
                                                                    flush=True))
        logins = check_logins(name, runner)
        routes = route_probe(name, runner)
        matcher = check_matcher(name, runner)
        # Every standing check passed: only now the pair's record and its tag at E2B.
        record_pass(name, runner, logins, routes, matcher)
        print(json.dumps({"template": name, "runner": hashlib.sha256(runner).hexdigest(),
                          "install": matcher["install"],
                          "template_id": getattr(info, "template_id", None),
                          "build_id": getattr(info, "build_id", None)}))


if __name__ == "__main__":
    os.chdir(PLATFORM)
    main()
