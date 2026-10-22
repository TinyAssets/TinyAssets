"""Real npm + native login inside separate Linux owner mounts; no provider account."""

import base64
import hashlib
import io
import json
import select
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.real_jail, pytest.mark.role_split]

CLI = r"""#!/usr/bin/env node
const fs = require('fs'), path = require('path');
const state = path.join(process.env.HOME, '.fake-login');
const action = process.argv[2];
if (action === 'status') process.exit(fs.existsSync(state) ? 0 : 1);
if (action === 'login') {
  process.stdout.write('Open https://login.example.test/approve?state=fixture\nCode: ABCD-1234\n');
  process.stdin.once('data', data => {
    if (data.toString().trim() !== 'approved') process.exit(2);
    fs.writeFileSync(state, 'private-login');
    process.stdout.write('Login successful\n');
    if (process.env.FAKE_INTERACTIVE === '1') setInterval(() => {}, 1000);
    else process.exit(0);
  });
} else if (action === 'engine') {
  let input = '';
  process.stdin.on('data', data => { input += data; });
  process.stdin.on('end', () => {
    const request = JSON.parse(input);
    const message = {role:'assistant', content:null, tool_calls:[{
      id:'fixture-call', type:'function', function:{name:request.tools[0].function.name,
      arguments:JSON.stringify({text:request.messages.at(-1).content})}}]};
    console.error('fixture progress on stderr');
    console.log(JSON.stringify({result:JSON.stringify(message),is_error:false}));
  });
} else if (action === 'invoke') {
  if (!fs.existsSync(state)) process.exit(3);
  console.log(JSON.stringify({result: process.argv[3], is_error: false}));
} else process.exit(4);
"""

SERVER = r"""
import json, os, sys
from pathlib import Path
from tinyassets import owner_runtime
from tinyassets.boxes.boxd import OwnerBoxd
from tinyassets.providers.subscription_runtime import SubscriptionRuntime, login_operation
root = Path('/home/owner')
core = OwnerBoxd(owner_id='owner', owner_root=root, state_dir=root / '.owner-boxd')
owner_runtime._runtime = owner_runtime.OwnerRuntime('owner', os.getuid(), root, os.getpid(), core)
try:
    for line in sys.stdin:
        try:
            doc = json.loads(line)
            runtime = SubscriptionRuntime('fake-cli')
            action = doc.pop('action')
            if action == 'invoke': result = {'text': runtime.invoke(**doc)}
            elif action == 'status': result = {'authenticated': runtime.authenticated()}
            elif action == 'install': runtime.install(); result = {'installed': True}
            elif action == 'visible': result = {'visible': Path(doc['path']).exists()}
            else: result = login_operation(action, **doc)
            print(json.dumps(result), flush=True)
        except Exception as error:
            print(json.dumps({'error': type(error).__name__, 'message': str(error)}), flush=True)
finally:
    core.close()
"""


def fixture_package(root):
    root.mkdir(exist_ok=True)
    archive = root / "fixture.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, content in {
            "package.json": json.dumps(
                {"name": "fake-cli", "version": "1.0.0", "bin": {"fake": "cli.js"}}
            ),
            "cli.js": CLI,
        }.items():
            data = content.encode()
            info = tarfile.TarInfo("package/" + name)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    definition = {
        "id": "fake-cli",
        "display_name": "Fixture",
        "install": {
            "manager": "npm",
            "package": "fake-cli",
            "version": "1.0.0",
            "bin": "fake",
            "archive": "fixture.tgz",
            "integrity": "sha512-"
            + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode(),
        },
        "login": {
            "argv": ["{bin}", "login"],
            "status_argv": ["{bin}", "status"],
            "url_pattern": r"(https://login\.example\.test/\S+)",
            "code_pattern": r"Code: ([A-Z0-9-]+)",
            "accepts_code": True,
        },
        "invoke": {
            "argv": ["{bin}", "invoke", "{prompt}"],
            "format": "json",
            "text_path": "result",
            "errors": {"is_error": True},
        },
        "egress": {
            "registry": ["registry.npmjs.org"],
            "login": ["login.example.test"],
            "provider": ["model.example.test"],
        },
        "state_paths": [".fake-login"],
    }
    definition.update(origin="declared", addresses=["https://fixture.example"],
        sign_in={"cli_login": {"install": definition.pop("install"),
                               "login": definition.pop("login")}},
        invoke={"cli": definition["invoke"]},
        state={"location": "owner_box", "paths": definition.pop("state_paths")})
    directory = root / ".ta-cli" / "sources"
    directory.mkdir(parents=True)
    (directory / "fake-cli.json").write_text(json.dumps(definition))
    return definition


class Box:
    def __init__(self, root):
        assert shutil.which("bwrap"), "linux_oracle must supply bubblewrap"
        argv = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all", "--clearenv"]
        for path in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(path).exists():
                argv += ["--ro-bind", path, path]
        argv += [
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/home",
            "--bind",
            str(root),
            "/home/owner",
            "--ro-bind",
            str(Path.cwd()),
            "/app",
            "--chdir",
            "/home/owner",
            "--setenv",
            "PYTHONPATH",
            "/app",
            "--setenv",
            "PATH",
            "/usr/local/bin:/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/home/owner",
            "--",
            sys.executable,
            "-u",
            "-c",
            SERVER,
        ]
        self.process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

    def call(self, action, **fields):
        self.process.stdin.write(json.dumps({"action": action, **fields}) + "\n")
        self.process.stdin.flush()
        assert select.select([self.process.stdout], [], [], 40)[0], "owner reply timed out"
        line = self.process.stdout.readline()
        assert line, self.process.stderr.read()
        return json.loads(line)

    def close(self):
        self.process.stdin.close()
        self.process.wait(10)
        error = self.process.stderr.read()
        self.process.stdout.close()
        self.process.stderr.close()
        assert self.process.returncode == 0, error


@pytest.mark.verifies("box-subscription-sources:Native login and durable isolation")
@pytest.mark.parametrize("terminal", [False, True])
def test_install_native_login_call_restart_and_other_owner(tmp_path, terminal):
    alice, bob = tmp_path / "alice", tmp_path / "bob"
    for root in (alice, bob):
        doc = fixture_package(root)
        if terminal:
            doc["sign_in"]["cli_login"]["login"].update(terminal=True, complete_on_state=True)
            doc["sign_in"]["cli_login"]["env"] = {"FAKE_INTERACTIVE": "1"}
            (root / ".ta-cli/sources/fake-cli.json").write_text(json.dumps(doc))
    first, second = Box(alice), Box(bob)
    try:
        result = first.call("begin", source_id="fake-cli")
        flow = result["flow"]
        deadline = time.monotonic() + 40
        while not result["authorize_url"] and time.monotonic() < deadline:
            assert result["status"] != "failed", result
            time.sleep(0.05)
            result = first.call("poll", flow=flow)
        assert result["authorize_url"] == "https://login.example.test/approve?state=fixture"
        assert result["code"] == "ABCD-1234"
        assert first.call("status") == {"authenticated": False}
        assert second.call("poll", flow=flow)["error"] == "PermissionError"
        first.call("code", flow=flow, code="approved")
        while result["status"] != "connected" and time.monotonic() < deadline:
            time.sleep(0.05)
            result = first.call("poll", flow=flow)
        assert result["status"] == "connected", result
        assert first.call("invoke", prompt="hello owner") == {"text": "hello owner"}
        lockfile = alice / ".ta-cli/fake-cli/package-lock.json"
        previous = lockfile.stat().st_mtime_ns
        assert first.call("install") == {"installed": True}
        assert lockfile.stat().st_mtime_ns == previous
        assert second.call("install") == {"installed": True}
        assert second.call("status") == {"authenticated": False}
        assert second.call("invoke", prompt="hello")["error"] == "PermissionError"
        assert second.call("visible", path=str(alice / ".fake-login")) == {"visible": False}
    finally:
        first.close()
        second.close()
    awake = Box(alice)
    try:
        assert awake.call("status") == {"authenticated": True}
        assert awake.call("invoke", prompt="after wake") == {"text": "after wake"}
    finally:
        awake.close()


@pytest.mark.verifies("box-subscription-sources:Owner-box subscription runtime")
def test_unadmitted_process_cannot_construct_runtime():
    from tinyassets.providers.subscription_runtime import SubscriptionRuntime

    with pytest.raises(PermissionError):
        SubscriptionRuntime("fake-cli")

@pytest.mark.verifies("box-subscription-sources:Owner-box subscription runtime")
def test_registry_forwarder_refuses_other_hosts_and_ports(tmp_path):
    import socket

    from tinyassets.owner_public_egress import OwnerPublicForwarder

    forwarder = OwnerPublicForwarder(tmp_path / 'absent-relay.sock',
        listen=('127.0.0.1', 0), allowed_hosts=['registry.npmjs.org'])
    try:
        address = forwarder.address
        for target in ('provider.example:443', 'registry.npmjs.org.evil:443',
                       'registry.npmjs.org:80'):
            with socket.create_connection(address, 2) as connection:  # hermetic-ok: loopback
                connection.sendall(('CONNECT ' + target + ' HTTP/1.1\r\n\r\n').encode())
                assert connection.recv(1024).startswith(b'HTTP/1.1 403 Forbidden')
    finally:
        forwarder.close()


def test_image_package_is_verified_and_reused_without_owner_install(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tinyassets import owner_runtime
    from tinyassets.providers.source_record import SourceRecord
    from tinyassets.providers.subscription_image import ImageInstaller
    from tinyassets.providers.subscription_runtime import SubscriptionError, SubscriptionRuntime

    doc = fixture_package(tmp_path)
    image = ImageInstaller(SourceRecord.parse(doc), tmp_path)
    image.build()
    owner = tmp_path / "owner"
    owner.mkdir()
    directory = owner / ".ta-cli/sources"
    directory.mkdir(parents=True)
    (directory / "fake-cli.json").write_text(json.dumps(doc))
    monkeypatch.setattr(SubscriptionRuntime, "IMAGE_ROOT", tmp_path)
    monkeypatch.setattr(owner_runtime, "current", lambda: SimpleNamespace(root=owner))
    runtime = SubscriptionRuntime("fake-cli")
    assert runtime.binary == image.binary
    monkeypatch.setattr(runtime, "_install_npm", lambda *a: pytest.fail("owner install"))
    runtime.install()
    assert not (runtime.directory / "node_modules").exists()
    lock = image.directory / "package-lock.json"
    lock.write_text(lock.read_text() + " ")
    with pytest.raises(SubscriptionError, match="image CLI lock mismatch"):
        SubscriptionRuntime("fake-cli")
