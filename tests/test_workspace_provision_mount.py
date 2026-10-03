"""Typed provisioning binds and real-jail mount/descriptor isolation."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from tinyassets import node_sandbox as sandbox


def directory(fd):
    return os.stat_result((stat.S_IFDIR | 0o700, fd, 1, 1, 1000, 1000, 0, 0, 0, 0))


def argv(mount=None, *, workspace=None, inherited=()):
    return sandbox._bwrap_argv(
        bwrap_path="/usr/bin/bwrap", exists=lambda _: False,
        realpath=lambda path: path, provision_mount=mount,
        workspace_bind=workspace, pass_fds=inherited,
    )


class ProvisionMountTests(unittest.TestCase):
    def test_invalid_shape_fails_before_launch(self):
        for values in ((True, 4, "acquire"), (3, "4", "acquire"),
                       (0, 4, "acquire"), (-1, 4, "acquire"),
                       (3, 3, "acquire"), (3, 4, "online"), (3, 4, None)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                sandbox.ProvisionMount(*values)

    def test_arbitrary_object_cannot_supply_bind_flags(self):
        class Forgery:
            phase = "acquire"

            def bind_argv(self, inherited):
                raise AssertionError("must reject before invoking supplied code")

        with self.assertRaisesRegex(ValueError, "exact typed"):
            argv(Forgery())

    def test_acquisition_has_only_canonical_manifest_and_writable_cache(self):
        mount = sandbox.ProvisionMount(30, 31, "acquire")
        with patch.object(sandbox.os, "fstat", side_effect=directory):
            result = argv(mount, inherited=(30, 31))
        self.assertEqual(result[-9:], [
            "--dir", "/provision", "--ro-bind", "/proc/self/fd/30",
            "/provision/manifests", "--bind", "/proc/self/fd/31",
            "/provision/cache", "--",
        ])
        self.assertNotIn("/workspace", result)
        self.assertIn("--unshare-all", result)
        self.assertIn("--clearenv", result)
        self.assertNotIn("--share-net", result)
        self.assertEqual(result.count("--bind"), 1)

    def test_offline_install_keeps_cache_and_manifests_read_only(self):
        mount = sandbox.ProvisionMount(30, 31, "install")
        with patch.object(sandbox.os, "fstat", side_effect=directory):
            result = argv(mount, workspace="/proc/self/fd/32", inherited=(30, 31, 32))
        self.assertEqual(result.count("--bind"), 1)
        start = result.index("--bind")
        self.assertEqual(result[start:start + 3], ["--bind", "/proc/self/fd/32", "/workspace"])
        self.assertEqual(result[-4:], ["--ro-bind", "/proc/self/fd/31", "/provision/cache", "--"])
        self.assertIn("--unshare-all", result)
        self.assertNotIn("--share-net", result)

    def test_acquisition_cannot_have_checkout(self):
        with self.assertRaisesRegex(ValueError, "excludes checkout"):
            argv(sandbox.ProvisionMount(30, 31, "acquire"),
                 workspace="/proc/self/fd/32", inherited=(30, 31, 32))

    def test_installation_requires_checkout(self):
        with self.assertRaisesRegex(ValueError, "requires checkout"):
            argv(sandbox.ProvisionMount(30, 31, "install"), inherited=(30, 31))

    def test_installation_requires_held_checkout(self):
        with self.assertRaisesRegex(ValueError, "held checkout"):
            sandbox._bwrap_argv(
                provision_mount=sandbox.ProvisionMount(30, 31, "install"),
                workspace_bind="/owned/project", allowed_workspace_roots=("/owned",),
                pass_fds=(30, 31), realpath=lambda path: path,
            )

    def test_installation_rejects_any_additional_descriptor(self):
        with self.assertRaisesRegex(ValueError, "distinct typed descriptors"):
            argv(sandbox.ProvisionMount(30, 31, "install"),
                 workspace="/proc/self/fd/32", inherited=(30, 31, 32, 33))

    def test_checkout_cannot_reuse_provisioning_descriptor(self):
        with self.assertRaisesRegex(ValueError, "distinct typed descriptors"):
            argv(sandbox.ProvisionMount(30, 31, "install"),
                 workspace="/proc/self/fd/30", inherited=(30, 31))

    def test_uninherited_descriptor_fails_before_fstat(self):
        with patch.object(sandbox.os, "fstat") as probe:
            with self.assertRaisesRegex(ValueError, "inheritance"):
                argv(sandbox.ProvisionMount(30, 31, "acquire"), inherited=(30,))
            probe.assert_not_called()

    def test_checkout_directory_is_revalidated_too(self):
        for replacement in (directory(30), os.stat_result((stat.S_IFSOCK,) + (0,) * 9)):
            def inspect(fd):
                return replacement if fd == 32 else directory(fd)
            with self.subTest(mode=replacement.st_mode):
                with patch.object(sandbox.os, "fstat", side_effect=inspect):
                    with self.assertRaises(ValueError):
                        argv(sandbox.ProvisionMount(30, 31, "install"),
                             workspace="/proc/self/fd/32", inherited=(30, 31, 32))

    def test_closed_descriptor_fails_loudly(self):
        with patch.object(sandbox.os, "fstat", side_effect=OSError("closed")):
            with self.assertRaises(OSError):
                argv(sandbox.ProvisionMount(30, 31, "acquire"), inherited=(30, 31))

    def test_non_directory_fails(self):
        for mode in (stat.S_IFREG, stat.S_IFSOCK, stat.S_IFLNK, stat.S_IFIFO):
            info = os.stat_result((mode, 1, 1, 1, 1000, 1000, 0, 0, 0, 0))
            with self.subTest(mode=mode), patch.object(sandbox.os, "fstat", return_value=info):
                with self.assertRaisesRegex(ValueError, "not a directory"):
                    argv(sandbox.ProvisionMount(30, 31, "acquire"), inherited=(30, 31))

    def test_distinct_descriptors_cannot_alias_same_directory(self):
        with patch.object(sandbox.os, "fstat", return_value=directory(10)):
            with self.assertRaisesRegex(ValueError, "alias"):
                argv(sandbox.ProvisionMount(30, 31, "acquire"), inherited=(30, 31))

    def test_unmodified_code_launcher_gets_no_provisioning_mount(self):
        self.assertFalse(any("/provision" in item for item in argv()))
        self.assertFalse(hasattr(sandbox.PlainSubprocessLauncher(), "for_provision"))

    def test_only_acquisition_mounts_the_public_system_ca_bundle(self):
        bundle = "/etc/ssl/certs/ca-certificates.crt"
        cases = ((None, None, ()),
                 (sandbox.ProvisionMount(30, 31, "acquire"), None, (30, 31)),
                 (sandbox.ProvisionMount(30, 31, "install"),
                  "/proc/self/fd/32", (30, 31, 32)))
        for mount, workspace, inherited in cases:
            with self.subTest(phase=mount.phase if mount else "ordinary"):
                with patch.object(sandbox.os, "fstat", side_effect=directory):
                    result = sandbox._bwrap_argv(
                        exists=lambda path: path == bundle, realpath=lambda path: path,
                        provision_mount=mount, workspace_bind=workspace, pass_fds=inherited)
                if mount and mount.phase == "acquire":
                    start = result.index(bundle)
                    self.assertEqual(result[start - 1:start + 2], ["--ro-bind", bundle, bundle])
                else:
                    self.assertNotIn(bundle, result)
                self.assertNotIn("/etc", result)
                self.assertNotIn("/etc/ssl/private", result)
                self.assertNotIn("--share-net", result)

    def test_acquisition_refuses_redirected_system_ca_bundle(self):
        bundle = "/etc/ssl/certs/ca-certificates.crt"
        with patch.object(sandbox.os, "fstat", side_effect=directory):
            with self.assertRaisesRegex(ValueError, "system CA bundle"):
                sandbox._bwrap_argv(
                    exists=lambda path: path == bundle,
                    realpath=lambda path: "/private/key" if path == bundle else path,
                    provision_mount=sandbox.ProvisionMount(30, 31, "acquire"),
                    pass_fds=(30, 31))

    def test_launcher_preserves_owned_fds_and_original_launcher(self):
        root = sandbox.BwrapLauncher(bwrap_path="/usr/bin/bwrap")
        workspace = root.for_workspace(sandbox.WorkspaceMount("/proc/self/fd/32", pass_fds=(32,)))
        install = workspace.for_provision(sandbox.ProvisionMount(30, 31, "install"))
        self.assertEqual(root.pass_fds, ())
        self.assertIsNone(root.provision_mount)
        self.assertEqual(workspace.pass_fds, (32,))
        self.assertEqual(install.pass_fds, (32, 30, 31))
        with patch.object(sandbox.os, "fstat", side_effect=directory):
            result = install.build_argv("print('ok')", [])
        self.assertIn("/provision/cache", result)
        self.assertEqual(result[-2:], ["32,30,31", "print('ok')"])
        self.assertIn("-I", result)
        self.assertIn(sandbox._CLOSE_MOUNT_FDS_SCRIPT, result)

    def test_cannot_reuse_provisioning_launcher_for_another_stage(self):
        acquire = sandbox.BwrapLauncher().for_provision(sandbox.ProvisionMount(30, 31, "acquire"))
        with self.assertRaisesRegex(ValueError, "fresh launcher"):
            acquire.for_provision(sandbox.ProvisionMount(33, 34, "acquire"))
        with self.assertRaisesRegex(ValueError, "rebound"):
            acquire.for_workspace(sandbox.WorkspaceMount("/proc/self/fd/32", pass_fds=(32,)))

    @unittest.skipIf(os.name != "posix", "real directory descriptors require POSIX")
    def test_real_directory_fds_and_dup_alias(self):
        with tempfile.TemporaryDirectory(prefix="ta-provision-mount-") as root:
            paths = [Path(root) / name for name in ("manifests", "cache", "checkout")]
            for path in paths:
                path.mkdir()
            descriptors = []
            try:
                for path in paths:
                    descriptors.append(os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
                manifest, cache, checkout = descriptors
                result = argv(sandbox.ProvisionMount(manifest, cache, "install"),
                              workspace=f"/proc/self/fd/{checkout}", inherited=tuple(descriptors))
                self.assertIn("/provision/cache", result)
                duplicate = os.dup(manifest)
                descriptors.append(duplicate)
                with self.assertRaisesRegex(ValueError, "alias"):
                    argv(sandbox.ProvisionMount(manifest, duplicate, "acquire"),
                         inherited=tuple(descriptors))
            finally:
                for fd in descriptors:
                    os.close(fd)


if __name__ == "__main__":
    unittest.main()


@pytest.mark.real_jail
@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("bwrap"),
                    reason="real provisioning mounts require Linux bubblewrap")
@pytest.mark.parametrize("phase", ["acquire", "install"])
def test_real_jail_mounts_do_not_leak_writable_directory_descriptors(tmp_path, phase):
    """Prove kernel permissions, not merely the presence of --ro-bind in argv."""
    directories = [tmp_path / name for name in ("manifests", "cache", "checkout")]
    for directory_path in directories:
        directory_path.mkdir()
        (directory_path / "marker").write_text(directory_path.name, encoding="utf-8")
    descriptors = [os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                   for path in directories]
    manifest_fd, cache_fd, checkout_fd = descriptors
    launcher = sandbox.BwrapLauncher()
    if phase == "install":
        launcher = launcher.for_workspace(sandbox.WorkspaceMount(
            f"/proc/self/fd/{checkout_fd}", pass_fds=(checkout_fd,)))
    launcher = launcher.for_provision(sandbox.ProvisionMount(manifest_fd, cache_fd, phase))
    script = r'''
import errno, json, os, socket, ssl, sys
from pathlib import Path
phase = sys.argv[1]
ca_bundle = '/etc/ssl/certs/ca-certificates.crt'
expect_ca = phase == 'acquire' and sys.argv[2] == '1'
assert Path(ca_bundle).exists() == expect_ca
assert not Path('/etc/ssl/private').exists()
if expect_ca:
    context = ssl.create_default_context(cafile=ca_bundle)
    assert context.cert_store_stats()['x509_ca'] > 0
    try:
        handle = os.open(ca_bundle, os.O_WRONLY)
    except OSError as error:
        assert error.errno in (errno.EROFS, errno.EACCES)
    else:
        os.close(handle)
        raise AssertionError('public trust bundle is writable')
assert Path('/provision/manifests/marker').read_text() == 'manifests'
assert Path('/provision/cache/marker').read_text() == 'cache'
assert Path('/workspace').exists() == (phase == 'install')
for path in ('/provision/manifests/forbidden', '/provision/cache/new'):
    try:
        Path(path).write_text('test')
    except OSError as error:
        assert error.errno in (errno.EROFS, errno.EACCES), (path, error)
        assert path != '/provision/cache/new' or phase == 'install'
    else:
        assert path == '/provision/cache/new' and phase == 'acquire'
if phase == 'install':
    Path('/workspace/installed').write_text('offline')
# A surviving host dirfd would bypass the read-only bind and expose its parents.
live_fds = []
for number in os.listdir('/proc/self/fd'):
    fd = int(number)
    if fd > 2:
        try:
            os.fstat(fd)
        except OSError:
            pass
        else:
            live_fds.append(fd)
assert not live_fds, live_fds
network = socket.socket()
network.settimeout(0.2)
assert network.connect_ex(('1.1.1.1', 443)) != 0
network.close()
assert 'TA_PROVISION_HOST_SECRET' not in os.environ
print(json.dumps({'phase': phase, 'mounts_verified': True}))
'''
    try:
        result = subprocess.run(
            launcher.build_argv(script, [phase, str(int(
                Path('/etc/ssl/certs/ca-certificates.crt').exists()))]),
            pass_fds=launcher.pass_fds,
            env={**launcher.env(str(tmp_path)), "TA_PROVISION_HOST_SECRET": "not-for-child"},
            capture_output=True, text=True, timeout=15,
        )
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"phase": phase, "mounts_verified": True}
    assert (directories[1] / "new").exists() == (phase == "acquire")
    assert (directories[2] / "installed").exists() == (phase == "install")
    assert not (directories[0] / "forbidden").exists()


@pytest.mark.parametrize("phase,pair", [
    ("acquire", (33, 34)), ("install", (33,)), ("install", [33, 34]),
    ("install", (33, True)), ("install", (30, 34)), ("install", (33, 33)),
])
def test_npm_overlay_shape_refuses_noncanonical_descriptor_sets(phase, pair):
    with pytest.raises(ValueError):
        sandbox.ProvisionMount(30, 31, phase, pair)


@pytest.fixture
def npm_overlay(tmp_path):
    if sys.platform != "linux" or not shutil.which("bwrap"):
        pytest.skip("real npm manifest overlays require Linux bubblewrap")
    paths = [tmp_path / name for name in ("manifests", "cache", "checkout")]
    for path in paths:
        path.mkdir()
    for name in ('package.json', 'package-lock.json'):
        (paths[0] / name).write_text('{"canonical":true}\n')
        (paths[2] / name).write_text('{"original":true}\n')
    fds = [os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW) for path in paths]
    for name in ('package.json', 'package-lock.json'):
        fds.append(os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fds[0]))
    try:
        yield paths, fds
    finally:
        for fd in fds:
            os.close(fd)


@pytest.mark.real_jail
def test_real_npm_overlays_are_readonly_and_leave_originals_unchanged(npm_overlay):
    paths, fds = npm_overlay
    launch = sandbox.BwrapLauncher().for_workspace(sandbox.WorkspaceMount(
        f"/proc/self/fd/{fds[2]}", pass_fds=(fds[2],))).for_provision(
            sandbox.ProvisionMount(fds[0], fds[1], "install", tuple(fds[3:])))
    source = '''
import errno, os
from pathlib import Path
for name in ('package.json', 'package-lock.json'):
    path = Path('/workspace') / name
    assert path.read_text() == '{"canonical":true}\\n'
    try:
        path.write_text('forbidden')
    except OSError as error:
        assert error.errno in (errno.EROFS, errno.EACCES)
    else:
        raise AssertionError('canonical overlay writable')
for name in os.listdir('/proc/self/fd'):
    if int(name) > 2:
        try:
            os.fstat(int(name))
        except OSError:
            continue
        raise AssertionError('host manifest descriptor survived')
print('overlays verified')
'''
    result = subprocess.run(launch.build_argv(source, []), pass_fds=launch.pass_fds,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            env=launch.env('/tmp'), timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'overlays verified'
    for name in ('package.json', 'package-lock.json'):
        assert (paths[0] / name).read_text() == '{"canonical":true}\n'
        assert (paths[2] / name).read_text() == '{"original":true}\n'


@pytest.mark.real_jail
@pytest.mark.parametrize('kind', ['wrong-file', 'directory', 'symlink-target', 'unadmitted'])
def test_npm_overlay_refuses_wrong_files_or_targets(npm_overlay, kind):
    paths, fds = npm_overlay
    pair = tuple(fds[3:])
    inherited = tuple(fds)
    if kind == 'wrong-file':
        pair = tuple(reversed(pair))
    if kind == 'directory':
        # A duplicate fd number would be refused even before the regular-file
        # check; use a genuinely distinct descriptor naming a directory.
        extra = os.dup(fds[0])
        fds.append(extra)
        pair = (extra, pair[1])
        inherited = tuple(fds)
    if kind == 'symlink-target':
        (paths[2] / 'package.json').unlink()
        (paths[2] / 'package.json').symlink_to(paths[0] / 'package.json')
    if kind == 'unadmitted':
        inherited = tuple(fds[:3])
    mount = sandbox.ProvisionMount(fds[0], fds[1], 'install', pair)
    with pytest.raises(ValueError):
        mount.bind_argv(inherited, checkout_fd=fds[2])
