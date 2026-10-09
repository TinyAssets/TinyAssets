"""The cutover image, end to end on a PR1-migrated volume (task 2.5).

One path only: there is no env switch, no OFF/reverse/legacy leg and no
old-image leg. Everything below runs against ONE data volume that this script
builds in production shape, migrates with the image's own
``/usr/local/libexec/ta-migrate.py`` exactly as
``docs/ops/owner-split-cutover-runbook.md`` step 4 does, and then serves.

    python scripts/role_image_oracle.py --image cutint-oracle:<sha>

Stages, in order:

1. **migrate** - a fresh named volume gets the production-shaped layout-2
   fixture from ``scripts/role_migrate_probe.build`` (three centers, three
   principals, a vault, ``outbound.db`` with an uncheckpointed WAL, egress
   proxy state, liveness, sidecars, admission staging, a community pool), then
   the runbook's ``--check`` / ``--snapshot`` / ``--check`` trio. The second
   check must print nothing.
2. **serve** - the image's real ``CMD`` in ``deploy/compose.yml``'s posture
   (``user: "0:0"``, ``cap_drop: ALL``, ``cap_add: [KILL, SETGID, SETUID,
   SETPCAP]``, the four ``security_opt`` lines) on the migrated volume. Proved
   from the HOST, with no application import in the probe: ``docker top`` plus
   ``/proc/<pid>/status`` for PID1, the broker and the mapper, and the compose
   healthcheck ``ta-op pulse``.
3. **cells** - the same image, the same posture and the same volume, with this
   script's in-container program as PID1. It calls the image's own
   ``ta-launch.py`` ``boot()`` - the production bootstrap, unmodified - and then
   drives the owner-cell classes as the retired daemon, which is the only
   identity the mapper authenticates (``SCM_CREDENTIALS`` pinned to PID 1).
4. **providers** - ``scripts/role_provider_cell_probe.py`` against the same
   image: the real Claude and Codex CLIs in real ``provider-exec`` cells.

The in-container legs are a tuple of one-argument-free functions. A new cell
class is one function plus its name in ``LEGS``; the host passes ``--legs`` to
select. ``workspace-remote`` (branch ``iso/cutover-ws``) lands as
``leg_workspace_remote``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

# deploy/compose.yml, daemon service: the serving posture, verbatim.
COMPOSE_USER = "0:0"
COMPOSE_CAPS = ("KILL", "SETGID", "SETUID", "SETPCAP")
COMPOSE_SECURITY = ("seccomp=unconfined", "apparmor=unconfined", "systempaths=unconfined",
                    "no-new-privileges=true")
COMPOSE_ENV = {
    "HOME": "/home/tinyassets",
    "TINYASSETS_DATA_DIR": "/data",
    "TINYASSETS_REPO_ROOT": "/data/community-pool",
    "TINYASSETS_CLOUD_DAEMON_SUBSCRIPTION_ONLY": "1",
    "TINYASSETS_GOAL_POOL": "off",
    "TINYASSETS_ONBOARDING_APP": "1",
    "TINYASSETS_ALLOW_CLAUDE_SERVING": "1",
}
# docs/ops/owner-split-cutover-runbook.md step 4: the one-shot migration's
# capabilities. The service image never holds them.
MIGRATION_CAPS = ("CHOWN", "FOWNER", "DAC_OVERRIDE")
LEG_NAMES = ("bootstrap", "daemon_reader", "admission", "tool_files", "provider_exec",
             "two_pass_delete")

#: The daemon serves only on an admitted cloud runtime: the link-local metadata
#: id must match the deploy-recorded one in the data root
#: (``tinyassets/platform_runtime_provenance.py``). The oracle therefore runs a
#: REAL metadata service on a docker network that owns the link-local address,
#: and records the same id on the volume. Nothing in the image is patched and no
#: reader is injected; the daemon makes its own HTTP read of a real endpoint.
METADATA_ADDRESS, METADATA_SUBNET = "169.254.169.254", "169.254.169.0/24"
METADATA_INSTANCE_ID = "4242424242"
METADATA_SERVER = """
import http.server
BODY = {body!r}.encode()
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = BODY if self.path == '/metadata/v1/id' else b''
        self.send_response(200 if body else 404)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *arguments):
        pass
http.server.HTTPServer(('0.0.0.0', 80), Handler).serve_forever()
"""


# --------------------------------------------------------------------------- #
# in-container program: PID1 runs the production bootstrap, then the legs
# --------------------------------------------------------------------------- #

CELLS = r'''
import asyncio, json, os, runpy, socket, stat, sys, threading, traceback
from pathlib import Path

sys.path.insert(0, '/app')
launch = runpy.run_path('/usr/local/libexec/ta-launch.py')
DATA = Path('/data')
WANTED = json.loads(os.environ['ORACLE_LEGS'])
PYTHON = '/usr/local/bin/python3.11'
CLAUDE = '/opt/claude-code-install/node_modules/.bin/claude'
NEW_PRINCIPAL, NEW_CENTER = 'dana', 'u-dana'

# The production bootstrap, unmodified: the layout-marker refusal, the admission
# log read through a fully retired broker child, the broker and bounded-mapper
# forks, then PID1's retirement to the capability-free daemon.
BINDINGS, (SUPERVISOR, CLIENT) = launch['boot'](launch)
BROKER_PID, MAPPER_PID = SUPERVISOR._bootstrap_pid, CLIENT._launcher_pid

from tinyassets import role_tools, universe_egress, workspace_fs
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.broker.owner_identities import owner_identity
from tinyassets.daemon_server import grant_universe_access
from tinyassets.providers.owned_process import aspawn_owned
from tinyassets.providers.provider_jail import provider_launch_scope
from tinyassets.role_center_admission import admit_center
from tinyassets.role_owner_tree_deletion import delete_center
from tinyassets.universe_files import read_universe_file


def proc(pid):
    """Kernel identity of one process, read from /proc as the daemon."""
    raw = Path('/proc', str(pid), 'status').read_text().splitlines()
    fields = dict(line.split(':', 1) for line in raw)
    return dict(pid=pid, uid=[int(v) for v in fields['Uid'].split()],
                gid=[int(v) for v in fields['Gid'].split()],
                caps={key: int(fields[key], 16) for key in launch['CAP_FIELDS']},
                nnp=int(fields['NoNewPrivs']))


def census(top, machine=None):
    """Names, bytes and unlistable directories under ``top``, as the daemon.

    An owner's 0700 directory is deliberately unreadable by uid 1001, so it is
    counted by its own label (lstat from the traversable parent) and reported
    rather than silently skipped. ``machine`` collects the entries that carry
    one owner identity.
    """
    names = total = 0
    unlistable, owned = [], []
    pending = [top]
    while pending:
        current = pending.pop()
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        names += 1
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
        if machine is not None and machine in (info.st_uid, info.st_gid):
            owned.append(str(current.relative_to(DATA)))
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            continue
        try:
            pending.extend(current / name for name in os.listdir(current))
        except PermissionError:
            unlistable.append(str(current.relative_to(DATA)))
    return dict(names=names, bytes=total, unlistable=sorted(unlistable), owned=sorted(owned))


def refuses(label, call):
    """Run ``call``; return the refusal. A success is the oracle's failure."""
    try:
        call()
    except Exception as exc:
        return f'{type(exc).__name__}: {str(exc)[:160]}'
    raise AssertionError('not refused: ' + label)


def center_of(principal):
    return next(center for (owner, center) in BINDINGS if owner == principal)


# --------------------------------------------------------------------------- #


def leg_bootstrap():
    """D60/D70: broker 1002, mapper host 300000, daemon 1001 with zero caps."""
    daemon, broker, mapper = proc(1), proc(BROKER_PID), proc(MAPPER_PID)
    assert os.getpid() == 1 and os.getresuid() == (1001, 1001, 1001), 'daemon identity'
    assert daemon['uid'] == [1001] * 4 and daemon['gid'] == [1001] * 4, daemon
    assert not any(daemon['caps'].values()) and daemon['nnp'] == 1, daemon
    assert broker['uid'] == [1002] * 4 and broker['gid'] == [1002] * 4, broker
    assert not any(broker['caps'].values()) and broker['nnp'] == 1, broker
    # The mapper's host uid, read from outside its user namespace.
    assert mapper['uid'] == [300000] * 4 and mapper['gid'] == [300000] * 4, mapper
    setuid_setgid = (1 << 6) | (1 << 7)
    assert mapper['caps']['CapEff'] == mapper['caps']['CapPrm'] == setuid_setgid, mapper
    assert not any(mapper['caps'][key] for key in ('CapBnd', 'CapInh', 'CapAmb')), mapper
    assert mapper['nnp'] == 1, mapper
    return dict(daemon=daemon, broker=broker, mapper=mapper,
                bindings={f'{owner}/{center}': machine
                          for (owner, center), machine in sorted(BINDINGS.items())})


def leg_daemon_reader():
    """A steered daemon reader never crosses owners (D65), three ways."""
    mine, theirs = center_of('bob'), center_of('alice')
    alias = DATA / mine / 'cross-owner-alias.md'
    if not alias.is_symlink():
        alias.symlink_to(DATA / theirs / 'notes' / 'n0.md')
    mine_fd = workspace_fs.open_dir_nofollow(DATA / mine)
    theirs_fd = workspace_fs.open_dir_nofollow(DATA / theirs)
    try:
        found = dict(
            planted_symlink=refuses('planted symlink into another owner', lambda:
                read_universe_file(DATA / mine, 'cross-owner-alias.md')),
            relative_escape=refuses('a relative path out of the center', lambda:
                read_universe_file(DATA / mine, '../' + theirs + '/notes/n0.md')),
            identity_mismatch=refuses("another owner's root under this owner's identity",
                lambda: workspace_fs.read_regular_file_beneath(
                    theirs_fd, 'notes/n0.md', max_bytes=4096,
                    expected_identity=(owner_identity(DATA, principal='bob').gid,) * 2)),
        )
        # The same reader on the owner's OWN file still works: these are
        # refusals of foreign bytes, not a broken reader.
        found['own_read'] = workspace_fs.read_regular_file_beneath(
            mine_fd, 'notes/n0.md', max_bytes=4096,
            expected_identity=(owner_identity(DATA, principal='bob').gid,) * 2).decode()
    finally:
        os.close(mine_fd)
        os.close(theirs_fd)
    alias.unlink()
    return found


def leg_admission():
    """DA3/DA4: a principal and center created after boot, bound with no restart.

    No restart happens anywhere here: the reservation, the center-root cell that
    labels the new root as the new uid, the ``admit`` row and the mapper's bind
    all run against the live broker and mapper this process forked at boot.
    """
    principal, center = NEW_PRINCIPAL, NEW_CENTER
    before = sorted(f'{owner}/{name}' for owner, name in BINDINGS)
    assert not any(name == center for _, name in BINDINGS), 'center was bound at boot'
    generation = admit_center(DATA, principal=principal, center=center)
    identity = owner_identity(DATA, principal=principal)
    assert generation > 0 and identity.uid == identity.gid > 300000, (generation, identity)
    grant_universe_access(DATA, universe_id=center, actor_id=principal,
                          permission='admin', granted_by=principal)
    root = (DATA / center).stat()
    assert (root.st_uid, root.st_gid, stat.S_IMODE(root.st_mode)) == (
        1001, identity.gid, 0o750), root
    # Idempotent by contract (DA4): the repeat re-binds the same generation.
    assert admit_center(DATA, principal=principal, center=center) == generation
    BINDINGS[(principal, center)] = identity.uid
    return dict(principal=principal, center=center, generation=generation,
                machine=identity.uid, bindings_before=before,
                root=[root.st_uid, root.st_gid, oct(stat.S_IMODE(root.st_mode))])


def leg_tool_files():
    """A tool-files cell for the center admitted after boot, as its own uid.

    This is the proof that the runtime bind is live: the mapper resolves the
    owner from its own binding table, so a cell for a center admitted after
    boot cannot start unless the ADMIT op bound it.
    """
    principal, center = NEW_PRINCIPAL, NEW_CENTER
    identity = owner_identity(DATA, principal=principal)
    with identity_context(Identity(principal, principal)):
        files = role_tools.prepare(DATA / center)
    workspace = (DATA / center / '.agent-workspace').stat()
    assert (workspace.st_uid, workspace.st_gid) == (identity.uid, identity.gid), workspace
    return dict(center=center, machine=identity.uid, files=files,
                workspace=[workspace.st_uid, workspace.st_gid])


def leg_provider_exec():
    """A provider-exec cell on the migrated volume: own uid, zero foreign bytes."""
    probe = r"""
import json, os, socket, sys
targets, port, outside = json.loads(bytes.fromhex(sys.argv[1]))
fields = dict(l.split(':', 1) for l in open('/proc/self/status').read().splitlines())
opened, wrote = [], []
for path in targets:
    try:
        os.listdir(path) if os.path.isdir(path) else open(path, 'rb').read()
        opened.append(path)
    except OSError:
        pass
    try:
        with open(os.path.join(path, 'oracle-write') if os.path.isdir(path) else path, 'ab'):
            pass
        wrote.append(path)
    except OSError:
        pass
try:
    socket.create_connection(tuple(outside), timeout=4).close(); direct = 'connected'
except OSError:
    direct = 'refused'
try:
    with socket.create_connection(('127.0.0.1', port), timeout=5) as c:
        engine = c.recv(64).decode().strip()
except OSError as exc:
    engine = 'unreachable: ' + type(exc).__name__
print(json.dumps(dict(uid=os.getuid(), gid=os.getgid(), caps=int(fields['CapEff'], 16),
    nnp=int(fields['NoNewPrivs']),
    fds=sorted(int(n) for n in os.listdir('/proc/self/fd') if n.isdigit()
               and os.path.exists('/proc/self/fd/' + n)),
    opened=opened, wrote=wrote, direct=direct, engine=engine)))
"""
    # A stand-in engine route on the daemon's loopback; the owner-checked relay
    # re-reads the route per connection, and here that read answers this port.
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(8)
    port = listener.getsockname()[1]

    def serve():
        while True:
            connection, _ = listener.accept()
            with connection:
                connection.sendall(b'ENGINE-ROUTE-OK\n')

    threading.Thread(target=serve, daemon=True).start()
    universe_egress._route_port = lambda actor_id, graph_id: port

    principal = 'bob'
    center = center_of(principal)
    other = center_of('alice')
    snapshot = DATA / center / '.runtime/provider-launch-credentials/snap'
    own = DATA / center / '.agent-workspace'
    targets = [str(DATA / other), str(DATA / other / 'notes' / 'n0.md'),
               str(DATA / other / '.credential-vault.json'),
               str(DATA / '.broker' / 'state'), str(DATA), str(own)]
    # An address the CONTAINER can reach, so "the cell cannot" is a real
    # refusal and not an artefact of a network that has no route at all.
    outside = (os.environ['ORACLE_OUTSIDE_HOST'], int(os.environ['ORACLE_OUTSIDE_PORT']))
    try:
        socket.create_connection(outside, timeout=4).close()
        reachable = True
    except OSError:
        reachable = False
    pipes = dict(stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                 stderr=asyncio.subprocess.PIPE)

    async def run(argv):
        with provider_launch_scope(DATA / center, credential_dir=snapshot,
                                   engine_route=(principal, center)):
            process = await aspawn_owned(argv, env={'TERM': 'dumb'}, **pipes)
        async with asyncio.timeout(180):
            return (await process.communicate(b''), process.returncode)

    with identity_context(Identity(principal, principal)):
        (out, err), code = asyncio.run(run(
            [PYTHON, '-I', '-S', '-c', probe,
             json.dumps([targets, port, list(outside)]).encode().hex()]))
        assert code == 0, (code, out[-2000:], err[-2000:])
        seen = json.loads(out)
        (version, verr), vcode = asyncio.run(run([CLAUDE, '--version']))
    inner = BINDINGS[(principal, center)] - 300000
    assert seen['uid'] == seen['gid'] == inner, seen
    assert seen['caps'] == 0 and seen['nnp'] == 1 and seen['fds'] == [0, 1, 2], seen
    assert seen['opened'] == [] and seen['wrote'] == [str(own)], seen
    assert reachable, 'the oracle network has no route to prove a cell refusal'
    assert seen['direct'] == 'refused' and seen['engine'] == 'ENGINE-ROUTE-OK', seen
    assert vcode == 0 and version.strip(), (vcode, version, verr[-2000:])
    return dict(center=center, cell=seen, claude=version.decode().strip(),
                daemon_reaches_outside=reachable, outside=list(outside))


def leg_two_pass_delete():
    """D10/D85/D218: owner pass, daemon pass, retire; zero bytes left."""
    principal = 'bob'
    center = center_of(principal)
    machine = BINDINGS[(principal, center)]
    grant_universe_access(DATA, universe_id=center, actor_id=principal,
                          permission='admin', granted_by=principal)
    before = census(DATA / center, machine)
    assert before['names'] > 1 and before['bytes'] > 0 and before['owned'], before
    with identity_context(Identity(principal, principal)):
        receipt = delete_center(DATA, center, principal=principal)
    after = census(DATA / center, machine)
    assert after == dict(names=0, bytes=0, unlistable=[], owned=[]), after
    assert not (DATA / center).exists(), 'center root survived both passes'
    assert receipt['owner_pass'] and receipt['daemon_pass']['removed'] > 0, receipt
    assert receipt['retired'] and receipt['fence'] == 'released', receipt
    # Zero foreign bytes after the fact: no entry anywhere on the volume still
    # carries the retired owner's uid or gid. Directories uid 1001 cannot list
    # belong to a DIFFERENT owner, and they are named so the claim is checkable.
    volume = census(DATA, machine)
    assert volume['owned'] == [], volume['owned']
    survivors = {name: (lambda info: [info.st_uid, info.st_gid])((DATA / name).lstat())
                 for name in volume['unlistable']}
    assert all(machine not in label for label in survivors.values()), survivors
    return dict(center=center, machine=machine, before=before, after=after,
                volume_owned_by_machine=volume['owned'], unlistable=survivors,
                receipt=json.loads(json.dumps(receipt, default=str)))


# EXTENSION POINT. One cell class is one leg: add ``leg_workspace_remote`` here
# and its name to LEG_NAMES in this file's host half. Nothing else changes.
LEGS = {'bootstrap': leg_bootstrap, 'daemon_reader': leg_daemon_reader,
        'admission': leg_admission, 'tool_files': leg_tool_files,
        'provider_exec': leg_provider_exec, 'two_pass_delete': leg_two_pass_delete}

report, failed = {}, []
for name in WANTED:
    try:
        report[name] = LEGS[name]()
        print(f'ok   {name}', flush=True)
    except BaseException:
        report[name] = {'FAILED': traceback.format_exc()}
        failed.append(name)
        print(f'FAIL {name}\n{traceback.format_exc()}', flush=True)
        break
print('ORACLE REPORT ' + json.dumps(report, indent=1, default=str), flush=True)
print('IMAGE ORACLE CELLS FAIL: ' + ','.join(failed) if failed
      else 'IMAGE ORACLE CELLS PASS', flush=True)
os._exit(1 if failed else 0)
'''


# --------------------------------------------------------------------------- #
# host half
# --------------------------------------------------------------------------- #


def expect(condition, message):
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"ok   {message}", flush=True)


def docker(*args, check=True, text=True):
    return subprocess.run(["docker", *args], capture_output=True, text=text, check=check)


def _posture(name, image, volume, *, user, caps, entrypoint=None, extra=()):
    command = ["docker", "run", "--name", name, "--user", user, "--cap-drop", "ALL"]
    for capability in caps:
        command += ["--cap-add", capability]
    for option in COMPOSE_SECURITY:
        command += ["--security-opt", option]
    for key, value in COMPOSE_ENV.items():
        command += ["-e", f"{key}={value}"]
    command += ["-v", f"{volume}:/data", *extra]
    if entrypoint is not None:
        command += ["--entrypoint", entrypoint]
    return command + [image]


#: Built into the fixture because production carries it: the deploy records the
#: expected instance id in the data root, and the migration must leave it alone.
FIXTURE_EXTRA = """
import json, os
from pathlib import Path
probe = runpy.run_path('/src/scripts/role_migrate_probe.py')


def extra(data):
    # What deploy-prod writes on the box; the serving gate reads it.
    path = Path(data) / 'platform-expected-instance.json'
    path.write_text(json.dumps({{'schema': 'platform_expected_instance', 'version': 1,
                                'expected_instance_id': {instance!r}}}))
    os.chmod(path, 0o600)


probe['build'](Path('/data'), extra=extra)
print(sum(1 for _ in Path('/data').rglob('*')) + 1)
"""


def stage_migrate(args):
    """The runbook's step 4, on a fresh production-shaped layout-2 volume."""
    repo = Path(__file__).resolve().parents[1]
    docker("volume", "rm", "-f", args.volume, check=False)
    docker("volume", "create", args.volume)
    source = str(repo)
    if len(source) > 2 and source[1] == ":":  # C:\... -> /c/... for Docker Desktop
        source = "/" + source[0].lower() + source[2:].replace("\\", "/")
    build = _posture(f"{args.prefix}-fixture", args.image, args.volume, user="0",
                     caps=MIGRATION_CAPS, entrypoint="/opt/venv/bin/python",
                     extra=["--rm", "--network", "none", "-v", f"{source}:/src:ro"])
    made = subprocess.run(build + [
        "-I", "-B", "-c",
        "import runpy\n" + FIXTURE_EXTRA.format(instance=METADATA_INSTANCE_ID),
    ], capture_output=True, text=True)
    expect(made.returncode == 0,
           f"production-shaped fixture: {made.stdout.strip()} names{made.stderr[-2000:]}")

    def migrate(*flags):
        run = _posture(f"{args.prefix}-migrate", args.image, args.volume, user="0",
                       caps=MIGRATION_CAPS, entrypoint="/opt/venv/bin/python",
                       extra=["--rm", "--network", "none"])
        return subprocess.run(run + ["-I", "-B", "/usr/local/libexec/ta-migrate.py", *flags],
                              capture_output=True, text=True)

    before = migrate("--check")
    expect(before.returncode == 1 and "reserve identity: alice" in before.stdout,
           f"--check before the apply: {before.stdout.splitlines()[0] if before.stdout else ''}"
           f"{before.stderr[-2000:]}")
    applied = migrate("--snapshot", args.prefix + "-snapshot", "--snapshot-bytes", "1")
    expect(applied.returncode == 0, f"the apply: {applied.stdout.strip()}{applied.stderr[-2000:]}")
    after = migrate("--check")
    expect(after.returncode == 0 and after.stdout == "",
           f"--check after the apply: 0 diffs{after.stdout[-2000:]}{after.stderr[-2000:]}")
    return {"fixture_names": made.stdout.strip(), "apply": applied.stdout.strip(),
            "check_before_diffs": len(before.stdout.splitlines())}


#: Read every /proc identity in ONE exec, so the runbook's three roles are
#: sampled from the same instant. Container-relative pids: PID1 is the daemon.
_PROC_CENSUS = r"""
import json, os
from pathlib import Path
seen = {}
for name in sorted(os.listdir('/proc'), key=lambda value: int(value) if value.isdigit() else 0):
    if not name.isdigit():
        continue
    try:
        raw = Path('/proc', name, 'status').read_text().splitlines()
        argv = Path('/proc', name, 'cmdline').read_bytes().replace(b'\0', b' ').decode().strip()
    except OSError:
        continue
    fields = dict(line.split(':', 1) for line in raw if ':' in line)
    seen[name] = dict(argv=argv, uid=fields['Uid'].split(), gid=fields['Gid'].split(),
                      nnp=fields['NoNewPrivs'].strip(), ppid=fields['PPid'].strip(),
                      caps={key: fields[key].strip() for key in
                            ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')})
print(json.dumps(seen))
"""


def _network(args):
    if docker("network", "inspect", args.network, check=False).returncode != 0:
        docker("network", "create", "--subnet", METADATA_SUBNET, args.network)
    return args.network


def _metadata(args, *, start):
    """A real link-local metadata service on its own docker network.

    Only this sidecar binds the privileged port; the daemon container's posture
    is untouched. Production's service is DigitalOcean's; this is the same read.
    """
    name = f"{args.prefix}-metadata"
    docker("rm", "-f", name, check=False)
    if not start:
        docker("network", "rm", args.network, check=False)
        return None
    _network(args)
    started = subprocess.run(
        ["docker", "run", "-d", "--name", name, "--network", args.network,
         "--ip", METADATA_ADDRESS, "--user", "0", "--cap-drop", "ALL",
         "--cap-add", "NET_BIND_SERVICE", "--entrypoint", "/opt/venv/bin/python",
         args.image, "-I", "-B", "-c",
         METADATA_SERVER.format(body=METADATA_INSTANCE_ID)],
        capture_output=True, text=True)
    expect(started.returncode == 0, f"link-local metadata service{started.stderr[-2000:]}")
    return name


def stage_serve(args):
    """The image's real CMD in compose's posture; every proof taken from the host."""
    _metadata(args, start=True)
    docker("rm", "-f", f"{args.prefix}-serve", check=False)
    command = _posture(f"{args.prefix}-serve", args.image, args.volume, user=COMPOSE_USER,
                       caps=COMPOSE_CAPS,
                       extra=["-d", "--network", args.network,
                              "-e", "TINYASSETS_IMAGE=" + args.image])
    started = subprocess.run(command, capture_output=True, text=True)
    expect(started.returncode == 0, f"serving container started{started.stderr[-2000:]}")
    deadline = time.monotonic() + args.serve_timeout
    pulse = None
    while time.monotonic() < deadline:
        pulse = docker("exec", f"{args.prefix}-serve", "/usr/local/libexec/ta-op", "pulse",
                       check=False)
        if pulse.returncode == 0:
            break
        if docker("inspect", "-f", "{{.State.Running}}", f"{args.prefix}-serve",
                  check=False).stdout.strip() != "true":
            break
        time.sleep(2)
    logs = docker("logs", f"{args.prefix}-serve", check=False)
    tail = (f"\n--- probe stderr ---\n{pulse.stderr[-800:]}\n--- container ---\n"
            f"{(logs.stdout + logs.stderr)[-4000:]}" if pulse is None or pulse.returncode else "")
    expect(pulse is not None and pulse.returncode == 0,
           "compose healthcheck `ta-op pulse` answers: "
           + (pulse.stdout.strip()[-400:] if pulse else "") + tail)
    # The runbook's step 6.3: enumerate from the host, then read each identity.
    top = docker("top", f"{args.prefix}-serve", "-eo", "pid,uid,gid,args")
    census = json.loads(docker("exec", f"{args.prefix}-serve", "/opt/venv/bin/python",
                               "-I", "-B", "-c", _PROC_CENSUS).stdout)
    roles = {"daemon": census["1"]}
    for pid, record in census.items():
        if "broker_main.py" in record["argv"]:
            roles["broker"] = record
        elif record["uid"][0] == "300000" and record["ppid"] == "1":
            roles["mapper"] = record
        record["pid"] = pid
    expect(set(roles) == {"daemon", "broker", "mapper"},
           f"PID1, the broker and the mapper are all running: {sorted(census)}")
    expect("1001" in top.stdout and "1002" in top.stdout and "300000" in top.stdout,
           "docker top shows uid 1001, 1002 and 300000 from the host:\n" + top.stdout.strip())
    zero = "0000000000000000"
    expect(roles["daemon"]["uid"] == ["1001"] * 4 and roles["daemon"]["gid"] == ["1001"] * 4
           and all(value == zero for value in roles["daemon"]["caps"].values())
           and roles["daemon"]["nnp"] == "1",
           f"PID1 retired to uid 1001 with zero capabilities: {roles['daemon']}")
    expect(roles["broker"]["uid"] == ["1002"] * 4
           and all(value == zero for value in roles["broker"]["caps"].values()),
           f"the broker runs as uid 1002 with zero capabilities: {roles['broker']}")
    expect(roles["mapper"]["uid"] == ["300000"] * 4
           and int(roles["mapper"]["caps"]["CapEff"], 16) == (1 << 6) | (1 << 7)
           and int(roles["mapper"]["caps"]["CapBnd"], 16) == 0,
           f"the mapper holds in-namespace SETUID/SETGID only: {roles['mapper']}")
    canary = docker("exec", f"{args.prefix}-serve", "/usr/local/libexec/ta-op", "canary",
                    check=False)
    expect(canary.returncode == 0,
           f"`ta-op canary` answers the MCP surface: {canary.stdout.strip()[-400:]}"
           f"{canary.stderr[-600:]}")
    docker("rm", "-f", f"{args.prefix}-serve", check=False)
    return {"processes": roles, "docker_top": top.stdout.strip().splitlines(),
            "pulse": pulse.stdout.strip()[-400:], "canary": canary.stdout.strip()[-400:]}


def stage_cells(args):
    """The booted image drives every owner-cell class it can, as the daemon."""
    _metadata(args, start=True)
    docker("rm", "-f", f"{args.prefix}-cells", check=False)
    command = _posture(f"{args.prefix}-cells", args.image, args.volume, user=COMPOSE_USER,
                       caps=COMPOSE_CAPS, entrypoint="/opt/venv/bin/python",
                       extra=["--rm", "-i", "--network", args.network,
                              "-e", "ORACLE_LEGS=" + json.dumps(list(args.legs)),
                              "-e", "ORACLE_OUTSIDE_HOST=" + METADATA_ADDRESS,
                              "-e", "ORACLE_OUTSIDE_PORT=80"])
    result = subprocess.run(command + ["-I", "-B", "-"], input=CELLS, text=True,
                            capture_output=True, timeout=args.cells_timeout)
    output = result.stdout + result.stderr
    print(output, flush=True)
    expect(result.returncode == 0 and "IMAGE ORACLE CELLS PASS" in result.stdout,
           f"owner-cell legs {','.join(args.legs)}")
    body = output.split("ORACLE REPORT ", 1)[1].rsplit("IMAGE ORACLE CELLS", 1)[0]
    return json.loads(body)


def stage_providers(args):
    """Builder C's probe, unchanged: the real CLIs in real provider-exec cells."""
    probe = Path(__file__).resolve().parent / "role_provider_cell_probe.py"
    result = subprocess.run([sys.executable, "-I", str(probe), "--image", args.image],
                            capture_output=True, text=True)
    print(result.stdout[-6000:] + result.stderr[-2000:], flush=True)
    expect(result.returncode == 0 and "PROVIDER CELL PROBE PASS" in result.stdout,
           "role_provider_cell_probe: the shipped Claude and Codex CLIs in provider-exec cells")
    return {"probe": "PROVIDER CELL PROBE PASS"}


STAGES = {"migrate": stage_migrate, "serve": stage_serve, "cells": stage_cells,
          "providers": stage_providers}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--image", required=True, help="the cutover image to prove")
    parser.add_argument("--prefix", default="role-image-oracle",
                        help="name prefix for this run's volume and containers")
    parser.add_argument("--volume", help="data volume name (default <prefix>-data)")
    parser.add_argument("--network", help="docker network name (default <prefix>-net)")
    parser.add_argument("--stages", default=",".join(STAGES),
                        help="comma-separated subset of " + ",".join(STAGES))
    parser.add_argument("--legs", default=",".join(LEG_NAMES),
                        help="comma-separated subset of " + ",".join(LEG_NAMES))
    parser.add_argument("--serve-timeout", type=float, default=300)
    parser.add_argument("--cells-timeout", type=float, default=900)
    parser.add_argument("--keep", action="store_true", help="leave the volume for inspection")
    args = parser.parse_args(argv)
    args.volume = args.volume or f"{args.prefix}-data"
    args.network = args.network or f"{args.prefix}-net"
    args.legs = tuple(name for name in args.legs.split(",") if name)
    unknown = set(args.legs) - set(LEG_NAMES)
    if unknown:
        raise SystemExit(f"unknown legs: {sorted(unknown)}")
    digest = docker("image", "inspect", args.image, "--format", "{{.Id}}").stdout.strip()
    print(f"cutover image: {args.image} {digest}", flush=True)
    report = {"image": args.image, "digest": digest}
    try:
        for name in args.stages.split(","):
            if not name:
                continue
            print(f"\n=== {name} ===", flush=True)
            report[name] = STAGES[name](args)
    finally:
        for suffix in ("serve", "cells", "fixture", "migrate"):
            docker("rm", "-f", f"{args.prefix}-{suffix}", check=False)
        _metadata(args, start=False)
        if not args.keep:
            docker("volume", "rm", "-f", args.volume, check=False)
    print("\nROLE IMAGE ORACLE REPORT " + json.dumps(report, indent=1, default=str), flush=True)
    print("ROLE IMAGE ORACLE PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
