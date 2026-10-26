"""Build platform CLI packages into the immutable box rootfs, using runtime verification."""
import hashlib
import json
import subprocess
from pathlib import Path

from tinyassets.providers.source_record import records
from tinyassets.providers.subscription_runtime import SubscriptionError, SubscriptionRuntime

LOCKS = Path(__file__).with_name("source_locks")


class ImageInstaller(SubscriptionRuntime):
    def __init__(self, source, root):
        import threading

        # SubscriptionRuntime's per-run lease state; never set here.
        self._local = threading.local()
        self.source = source
        self.cli_login = source.sign_in["cli_login"]
        self.home = root
        self.directory = root / source.id
        self.directory.mkdir(parents=True)
        install = self.cli_login["install"]
        self.binary = self.directory / ("venv/bin" if install["manager"] == "pip"
                                        else "node_modules/.bin") / install["bin"]

    def run(self, argv, *, phase, timeout=120, **kwargs):
        # The image build never inherits host credentials, npmrc, proxies or loader hooks.
        global_config = self.directory / ".npm-globalrc"
        global_config.write_text("")
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.directory),
               "LANG": "C.UTF-8", "NPM_CONFIG_USERCONFIG": "/dev/null",
               "NPM_CONFIG_GLOBALCONFIG": str(global_config),
               "NPM_CONFIG_CACHE": str(self.directory / ".npm-cache")}
        return subprocess.run(argv, cwd=self.directory, capture_output=True, timeout=timeout,
                              env=env)

    def build(self):
        if self.cli_login["install"]["manager"] == "pip":
            self.install_pip()
        else:
            install = self.cli_login["install"]
            committed = LOCKS / self.source.id
            raw = (committed / "package-lock.json").read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != install.get("lock_sha256"):
                raise SubscriptionError("committed CLI lock digest mismatch")
            manifest = json.loads((committed / "package.json").read_bytes())
            if manifest.get("dependencies") != {install["package"]: install["version"]}:
                raise SubscriptionError("committed CLI package does not match source")
            lock = self.directory / "package-lock.json"
            lock.write_bytes(raw)
            self.verify_lock(lock)
            (self.directory / "package.json").write_text(json.dumps(manifest))
            command = ["npm", "ci", "--registry",
                       "https://" + self.source.egress["registry"][0],
                       "--no-audit", "--no-fund"]
            if not install.get("lifecycle_scripts"):
                command.append("--ignore-scripts")
            result = self.run(command, phase="registry", timeout=300)
            if result.returncode or not self.binary.is_file():
                raise SubscriptionError("CLI installation failed")
            if lock.read_bytes() != raw:
                raise SubscriptionError("committed CLI lock changed during installation")
            if install.get("verify_argv"):
                result = self.run(self.argv(install["verify_argv"]), phase="registry")
                if result.returncode or install["version"] not in result.stdout.decode():
                    raise SubscriptionError("installed CLI version verification failed")
            (self.directory / "installed.json").write_text(json.dumps({
                "source": self.install_digest(), "lock": digest}))


def build(root=SubscriptionRuntime.IMAGE_ROOT):
    for source in records(strict=True).values():
        if "cli_login" in source.sign_in:
            ImageInstaller(source, root).build()


if __name__ == "__main__":
    build()
