"""One native CLI runtime, executed exclusively by the admitted owner's box."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import queue
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from tinyassets.providers.subscription_source import digest, select, sources


class SubscriptionError(RuntimeError):
    pass


class SubscriptionRuntime:
    def __init__(self, source_id):
        from tinyassets.owner_runtime import current

        self.owner = current()
        if self.owner is None:
            raise PermissionError("subscription CLI requires an admitted owner box")
        self.home = self.owner.root
        self.source = sources(self.home)[source_id]
        self.directory = self.home / ".ta-cli" / source_id
        self.binary = self.directory / "node_modules" / ".bin" / self.source["install"]["bin"]

    @contextlib.contextmanager
    def lock(self):
        import fcntl

        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.directory / ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    @contextlib.contextmanager
    def environment(self, phase):
        from tinyassets.owner_public_egress import OwnerPublicForwarder

        # The fixture path can install an integrity-pinned local archive offline.
        # Networked operation always requires the startup-mounted owner relay.
        if self.owner.relay is None:
            if "archive" not in self.source["install"]:
                raise SubscriptionError("owner egress relay unavailable")
            yield {"HOME": str(self.home)}
            return
        proxy = OwnerPublicForwarder(
            self.owner.relay, listen=("127.0.0.1", 0), allowed_hosts=self.source["egress"][phase]
        )
        url = f"http://127.0.0.1:{proxy.address[1]}"
        env = {
            key: url
            for key in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
            )
        }
        env.update(
            HOME=str(self.home),
            NO_PROXY="",
            no_proxy="",
            npm_config_proxy=url,
            npm_config_https_proxy=url,
            npm_config_cache=str(self.home / ".ta-cli" / ".npm-cache"),
        )
        try:
            yield env
        finally:
            proxy.close()

    def run(self, argv, *, phase, timeout=120, **kwargs):
        with self.environment(phase) as env:
            env.update(self.source.get("env", {}))
            # Definition environment cannot move HOME or bypass the proxy.
            env.update({"HOME": str(self.home)})
            return self.owner.tools.run(
                argv,
                cwd=self.home,
                env=env,
                wall_seconds=timeout,
                output_bytes=4 * 1024 * 1024,
                **kwargs,
            )

    def argv(self, template, **values):
        values = {"bin": str(self.binary), "home": str(self.home), **values}
        # Replace only known placeholders; literal JSON argv is common. Values
        # are substituted once, so braces in a prompt cannot become directives.
        pattern = re.compile(r"\{(" + "|".join(values) + r")\}")
        return [pattern.sub(lambda match: str(values[match.group(1)]), arg) for arg in template]

    def install(self):
        with self.lock():
            install = self.source["install"]
            lockfile = self.directory / "package-lock.json"
            marker = self.directory / "installed.json"
            if marker.exists() and lockfile.exists() and self.binary.is_file():
                record = json.loads(marker.read_text())
                if record == {
                    "source": digest(self.source),
                    "lock": hashlib.sha256(lockfile.read_bytes()).hexdigest(),
                }:
                    self.verify_lock(lockfile)
                    return
            package = install["package"]
            spec = install["version"]
            if "archive" in install:
                archive = (self.home / install["archive"]).resolve()
                archive.relative_to(self.home.resolve())
                actual = (
                    "sha512-"
                    + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
                )
                if actual != install["integrity"]:
                    raise SubscriptionError("local package integrity mismatch")
                spec = "file:" + str(archive)
            (self.directory / "package.json").write_text(
                json.dumps(
                    {
                        "name": "owner-subscription-source",
                        "version": "1.0.0",
                        "private": True,
                        "dependencies": {package: spec},
                    }
                )
            )
            npm = [
                "npm",
                "--prefix",
                str(self.directory),
                "--registry",
                "https://" + self.source["egress"]["registry"][0],
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ]
            result = self.run(
                [*npm, "install", "--package-lock-only"], phase="registry", timeout=300
            )
            if result.returncode:
                raise SubscriptionError("CLI package lock failed")
            self.verify_lock(lockfile)
            result = self.run([*npm, "ci"], phase="registry", timeout=300)
            if result.returncode or not self.binary.is_file():
                raise SubscriptionError("CLI installation failed")
            marker.write_text(
                json.dumps(
                    {
                        "source": digest(self.source),
                        "lock": hashlib.sha256(lockfile.read_bytes()).hexdigest(),
                    }
                )
            )

    def verify_lock(self, path):
        packages = json.loads(path.read_text())["packages"]
        install = self.source["install"]
        root = packages["node_modules/" + install["package"]]
        if (
            root.get("version") != install["version"]
            or root.get("integrity") != install["integrity"]
        ):
            raise SubscriptionError("CLI lock does not match the pinned package")
        for name, package in packages.items():
            if not name:
                continue
            url = urlsplit(package.get("resolved", ""))
            local = "archive" in install and name == "node_modules/" + install["package"]
            if not package.get("integrity") or (
                not local
                and (
                    url.scheme != "https"
                    or url.hostname not in self.source["egress"]["registry"]
                    or url.port not in (None, 443)
                    or url.username
                    or url.password
                )
            ):
                raise SubscriptionError("CLI lock contains an unverified package or registry")

    def authenticated(self):
        if not self.binary.is_file():
            return False
        result = self.run(self.argv(self.source["login"]["status_argv"]), phase="login", timeout=30)
        if result.returncode:
            return False
        expected = self.source["login"].get("status_equals", {})
        if expected:
            try:
                value = json.loads(result.stdout)
            except ValueError:
                return False
            if any(select(value, path) != item for path, item in expected.items()):
                return False
        return all((self.home / path).exists() for path in self.source["state_paths"])

    def invoke(self, prompt, system="", model=""):
        if not self.authenticated():
            raise SubscriptionError("subscription sign-in required")
        invoke = self.source["invoke"]
        argv = self.argv(invoke["argv"], prompt=prompt, system=system, model=model)
        result = self.run(argv, phase="provider", timeout=invoke.get("timeout", 600))
        if result.returncode:
            raise SubscriptionError("subscription invocation failed")
        output = result.stdout.decode("utf-8")
        if invoke["format"] == "text":
            text = output.strip()
        else:
            values = (
                [json.loads(output)]
                if invoke["format"] == "json"
                else [json.loads(line) for line in output.splitlines() if line.strip()]
            )
            matching = [
                value
                for value in values
                if all(
                    select(value, key) == expected
                    for key, expected in invoke.get("match", {}).items()
                )
            ]
            if not matching:
                raise SubscriptionError("CLI returned no terminal result")
            value = matching[-1]
            if any(
                select(value, key) == expected for key, expected in invoke.get("errors", {}).items()
            ):
                raise SubscriptionError("CLI reported an unsuccessful result")
            text = select(value, invoke["text_path"])
        if not isinstance(text, str) or not text.strip():
            raise SubscriptionError("CLI returned no answer")
        return text


class Login:
    def __init__(self, runtime):
        self.runtime = runtime
        self.handle = secrets.token_urlsafe(32)
        self.status = "installing"
        self.url = self.code = ""
        self.inputs = queue.Queue(maxsize=1)
        self.cancelled = threading.Event()
        self.expires = time.monotonic() + 900
        self._output = ""
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def view(self):
        with self._lock:
            return {
                "flow": self.handle,
                "source_id": self.runtime.source["id"],
                "status": self.status,
                "authorize_url": self.url,
                "code": self.code,
                "accepts_code": self.runtime.source["login"].get("accepts_code", False),
            }

    def input(self):
        try:
            return self.inputs.get_nowait()
        except queue.Empty:
            return b""

    def submit(self, value):
        if (
            not self.runtime.source["login"].get("accepts_code")
            or self.status != "waiting"
            or not isinstance(value, str)
            or not 0 < len(value) <= 2048
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("invalid login code handoff")
        self.inputs.put_nowait(value.encode() + b"\n")

    def observe(self, data):
        with self._lock:
            self._output = (self._output + data.decode("utf-8", errors="replace"))[-32768:]
            login = self.runtime.source["login"]
            for key, attribute in (("url_pattern", "url"), ("code_pattern", "code")):
                match = re.search(login[key], self._output) if login.get(key) else None
                if not match:
                    continue
                value = match.group(1)
                if attribute == "url":
                    url = urlsplit(value)
                    if (
                        url.scheme != "https"
                        or url.hostname not in self.runtime.source["egress"]["login"]
                        or url.username
                        or url.password
                        or url.port not in (None, 443)
                    ):
                        continue
                setattr(self, attribute, value)

    def _run(self):
        from tinyassets.owner_activity import activity

        try:
            with activity.enter():
                self.runtime.install()
                with self.runtime.lock():
                    if not self.runtime.authenticated():
                        self.status = "waiting"
                        result = self.runtime.run(
                            self.runtime.argv(self.runtime.source["login"]["argv"]),
                            phase="login",
                            timeout=600,
                            input_source=self.input,
                            output_handler=self.observe,
                            should_cancel=self.cancelled.is_set,
                        )
                        if result.returncode or not self.runtime.authenticated():
                            raise SubscriptionError("native login did not complete")
                    self.status = "connected"
        except Exception:
            # CLI output can contain credentials; only safe state crosses the page boundary.
            self.status = "cancelled" if self.cancelled.is_set() else "failed"
        finally:
            self._output = ""


_logins = {}
_logins_lock = threading.Lock()


def login_operation(action, *, source_id=None, flow=None, code=None):
    from tinyassets.owner_runtime import current

    owner = current()
    if owner is None:
        raise PermissionError("login requires an admitted owner box")
    with _logins_lock:
        for key, login in list(_logins.items()):
            if time.monotonic() >= login.expires:
                login.cancelled.set()
                del _logins[key]
        if action == "begin":
            for login in _logins.values():
                if (
                    login.runtime.owner is owner
                    and login.runtime.source["id"] == source_id
                    and login.status in {"installing", "waiting"}
                ):
                    return login.view()
            if len(_logins) >= 16:
                raise SubscriptionError("too many pending logins")
            login = Login(SubscriptionRuntime(source_id)).start()
            _logins[login.handle] = login
        else:
            login = _logins.get(flow)
            if login is None or login.runtime.owner is not owner:
                raise PermissionError("login flow is unavailable")
            if action == "cancel":
                login.cancelled.set()
            elif action == "code":
                login.submit(code)
            elif action != "poll":
                raise ValueError("unknown login operation")
        return login.view()
