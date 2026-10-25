"""One native CLI runtime, executed exclusively by the admitted owner's box."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import queue
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from tinyassets.boxes.provider import BoxError
from tinyassets.owner_process import OwnerProcessCancelled
from tinyassets.providers.subscription_source import digest, select, sources

_LOG = logging.getLogger(__name__)


class SubscriptionError(RuntimeError):
    pass


class SubscriptionRuntime:
    IMAGE_ROOT = Path("/opt/tinyassets/cli")

    def __init__(self, source_id):
        from tinyassets.owner_runtime import current

        self.owner = current()
        if self.owner is None:
            raise PermissionError("subscription CLI requires an admitted owner box")
        self.home = self.owner.root
        self._local = threading.local()
        self.source = sources(self.home)[source_id]
        self.cli_login = self.source.sign_in["cli_login"]
        self.directory = self.home / ".ta-cli" / source_id
        self.binary = self.directory / "node_modules" / ".bin" / self.cli_login["install"]["bin"]
        if self.cli_login["install"]["manager"] == "pip":
            self.binary = self.directory / "venv" / "bin" / self.cli_login["install"]["bin"]
        self.image_install = self._image_binary()
        if self.image_install:
            self.binary = self.image_install

    def _image_binary(self):
        directory = self.IMAGE_ROOT / self.source.id
        marker = directory / "installed.json"
        if not marker.is_file():
            return None
        record = json.loads(marker.read_text())
        if record["source"] != self.install_digest():
            return None
        lockfile = directory / ("requirements.lock" if self.cli_login["install"]["manager"]
                                == "pip" else "package-lock.json")
        if record["lock"] != hashlib.sha256(lockfile.read_bytes()).hexdigest():
            raise SubscriptionError("image CLI lock mismatch")
        if self.cli_login["install"]["manager"] == "npm":
            self.verify_lock(lockfile)
        binary = directory / self.binary.relative_to(self.directory)
        if not binary.is_file():
            raise SubscriptionError("image CLI binary missing")
        return binary

    @contextlib.contextmanager
    def lock(self):
        from tinyassets.providers.subscription_lease import source_lock

        with source_lock(self):
            yield

    def lease(self, *, login=False, cancelled=None):
        from tinyassets.providers.subscription_lease import lease

        return lease(self, login=login, cancelled=cancelled)

    @property
    def state_home(self):
        return getattr(self._local, "home", None) or self.home

    @contextlib.contextmanager
    def environment(self, phase):
        from tinyassets.owner_public_egress import OwnerPublicForwarder

        # The fixture path can install an integrity-pinned local archive offline.
        # Networked operation always requires the startup-mounted owner relay.
        if self.owner.relay is None:
            if not {"archive", "wheelhouse"}.intersection(self.cli_login["install"]):
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
        if phase != "registry" and self.state_home == self.home:
            raise PermissionError("native execution requires a credential lease")
        with self.environment(phase) as env:
            env.update({key: self.argv([value])[0]
                        for key, value in self.cli_login.get("env", {}).items()})
            # Definition environment cannot move HOME or bypass the proxy.
            env.update({"HOME": str(self.state_home), "TMPDIR": str(self.state_home)})
            return self.owner.tools.run(
                argv,
                cwd=self.home,
                env=env,
                wall_seconds=timeout,
                output_bytes=4 * 1024 * 1024,
                **kwargs,
            )

    def argv(self, template, **values):
        values = {"bin": str(self.binary), "home": str(self.state_home),
                  "config": str(self.state_home / ".ta-cli" / self.source.id / "config"), **values}
        # Replace only known placeholders; literal JSON argv is common. Values
        # are substituted once, so braces in a prompt cannot become directives.
        pattern = re.compile(r"\{(" + "|".join(values) + r")\}")
        return [pattern.sub(lambda match: str(values[match.group(1)]), arg) for arg in template]

    def install_digest(self):
        return digest({"install": self.cli_login["install"],
                       "registry": self.source["egress"]["registry"]})

    def install(self):
        with self.lock():
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            install = self.cli_login["install"]
            for name, content in self.cli_login.get("files", {}).items():
                target = self.directory / "config" / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                target.write_text(self.argv([content])[0])
                target.chmod(0o600)
            if self.image_install:
                return
            if install["manager"] == "pip":
                self.install_pip()
                return
            lockfile = self.directory / "package-lock.json"
            marker = self.directory / "installed.json"
            if marker.exists() and lockfile.exists() and self.binary.is_file():
                record = json.loads(marker.read_text())
                if record == {
                    "source": self.install_digest(),
                    "lock": hashlib.sha256(lockfile.read_bytes()).hexdigest(),
                }:
                    self.verify_lock(lockfile)
                    return
            try:
                self._require_space()
                self._install_npm(install, lockfile, marker)
            except BaseException:
                self._discard_partial()
                raise
            finally:
                self._clear_download_cache()

    INSTALL_FREE_BYTES = 1024**3

    def _require_space(self):
        import shutil

        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if shutil.disk_usage(self.directory).free < self.INSTALL_FREE_BYTES:
            raise SubscriptionError("not enough free storage to install this source")

    def _discard_partial(self):
        import shutil

        for name in ("node_modules", "package-lock.json", "installed.json"):
            target = self.directory / name
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)

    def _clear_download_cache(self):
        import shutil

        shutil.rmtree(self.home / ".ta-cli" / ".npm-cache", ignore_errors=True)

    def _install_npm(self, install, lockfile, marker):
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
        scripts = ["--ignore-scripts=false"] if install.get("lifecycle_scripts") else []
        result = self.run([*npm, "ci", *scripts], phase="registry", timeout=300)
        if result.returncode or not self.binary.is_file():
            raise SubscriptionError("CLI installation failed")
        if install.get("verify_argv"):
            result = self.run(self.argv(install["verify_argv"]), phase="registry",
                              separate_stderr=True)
            if result.returncode or install["version"] not in result.stdout.decode():
                raise SubscriptionError("installed CLI version verification failed")
        marker.write_text(
            json.dumps(
                {
                    "source": self.install_digest(),
                    "lock": hashlib.sha256(lockfile.read_bytes()).hexdigest(),
                }
            )
        )

    def install_pip(self):
        """Wheels only: every transitive dependency must be version/hash locked."""
        install = self.cli_login["install"]
        lockfile = self.directory / "requirements.lock"
        locked = "\n".join(
            item["package"] + "==" + item["version"] + " "
            + " ".join("--hash=" + h for h in item["hashes"])
            for item in install["requirements"]
        ) + "\n"
        record = {"source": self.install_digest(),
                  "lock": hashlib.sha256(locked.encode()).hexdigest()}
        marker = self.directory / "installed.json"
        if (self.binary.is_file() and marker.exists() and lockfile.exists()
                and marker.read_text() == json.dumps(record) and lockfile.read_text() == locked):
            return
        result = self.run(["python3", "-m", "venv", str(self.directory / "venv")],
                          phase="registry")
        if result.returncode:
            raise SubscriptionError("owner CLI virtual environment creation failed")
        lockfile.write_text(locked)
        location = ["--index-url", "https://" + self.source["egress"]["registry"][0] + "/simple"]
        if "wheelhouse" in install:
            directory = (self.home / install["wheelhouse"]).resolve()
            directory.relative_to(self.home.resolve())
            location = ["--no-index", "--find-links", str(directory)]
        result = self.run([
            str(self.directory / "venv/bin/python"), "-m", "pip", "--isolated", "install",
            "--disable-pip-version-check", "--require-hashes", "--only-binary=:all:",
            "--force-reinstall",
            *location,
            "-r", str(lockfile),
        ], phase="registry", timeout=300)
        if result.returncode or not self.binary.is_file():
            raise SubscriptionError("hash-verified owner CLI installation failed")
        marker.write_text(json.dumps(record))

    def verify_lock(self, path):
        packages = json.loads(path.read_text())["packages"]
        install = self.cli_login["install"]
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
        from tinyassets.providers.subscription_lease import capture, custody

        if self.state_home != self.home:
            return self._authenticated()
        if not self.binary.is_file():
            return False
        if not custody(self, "read")["state"] and not capture(self, self.home):
            return False
        with self.lease():
            return self._authenticated()

    def _authenticated(self):
        if not self.binary.is_file():
            return False
        if not all((self.state_home / path).is_file() for path in self.source["state"]["paths"]):
            return False
        for check in self.cli_login["login"].get("state_checks", []):
            try:
                value = json.loads((self.state_home / check["path"]).read_text())
                if any(not select(value, key) for key in check.get("present", [])):
                    return False
                if any(select(value, key) != expected
                       for key, expected in check.get("equals", {}).items()):
                    return False
            except (ValueError, OSError):
                return False
        argv = self.cli_login["login"].get("status_argv")
        if not argv:
            return bool(self.cli_login["login"].get("state_checks"))
        result = self.run(self.argv(argv), phase="login", timeout=30, separate_stderr=True)
        if result.returncode:
            return False
        expected = self.cli_login["login"].get("status_equals", {})
        if expected:
            try:
                value = json.loads(result.stdout)
            except ValueError:
                return False
            if any(select(value, path) != item for path, item in expected.items()):
                return False
        return all((self.state_home / path).is_file() for path in self.source["state"]["paths"])

    def accept(self):
        """Bind this explicit login to the exact source the owner connected."""
        from tinyassets.providers.subscription_lease import write_owner_text

        if sources(self.home).get(self.source.id) != self.source:
            raise PermissionError("source changed during sign-in")
        write_owner_text(self.directory / "connection", json.dumps({
            "source": digest(self.source), "incarnation": secrets.token_hex(32)}))

    def require_accepted(self):
        with self.lock():
            self._require_accepted()

    def accepted(self):
        """Lock-free: the marker is replaced atomically. False means ask
        require_accepted(), which may upgrade the marker under the lock."""
        try:
            marker = json.loads((self.directory / "connection").read_text())
            return marker["source"] == digest(self.source) and bool(marker["incarnation"])
        except (ValueError, TypeError, KeyError, OSError):
            return False

    def _require_accepted(self):
        try:
            marker = json.loads((self.directory / "connection").read_text())
            if marker["source"] != digest(self.source):
                from tinyassets.providers.subscription_lease import upgrade_connection

                upgrade_connection(self, marker)
            if marker["source"] == digest(self.source) and marker["incarnation"]:
                return
        except (ValueError, TypeError, KeyError, OSError):
            pass
        raise PermissionError("source changed or has not been accepted; reconnect required")

    def _report_failure(self, how, started, prompt_bytes, output):
        """Name where a run stopped: event types and sizes only, never content."""
        kinds = []
        for line in (output or b"").decode("utf-8", "replace").splitlines():
            try:
                kind = json.loads(line).get("type")
            except (ValueError, AttributeError):
                continue
            if isinstance(kind, str) and re.fullmatch(r"[a-z_.]{1,40}", kind):
                kinds.append(kind)
        _LOG.warning(
            "subscription run %s: source=%s seconds=%d prompt_bytes=%d output_bytes=%d "
            "events=%s", how, self.source.id, time.monotonic() - started, prompt_bytes,
            len(output or b""), ",".join(kinds[-12:]) or "none")

    def invoke(self, prompt, system="", model="", *, should_cancel=None, timeout=None):
        with self.lock():
            self.require_accepted()
            with self.lease():
                return self._invoke(prompt, system, model, should_cancel=should_cancel,
                                    timeout=timeout)

    def _invoke(self, prompt, system="", model="", *, should_cancel=None, timeout=None):
        if not self.authenticated():
            raise SubscriptionError("subscription sign-in required")
        invoke = self.source["invoke"]["cli"]
        limit = invoke.get("max_prompt_bytes", 4 * 1024 * 1024)
        if len((system + prompt).encode()) > limit:
            raise SubscriptionError("prompt exceeds the source's invocation capacity")
        argv = self.argv(invoke["argv"], prompt=prompt, system=system,
                         system_json=json.dumps(system), model=model)
        if model and model != "default":
            if not invoke.get("model_argv"):
                raise SubscriptionError("source cannot select a model")
            argv += self.argv(invoke["model_argv"], model=model)
        stdin = self.argv([invoke.get("stdin", "")], prompt=prompt, system=system, model=model)[0]
        started = time.monotonic()
        try:
            result = self.run(argv, stdin=stdin.encode(), phase="provider",
                              timeout=min(invoke.get("timeout", 600), timeout or 600),
                              separate_stderr=True, should_cancel=should_cancel)
        except OwnerProcessCancelled:
            raise
        except BoxError as exc:
            # A stall is a failed turn the owner must see, not a silent one.
            self._report_failure("ended", started, len(stdin.encode()),
                                 getattr(exc, "output", b""))
            raise SubscriptionError("subscription invocation did not finish") from exc
        if result.returncode:
            self._report_failure("exit", started, len(stdin.encode()), result.stdout)
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
        self.status = "waiting" if runtime.image_install else "installing"
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
                "display_name": self.runtime.source.display_name,
                "status": "cancelled" if self.cancelled.is_set() else self.status,
                "authorize_url": self.url,
                "code": self.code,
                "accepts_code": self.runtime.cli_login["login"].get("accepts_code", False),
            }

    def input(self):
        try:
            return self.inputs.get_nowait()
        except queue.Empty:
            return b""

    def submit(self, value):
        if (
            not self.runtime.cli_login["login"].get("accepts_code")
            or self.status != "waiting"
            or not isinstance(value, str)
            or not 0 < len(value) <= 2048
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("invalid login code handoff")
        try:
            self.inputs.put_nowait(value.encode() + b"\n")
        except queue.Full as exc:
            raise ValueError("a login code is already pending") from exc

    def observe(self, data):
        with self._lock:
            self._output = (self._output + data.decode("utf-8", errors="replace"))[-32768:]
            output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", self._output)
            login = self.runtime.cli_login["login"]
            for key, attribute in (("url_pattern", "url"), ("code_pattern", "code")):
                match = re.search(login[key], output) if login.get(key) else None
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
            with activity.pending_login(self.cancelled):
                self.runtime.install()
                with self.runtime.lease(login=True, cancelled=self.cancelled):
                    if self.cancelled.is_set():
                        raise SubscriptionError("native login cancelled")
                    if not self.runtime.authenticated():
                        self.status = "waiting"
                        login = self.runtime.cli_login["login"]
                        state_complete = False

                        def stop():
                            nonlocal state_complete
                            if login.get("complete_on_state"):
                                state_complete = self.runtime.authenticated()
                            return self.cancelled.is_set() or state_complete

                        from tinyassets.owner_process import OwnerProcessCancelled

                        try:
                            result = self.runtime.run(
                                self.runtime.argv(login["argv"]), phase="login", timeout=600,
                                input_source=self.input, output_handler=self.observe,
                                terminal=login.get("terminal", False), should_cancel=stop,
                            )
                            succeeded = result.returncode == 0
                        except OwnerProcessCancelled:
                            succeeded = state_complete and not self.cancelled.is_set()
                        if not succeeded or not self.runtime.authenticated():
                            raise SubscriptionError("native login did not complete")
                    with activity.lock:
                        if self.cancelled.is_set():
                            raise SubscriptionError("native login cancelled")
                        from tinyassets.providers.subscription_lease import commit_login

                        commit_login(self.runtime)
                        self.runtime.accept()
                        activity.pending_logins.discard(self.cancelled)
                self.status = "connected"
        except Exception:
            # CLI output can contain credentials; only safe state crosses the page boundary.
            self.status = "cancelled" if self.cancelled.is_set() else "failed"
        finally:
            self._output = ""


_logins = {}
_logins_lock = threading.Lock()


def disconnect_source(source_id):
    from tinyassets.owner_runtime import admitted
    from tinyassets.providers.subscription_lease import disconnect

    owner = admitted()
    # Keep begin outside this boundary until cancelled processes have exited and
    # the broker entry and local connection have both gone.
    with _logins_lock:
        pending = [login for login in _logins.values()
                   if login.runtime.owner is owner and login.runtime.source.id == source_id]
        for login in pending:
            login.cancelled.set()
        deadline = time.monotonic() + 5
        for login in pending:
            login.thread.join(max(0, deadline - time.monotonic()))
            if login.thread.is_alive():
                raise SubscriptionError("native login is still stopping; retry disconnect")
            _logins.pop(login.handle, None)
        disconnect(owner, source_id)
    return {"status": "disconnected", "source_id": source_id}


def login_operation(action, *, source_id=None, flow=None, code=None):
    from tinyassets.owner_runtime import admitted

    owner = admitted()
    with _logins_lock:
        for key, login in list(_logins.items()):
            if time.monotonic() >= login.expires:
                login.cancelled.set()
                if not login.thread.is_alive():
                    del _logins[key]
        if action == "begin":
            for login in _logins.values():
                if (
                    login.runtime.owner is owner
                    and login.runtime.source["id"] == source_id
                    and login.status in {"installing", "waiting"}
                    and not login.cancelled.is_set()
                ):
                    return login.view()
            if len(_logins) >= 16:
                raise SubscriptionError("too many pending logins")
            login = Login(SubscriptionRuntime(source_id)).start()
            _logins[login.handle] = login
        else:
            login = _logins.get(flow)
            if (login is None or login.runtime.owner is not owner
                    or time.monotonic() >= login.expires):
                raise PermissionError("login flow is unavailable")
            if action == "cancel":
                login.cancelled.set()
            elif action == "code":
                login.submit(code)
            elif action != "poll":
                raise ValueError("unknown login operation")
        return login.view()
