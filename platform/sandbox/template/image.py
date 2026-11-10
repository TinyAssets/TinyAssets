"""The sandbox's E2B image: its spec (`image.ts`) as E2B's template steps, and their build.

Every name, path, mode, pin and engine is the spec's, and every file is the staging
directory's (`image.ts --stage`); this holds the steps alone. It is in the image's tag, so
any change to it is a new image, a paid build.

    python3 image.py build <name:tag> <stage dir> <spec.json>
"""
import json
import sys
from pathlib import Path


def octal(mode):
    return format(mode, "o")


def template(stage, spec):
    """The image's steps, its copied files staged in `stage` (E2B copies from its file
    context)."""
    from e2b import Template

    node, sync, pool, state = spec["node"], spec["sync"], spec["pool"], spec["state"]
    usr, version, tarball = node["prefix"], node["version"], f"node-{node['version']}-linux-x64.tar.gz"
    opt, runners, engines = spec["opt"], spec["runners"], spec["engines_dir"]
    workspace, loader, group = spec["workspace"], spec["loader"], spec["group"]
    t = (Template(file_context_path=stage).from_base_image()
         .set_user("root")
         .run_cmd(f"cd /tmp && curl -fsSLO https://nodejs.org/dist/{version}/{tarball} && "
                  f"echo '{node['sha256']}  {tarball}' | sha256sum -c - && "
                  # The base image's own Node 20 and npm, under the prefix, gone first: an
                  # npm extracted over another is broken.
                  f"rm -rf {usr}/lib/node_modules/npm {usr}/bin/npm {usr}/bin/npx "
                  f"{usr}/bin/corepack {usr}/include/node && "
                  # Owned by root, whatever uid the tarball names (1001, the first engine
                  # user's, would own the runner's own Node), and written by root alone.
                  f"tar -xzf {tarball} -C {usr} --strip-components=1 --no-same-owner "
                  f"&& rm {tarball} && chown -R root:root {usr} && "
                  f"chmod -R go-w {usr} && node --version | grep -qx {version}")
         .run_cmd(f"groupadd -r {group} && "
                  f"useradd -r -M -d {spec['no_home']} -s {sync['shell']} -U {sync['user']} && "
                  f"for n in $(seq 0 {pool['size'] - 1}); do useradd -M -d {spec['no_home']} "
                  f"-s {pool['shell']} "
                  f"-U -G {group} {pool['prefix']}$n; done && "
                  f"gpasswd -d {spec['e2b_user']} sudo 2>/dev/null || true; "
                  "rm -f /etc/sudoers.d/* && "
                  # No user but root may sudo: checked, never assumed.
                  f"for u in {spec['e2b_user']} {sync['user']} {pool['prefix']}0; "
                  "do sudo -l -U $u | grep -q 'not allowed'; done")
         .run_cmd(f"mkdir -p {state['inbox']} {sync['dir']} {runners['dir']} {engines} "
                  f"{workspace['dir']} && chmod {octal(state['mode'])} {state['dir']} && "
                  f"chown {sync['user']}:{sync['user']} {sync['dir']} "
                  f"&& chmod {octal(sync['mode'])} {sync['dir']} && "
                  f"chown root:{group} {workspace['dir']} && "
                  f"chmod {octal(workspace['mode'])} {workspace['dir']}"))
    for e in spec["engines"]:
        home = f"{engines}/{e['id']}"
        t = t.copy(f"locks/{e['id']}/package.json", f"{home}/package.json")
        t = t.copy(f"locks/{e['id']}/package-lock.json", f"{home}/package-lock.json")
        scripts = "" if e["scripts"] else " --ignore-scripts"
        t = t.run_cmd(f"cd {home} && ({usr}/bin/npm ci --omit=dev "
                      f"--no-audit --no-fund{scripts} || (cat /root/.npm/_logs/*.log | tail -n 60; "
                      f"exit 1)) && test -x node_modules/.bin/{e['bin']}")
    path = loader["path"]
    t = (t.copy("ta-runner", path, user="root", mode=loader["mode"])
         # Root's alone to write, everything the runner or a CLI runs from; checked, never
         # assumed (a run's user that could write either would run as root). A runner the
         # loader installs holds no secret (the lease's are in the state directory), so the
         # sync agent may read it; the loader's uploads wait in the root-only state directory.
         .run_cmd(f"mkdir -p {state['incoming']} && "
                  f"chmod {octal(state['incoming_mode'])} {state['incoming']} && "
                  f"chown -R root:root {opt} {usr} && chmod -R go-w {opt} {usr} "
                  f"&& chmod {octal(runners['mode'])} {runners['dir']} && "
                  f"test -z \"$(ls -A {runners['dir']})\" && "
                  f"chmod {octal(loader['mode'])} {path} && "
                  f"test $(stat -c %a {path}) = {octal(loader['mode'])} && "
                  f"test -z \"$(find {opt} {usr} -perm /022 ! -type l)\""))
    return t


def main(argv):
    if len(argv) != 5 or argv[1] != "build":
        raise SystemExit("image.py build <name:tag> <stage dir> <spec.json>")
    from e2b import Template

    name, stage, spec = argv[2], Path(argv[3]), json.loads(Path(argv[4]).read_text())
    info = Template.build(template(stage, spec), name, cpu_count=spec["cpu"],
                          memory_mb=spec["memory_mb"],
                          on_build_logs=lambda entry: print(str(entry)[:2000], flush=True))
    print(json.dumps({"template": name, "template_id": info.template_id,
                      "build_id": info.build_id}), flush=True)


if __name__ == "__main__":
    main(sys.argv)
