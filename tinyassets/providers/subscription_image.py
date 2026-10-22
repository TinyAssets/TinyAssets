"""Build platform CLI packages into the immutable box rootfs, using runtime verification."""
import subprocess

from tinyassets.providers.source_record import records
from tinyassets.providers.subscription_runtime import SubscriptionRuntime


class ImageInstaller(SubscriptionRuntime):
    def __init__(self, source, root):
        self.source = source
        self.cli_login = source.sign_in["cli_login"]
        self.home = root
        self.directory = root / source.id
        self.directory.mkdir(parents=True)
        install = self.cli_login["install"]
        self.binary = self.directory / ("venv/bin" if install["manager"] == "pip"
                                        else "node_modules/.bin") / install["bin"]

    def run(self, argv, *, phase, timeout=120, **kwargs):
        return subprocess.run(argv, cwd=self.directory, capture_output=True, timeout=timeout)

    def build(self):
        if self.cli_login["install"]["manager"] == "pip":
            self.install_pip()
        else:
            self._install_npm(self.cli_login["install"], self.directory / "package-lock.json",
                              self.directory / "installed.json")


def build(root=SubscriptionRuntime.IMAGE_ROOT):
    for source in records().values():
        if "cli_login" in source.sign_in:
            ImageInstaller(source, root).build()


if __name__ == "__main__":
    build()
