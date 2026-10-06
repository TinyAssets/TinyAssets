"""Codex / GPT provider -- ``codex exec`` for text calls, ``codex app-server`` for agent turns.

Covered by the ChatGPT Plus subscription.  Different model family from
Claude, making it ideal as a judge when Claude is the writer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

from tinyassets import agent_sessions
from tinyassets.exceptions import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from tinyassets.providers import provider_jail
from tinyassets.providers.base import (
    BaseProvider,
    ModelConfig,
    ProviderResponse,
    check_bwrap_failure,
    get_sandbox_status,
    subprocess_env_for_provider,
)
from tinyassets.providers.owned_process import (
    aspawn_owned,
    disk_stop_note,
    kill_owned_tree,
    no_window_kwargs,
)
from tinyassets.providers.provider_jail import JailMount, UniverseView
from tinyassets.served_tools import granted_tools, model_tools

logger = logging.getLogger(__name__)


def _no_window_kwargs() -> dict:
    """Return subprocess kwargs to suppress console windows on Windows."""
    return no_window_kwargs()


def _resolve_codex_cmd() -> tuple[list[str], bool]:
    """Resolve the codex command, handling Windows .cmd/.bat wrappers.

    Returns (base_cmd, use_shell) where base_cmd is the command prefix
    and use_shell indicates whether to use shell execution.
    """
    codex_path = shutil.which("codex")
    if codex_path and sys.platform == "win32" and codex_path.lower().endswith((".cmd", ".bat")):
        return [codex_path], True
    if codex_path:
        return [codex_path], False
    return ["codex"], False


_CODEX_BIN_ASSIGNMENT = re.compile(
    r"(?m)^\s*CODEX_BIN\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|(\S+))\s*$"
)


def _resolved_codex_executable(base_cmd: list[str]) -> tuple[Path, Path]:
    """Return the invoked wrapper and the real executable it delegates to."""

    if not base_cmd:
        raise ProviderError("codex served sandbox cannot resolve an empty command")
    wrapper = Path(base_cmd[0]).expanduser()
    if not wrapper.is_absolute():
        located = shutil.which(str(wrapper))
        if not located:
            raise ProviderError("codex served sandbox cannot resolve the executable")
        wrapper = Path(located)
    try:
        wrapper = Path(os.path.abspath(wrapper))
        resolved = wrapper.resolve(strict=True)
    except OSError as exc:
        raise ProviderError(
            "codex served sandbox cannot resolve the executable"
        ) from exc
    real_executable = resolved
    if wrapper == resolved:
        try:
            with wrapper.open("rb") as stream:
                wrapper_text = stream.read(65_536).decode("utf-8", errors="strict")
        except (OSError, UnicodeError):
            wrapper_text = ""
        if "CODEX_BIN" in wrapper_text:
            match = _CODEX_BIN_ASSIGNMENT.search(wrapper_text)
            if match is None:
                raise ProviderError(
                    "codex served sandbox cannot resolve the wrapper's real binary"
                )
            try:
                raw_real_executable = next(
                    value for value in match.groups() if value is not None
                )
                real_path = Path(raw_real_executable)
                if not real_path.is_absolute():
                    raise OSError("wrapper target is not absolute")
                real_executable = real_path.resolve(strict=True)
            except OSError as exc:
                raise ProviderError(
                    "codex served sandbox cannot resolve the wrapper's real binary"
                ) from exc
    if not real_executable.is_file():
        raise ProviderError("codex served sandbox resolved binary is not a file")
    return wrapper, real_executable


def _codex_binary_tree(real_executable: Path) -> Path:
    for ancestor in real_executable.parents:
        if ancestor.name == "node_modules":
            tree = ancestor.parent
            break
    else:
        tree = real_executable.parent
    if not tree.is_dir():
        raise ProviderError("codex served sandbox cannot mount the resolved binary tree")
    return tree


_SECRET_SHAPES = re.compile(
    # Explicit secret shapes only (a generic long-token rule also hid hashes,
    # paths and model ids — the real cause). JWT fragments: any `eyJ…` run,
    # with or without the dotted tail.
    r"(sk-[A-Za-z0-9_-]{8,}|eyJ[A-Za-z0-9_.-]{10,}|"
    r"(?i:bearer\s+\S+)|(?i:(?:token|secret|api[_-]?key|password)[\"']?\s*[:=]\s*\S+))"
)


def _redacted_stderr_excerpt(stderr_text: str, limit: int = 240) -> str:
    """The last stderr line, secrets replaced, as a head+tail excerpt.

    Feeds user-visible diagnostics (router chain_state), so it must be safe
    even if codex ever echoes credential material. Head+tail (not a plain
    prefix) because codex 0.135 appends its auth error code at the END of
    the line."""
    lines = [line.strip() for line in stderr_text.strip().splitlines() if line.strip()]
    if not lines:
        return "(no stderr)"
    from tinyassets.workspace_git import scrub_text

    text = _SECRET_SHAPES.sub("[redacted]", scrub_text(lines[-1]))
    if len(text) <= limit:
        return text
    half = (limit - 5) // 2
    return text[:half] + " ... " + text[-half:]


#: Phrases in the CLI's OWN terminal error that mean the stored sign-in is
#: finished, not that the source is having a bad minute. A spent single-use
#: refresh token is the live case (founder's subscription, every turn since
#: 2026-09-24): the platform now refreshes before launch, but a launch that still
#: reaches this state must be reported as a SIGN-IN failure, not an outage.
#:
#: Why it matters which: a `ProviderUnavailableError` buys the source a 120s
#: cooldown and stops the turn, while a `ProviderAuthenticationError` marks the
#: source for reconnect and lets the router continue to the next model the owner
#: allowed (providers/router.py, `except ProviderAuthenticationError`). One is a
#: dead turn, the other is an answered one.
_TERMINAL_AUTH_PHRASES = (
    "already been used",
    "already used",
    "invalid_grant",
    "sign in again",
    "log in again",
    "not logged in",
    "please login",
    "please log in",
    "reauthenticate",
    "re-authenticate",
    "unauthorized",
)


def _terminal_auth_failure(excerpt: str) -> bool:
    """Whether the CLI's own redacted words describe a finished sign-in.

    Read from the excerpt because that is the only channel the CLI gives for this:
    it exits 1 like any other early failure, and the reason is in its message.
    Matched against a NARROW phrase list rather than any mention of "auth", so an
    ordinary failure that happens to name an auth header is not turned into a
    request for the owner to sign in again.
    """
    lower = excerpt.lower()
    return any(phrase in lower for phrase in _TERMINAL_AUTH_PHRASES)


#: Where the adapter's private home is mounted inside its jail.
_JAIL_HOME = "/codex-home"

_THREAD_ID = re.compile(r"[0-9A-Za-z][0-9A-Za-z-]{0,127}")


def _native_session_exists(store: Path, thread_id: str) -> bool:
    """Whether ``store`` still holds the rollout file for ``thread_id``."""
    if not _THREAD_ID.fullmatch(thread_id or ""):
        return False
    try:
        return agent_sessions.native_file_exists(store, f"{thread_id}.jsonl")
    except OSError:
        return False


def _codex_home_file_mounts(codex_home: Path) -> list[JailMount]:
    """A read-only bind for each regular credential file of the sealed snapshot.

    Never a ``config.toml``: ``codex app-server`` has no ``--ignore-user-config``,
    and a config could add MCP servers or tools the definition does not have.
    """
    mounts: list[JailMount] = []
    for entry in sorted(codex_home.iterdir()):
        if entry.is_symlink() or not entry.is_file() or entry.name == "config.toml":
            continue
        mounts.append(JailMount("ro-bind", f"{_JAIL_HOME}/{entry.name}", entry))
    if not mounts:
        raise ProviderError("codex served sandbox found no credential files to mount")
    return mounts


def _codex_sandbox_mounts(base_cmd: list[str]) -> tuple[Path, ...]:
    wrapper, real_executable = _resolved_codex_executable(base_cmd)
    candidates = [_codex_binary_tree(real_executable)]
    covered_roots = tuple(Path(path) for path in ("/usr", "/bin", "/lib", "/lib64"))
    if not any(wrapper.is_relative_to(root) for root in covered_roots):
        candidates.append(wrapper.parent)
    mounts: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve(strict=True)
        if resolved not in mounts:
            mounts.append(resolved)
    return tuple(mounts)


_VALID_CODEX_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh"})


def _reasoning_effort_args(effort: str | None) -> list[str]:
    """Map a generic ModelConfig.reasoning_effort to Codex's CLI override.

    Codex honors ``-c model_reasoning_effort=<minimal|low|medium|high|xhigh>``.
    Empty / unknown values yield no flag (provider default), so the knob is a
    pure opt-in and never breaks a call.
    """
    normalized = (effort or "").strip().lower()
    if normalized in _VALID_CODEX_EFFORTS:
        return ["-c", f"model_reasoning_effort={normalized}"]
    return []


def _codex_model() -> str:
    """Return an explicit operator override, or let the connected CLI choose.

    A compiled model default can become unsupported by an otherwise healthy
    subscription. An unspecified model belongs to the provider's own selection,
    not a platform-maintained catalogue. Never retry a rejected explicit choice
    with some other model.
    """
    return os.environ.get("TINYASSETS_CODEX_MODEL", "").strip()


def _codex_workdir() -> str:
    """Return the source workspace Codex should inspect for coding tasks."""
    configured = os.environ.get("TINYASSETS_CODEX_WORKDIR", "").strip()
    if configured:
        return configured
    return str(Path(__file__).resolve().parents[2])


def _terminate(proc) -> None:
    """Kill a provider subprocess tree, tolerating one that has already exited.

    Signals only the group recorded for this process at spawn, so a descendant
    the CLI started (the Windows shim's real binary, the engine-MCP server) dies
    with the turn instead of outliving it unowned. A process this adapter did
    not spawn -- a test double, an externally supplied handle -- is unmarked and
    is killed individually exactly as before, never by group.

    Killing a finished process raises ProcessLookupError on POSIX, which would
    replace the real exception (often CancelledError) with a confusing one; the
    helper suppresses that on every path.
    """
    with contextlib.suppress(ProcessLookupError, OSError):
        kill_owned_tree(proc)


#: How long a served turn may wait on ONE of its own tool calls before that wait
#: counts as idle. A 30s idle budget killed healthy 42s tool calls; "not idle
#: until the absolute cap" turned a wedged tool into an hour-long wait (Codex
#: round 2, P1). Fifteen minutes covers any run a served tool call launches
#: today and still ends a silent wedge.
_TOOL_WAIT_S = 900.0
#: Silence inside the turn is the model generating: one full model round-trip
#: can pass between events (a 31s gap was killed as idle at 30s on 2026-08-29,
#: deployed #2674). Same bound as a tool wait; no evidence supports a tighter one.
_TURN_WAIT_S = _TOOL_WAIT_S
#: asyncio's default 64 KiB stream limit raises on one long JSON line; a tool
#: result carrying a whole file can exceed it. Same bound the claude reader uses.
_STDOUT_READER_LIMIT = 32 * 1024 * 1024


class CodexProvider(BaseProvider):
    """Calls GPT via the ``codex exec`` CLI binary."""

    agent_execution_kind = "native_agent"
    #: Continues a stored native session by its thread id (``agent_sessions``).
    native_resume = True

    name = "codex"
    family = "openai"
    native_credential_service = name
    native_command_resolver = staticmethod(lambda: _resolve_codex_cmd())
    native_process_options = staticmethod(_no_window_kwargs)
    native_install_mounts = staticmethod(lambda command: _codex_sandbox_mounts(command))
    native_metadata_arguments = ("app-server",)
    from tinyassets.providers.native_jsonrpc_discovery import NativeJsonRpcProtocol

    native_discovery_protocol = NativeJsonRpcProtocol(
        list_method="model/list", items_key="data", model_key="model", default_key="isDefault",
        modalities_key="inputModalities", hidden_key="hidden", cursor_key="nextCursor",
        cursor_param="cursor", initialize_method="initialize",
        initialized_notification="initialized",
        # Effort, from the source rather than a constant here. Codex advertises
        # no boolean gate and lists OBJECTS, so support is implied by a
        # non-empty list and the level name sits inside each entry. Its
        # vocabulary also differs from Claude Code's -- a live catalogue offers
        # `ultra`, which Claude does not -- which is why the admissible set is
        # always per model and never a shared enum.
        effort_levels_key="supportedReasoningEfforts",
        effort_level_key="reasoningEffort",
        initialize_params_json='{"clientInfo":{"name":"tinyassets_model_discovery","version":"1"}}',
        list_params_json='{"limit":100,"includeHidden":true}',
    )

    @classmethod
    def is_available(cls) -> bool:
        return shutil.which("codex") is not None

    async def complete(
        self,
        prompt: str,
        system: str,
        config: ModelConfig,
        *,
        universe_dir: Path | None = None,
    ) -> ProviderResponse:
        self.require_text_only_support(config)
        if config.sandbox_workspace:
            return await self._complete_served(prompt, system, config, universe_dir)
        full_input = f"{system}\n\n{prompt}" if system else prompt

        base_cmd, use_shell = self.native_command_resolver()
        model = _codex_model() if config.native_model_id is None else config.native_model_id
        sandbox_status = get_sandbox_status()
        # Our provider jail (tinyassets.providers.provider_jail) is the sandbox
        # whenever this launch is confined. codex's OWN workspace-write sandbox
        # is a nested bubblewrap inside ours: it adds no confinement our jail
        # does not already give (the universe RW, nothing else writable, no
        # network off the egress proxy), and a nested bwrap is what forced the
        # jail's seccomp to keep user namespaces and symlinks open. So drop it
        # and let codex run its commands directly in our jail. Off the jail (a
        # host-authority call with no owning universe) codex keeps its own
        # sandbox, falling back to bypass only where bwrap is unavailable.
        if provider_jail.launch_is_confined():
            sandbox_args = ["--dangerously-bypass-approvals-and-sandbox"]
        else:
            sandbox_args = (
                ["--sandbox", "workspace-write"] if sandbox_status.get("bwrap_available")
                else ["--dangerously-bypass-approvals-and-sandbox"]
            )
        # Prompt-node calls use Codex as a subscription-backed text model, but
        # loop-investigation coding prompts still need repo source/tests mounted.
        # Prefer Codex's sandboxed auto mode when bwrap is actually usable;
        # bwrap-less hosts fall back to the hosted subscription mode already
        # used by auto-fix, with API keys stripped.
        # Per-node effort (real Codex setting, not a prompt hint): when the
        # branch node declares config.reasoning_effort, override Codex's
        # model_reasoning_effort so a light node (e.g. localize) runs minimal/
        # low and finishes fast+cheap instead of deep-reasoning a trivial task.
        effort_args = _reasoning_effort_args(
            getattr(config, "reasoning_effort", "")
        )
        proc_env = subprocess_env_for_provider(
            self.name,
            universe_dir=universe_dir,
            credential_snapshot_dir=config.credential_snapshot_dir,
        )
        from tinyassets.providers.native_model_selection import native_model_arguments

        model_args = native_model_arguments(model, "-m")
        cmd = [
            *base_cmd,
            "exec",
            *model_args,
            *effort_args,
            *sandbox_args,
            # Disable the `apps` feature (codex >= 0.135 default: stable/on) on
            # EVERY codex launch. It exposes the subscription account's
            # installed ChatGPT connectors -- including TinyAssets' OWN /mcp
            # connector -- to the model as `codex_apps` MCP tools, which
            # `--ignore-user-config` does NOT strip (they are account-side).
            # A turn relaying itself back through them was a confused-deputy
            # loop (live-diagnosed 2026-08-22). codex rejects unknown feature
            # names, so this fails closed on a rename.
            "--disable",
            "apps",
            # Remote plugin sync became default-on in CLI 0.153.4. This
            # provider consumes no account/local plugin tools or their
            # injected instructions.
            "--disable",
            "plugins",
            "--disable",
            "remote_plugin",
            "--skip-git-repo-check",
            "--ephemeral",
        ]
        # A universe's call runs in that universe (the shared jail binds
        # nothing else); only a host call keeps the source checkout.
        workdir = str(universe_dir) if universe_dir is not None else _codex_workdir()
        launch_cmd = [*cmd, "-C", workdir]
        # Spawn as an owned FAMILY: on POSIX a live anchor holds the group id
        # so teardown reaches what the CLI starts without ever naming a group
        # integer that could have been recycled. Fails closed if it cannot.
        # The shared spawn point jails every launch made for a universe; this
        # adapter only names where its own install lives (the wrapper script
        # execs a binary the generic command lookup cannot see).
        proc = await aspawn_owned(
            launch_cmd,
            shell=use_shell,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STDOUT_READER_LIMIT,
            env=proc_env,
            install_mounts=lambda: self.native_install_mounts(base_cmd),
        )

        # EVERY exit -- success, classified raise, cancellation -- ends the
        # owned family, so no descendant the CLI started outlives the call.
        try:
            start = time.monotonic()
            try:
                # Plain-text stdout, no events to reset a watchdog on: the
                # legacy total timeout. Streaming this path killed every long
                # non-served call on the 10s init budget (Codex round 2, P0).
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=full_input.encode("utf-8")),
                    timeout=config.timeout,
                )
            except asyncio.TimeoutError:
                _terminate(proc)
                await proc.wait()
                raise ProviderTimeoutError(
                    f"codex exec exceeded {config.timeout}s timeout"
                )
            except BaseException:
                # Every OTHER way out -- cancellation above all -- must end the
                # subprocess too, or the admission slot is returned while the
                # process it accounted for keeps running (cross-family review:
                # `{'slot_live': 0, 'subprocess_killed': False}`).
                _terminate(proc)
                with contextlib.suppress(Exception):
                    await proc.wait()
                raise

            elapsed_ms = (time.monotonic() - start) * 1000

            stderr_text = stderr.decode("utf-8", errors="replace")
            failure_excerpt = _redacted_stderr_excerpt(stderr_text)
            # Sandbox failures are classified FIRST: they are a host defect, not a
            # provider outage, and must surface as such instead of being folded
            # into a "likely unavailable" cooldown (how the 2026-08-21 outage hid).
            check_bwrap_failure(stderr_text)
            # A finished sign-in is classified BEFORE the exit-code heuristics: it
            # exits 1 quickly like an outage does, and reading it as one cost the
            # turn.
            if proc.returncode != 0 and _terminal_auth_failure(failure_excerpt):
                raise ProviderAuthenticationError(
                    "the stored sign-in is no longer accepted: " + failure_excerpt
                )
            # Quick exit-code-1 => provider unavailable (same heuristic as claude).
            # Carry a REDACTED excerpt of codex's own words so the real cause is
            # visible; never raw stderr (it can carry token material).
            if proc.returncode == 1 and elapsed_ms < 5000:
                raise ProviderUnavailableError(
                    "codex exec returned exit code 1 quickly -- likely unavailable: "
                    + failure_excerpt
                )
            if proc.returncode != 0:
                raise ProviderError(
                    f"codex exec exit {proc.returncode}{disk_stop_note(proc)}: "
                    f"{failure_excerpt}"
                )

            text = stdout.decode("utf-8", errors="replace").strip()
            if not text:
                # codex v0.122+ exits 0 on auth failure (401) but emits nothing to
                # stdout. Surface the silent-auth-failure pattern as a hard error
                # rather than an empty response cascading through later nodes.
                _auth_patterns = ("401", "Unauthorized", "Reconnecting", "auth")
                stderr_lower = stderr_text.lower()
                if any(p.lower() in stderr_lower for p in _auth_patterns):
                    excerpt = stderr_text[:300].strip()
                    raise ProviderError(
                        f"codex returned empty stdout with auth-error signal in stderr "
                        f"(exit={proc.returncode}): {excerpt}"
                    )
                raise ProviderError(
                    f"codex returned empty response (exit={proc.returncode}); "
                    f"stderr: {stderr_text[:200].strip() or '(empty)'}"
                )

            return ProviderResponse(
                text=text,
                provider=self.name,
                model=model or "provider-default",
                requested_model=model,
                configured_model=model,
                family=self.family,
                latency_ms=elapsed_ms,
            )
        finally:
            kill_owned_tree(proc)

    async def _complete_served(
        self, prompt: str, system: str, config: ModelConfig, universe_dir: Path | None,
    ) -> ProviderResponse:
        """A served agent turn: the one agent definition over ``codex app-server``.

        The model sees exactly the definition's tools and instructions
        (``codex_app_server``); Codex itself runs nothing, and each tool call is
        forwarded through the owner's engine route. No MCP server, bearer or
        native tool enters the jail.
        """
        from tinyassets.agent_definition import agent_definition
        from tinyassets.providers import codex_app_server as app

        base_cmd, use_shell = self.native_command_resolver()
        model = _codex_model() if config.native_model_id is None else config.native_model_id
        sandbox_status = get_sandbox_status()
        if universe_dir is None or use_shell or not sandbox_status.get("bwrap_available"):
            raise ProviderError(
                "codex served turns require the OS sandbox; refusing unconfined launch"
            )
        proc_env = subprocess_env_for_provider(
            self.name, universe_dir=universe_dir,
            credential_snapshot_dir=config.credential_snapshot_dir,
        )
        codex_home = Path(proc_env.get("CODEX_HOME", "")).resolve(strict=False)
        universe_root = universe_dir.resolve(strict=False)
        try:
            codex_home.relative_to(universe_root)
        except ValueError as exc:
            raise ProviderError("codex auth home is outside the served command center") from exc
        if not codex_home.is_dir():
            raise ProviderError(
                "codex served turns require an available OS sandbox and command center auth"
            )
        profile = config.stream_timeout_profile()
        async with contextlib.AsyncExitStack() as stack:
            tools = await _served_engine_tools(stack, config, timeout=profile.absolute_cap_s)
            definition = agent_definition(tools.tools if tools is not None else (), system)
            catalog = app.write_catalog(
                app.reduced_catalog(app.bundled_catalog(base_cmd), model or None),
                universe_root / ".runtime" / "codex-model-catalog.json",
            )
            # The stored thread carries the tools it started with, so a thread
            # resumes only under the same tool set (a narrowed grant starts fresh).
            session_model = f"{model or ''}#tools:{app.tools_digest(definition)}"
            session_ref = getattr(config, "agent_session", None)
            persist = (stack.enter_context(agent_sessions.exclusive(session_ref))
                       if session_ref is not None else False)
            resume_record: dict | None = None
            session_store: Path | None = None
            input_text = prompt
            if persist:
                session_store = agent_sessions.native_store(universe_root, self.name)
                resume_record = agent_sessions.resumable(
                    session_ref, adapter=self.name, model=session_model, prompt=prompt,
                )
                if resume_record is not None and not _native_session_exists(
                    session_store, str(resume_record["handle"]),
                ):
                    logger.warning("native session for %s is gone; starting a new one",
                                   session_ref.key)
                    resume_record = None
                if resume_record is not None:
                    # The current instructions travel as the thread's own
                    # (``thread_resume_params``), never inside the user's input.
                    input_text = session_ref.resume_prompt
            sandbox_chat = getattr(config, "sandbox_chat", False)
            # A chat turn gets an empty scratch /workspace; other served turns
            # see the universe read-only. Codex has no tool to touch either.
            workspace_mount = (
                JailMount("tmpfs", "/workspace") if sandbox_chat
                else JailMount("ro-bind", "/workspace", universe_root)
            )
            workspace_masks: tuple[JailMount, ...] = ()
            if not sandbox_chat:
                from tinyassets.providers.provider_jail import (
                    AGENT_WORKSPACE_DIR,
                    ensure_agent_workspace,
                )

                ensure_agent_workspace(universe_root)
                workspace_masks = (JailMount("tmpfs", f"/workspace/{AGENT_WORKSPACE_DIR}"),)
            universe_view = UniverseView(
                universe_dir=universe_root,
                mounts=(
                    workspace_mount,
                    *workspace_masks,
                    JailMount("tmpfs", "/workspace/.runtime/provider-launch-credentials"),
                    # CODEX_HOME is a private tmpfs with the snapshot's credential
                    # FILES bound read-only into it: the launcher takes
                    # `flock $CODEX_HOME/.lock`, so the home itself must be writable.
                    JailMount("tmpfs", _JAIL_HOME),
                    *_codex_home_file_mounts(codex_home),
                    JailMount("ro-bind", f"{_JAIL_HOME}/{_CATALOG_NAME}", catalog),
                    *((JailMount("bind", f"{_JAIL_HOME}/sessions", session_store),)
                      if session_store is not None else ()),
                ),
                chdir="/workspace",
                setenv=(("CODEX_HOME", _JAIL_HOME), ("HOME", "/tmp")),
            )
            proc_env["CODEX_HOME"] = _JAIL_HOME
            proc_env["HOME"] = "/tmp"
            # The engine route is dialled from this process; no route secret
            # belongs in the jail, whatever the daemon's environment carries.
            proc_env.pop("TINYASSETS_ENGINE_MCP_BEARER", None)
            launch_cmd = [
                *base_cmd, *app.SERVED_LAUNCH_ARGS,
                *_reasoning_effort_args(getattr(config, "reasoning_effort", "")),
                "-c", "model_catalog_json=" + json.dumps(f"{_JAIL_HOME}/{_CATALOG_NAME}"),
            ]
            proc = await aspawn_owned(
                launch_cmd, shell=False,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=_STDOUT_READER_LIMIT,
                env=proc_env, universe_view=universe_view,
                install_mounts=lambda: self.native_install_mounts(base_cmd),
            )
            stack.callback(kill_owned_tree, proc)
            stderr_chunks: list[bytes] = []

            async def _drain_stderr() -> None:
                with contextlib.suppress(Exception):
                    while chunk := await proc.stderr.read(4096):
                        stderr_chunks.append(bytes(chunk))

            drain = asyncio.create_task(_drain_stderr())
            stack.push_async_callback(_cancel_task, drain)
            start = time.monotonic()
            turn = app.AppServerTurn(proc, tools=tools, profile=profile, start=start,
                                     turn_wait=_TURN_WAIT_S, tool_wait=_TOOL_WAIT_S)
            saved = False
            try:
                thread = (
                    ("thread/resume", app.thread_resume_params(
                        definition, str(resume_record["handle"])))
                    if resume_record is not None else
                    ("thread/start", app.thread_start_params(
                        definition, model=model or None, cwd="/workspace",
                        ephemeral=not persist))
                )
                outcome = await turn.run(thread=thread, input_text=input_text, effort=None)
                elapsed_ms = (time.monotonic() - start) * 1000
                stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace")
                if outcome.status != "completed":
                    excerpt = _redacted_stderr_excerpt(
                        " ".join((outcome.error or stderr_text).splitlines()))
                    if _terminal_auth_failure(excerpt):
                        raise ProviderAuthenticationError(
                            "the stored sign-in is no longer accepted: " + excerpt)
                    raise ProviderError(f"codex turn {outcome.status or 'ended'}: {excerpt}")
                if not outcome.messages or not outcome.usage_seen:
                    raise ProviderError("codex turn omitted its result or usage")
                if persist and outcome.thread_id:
                    agent_sessions.save(session_ref, adapter=self.name, model=session_model,
                                        handle=outcome.thread_id, system=system)
                    saved = True
            except ProviderError:
                check_bwrap_failure(b"".join(stderr_chunks).decode("utf-8", errors="replace"))
                raise
            finally:
                if resume_record is not None and not saved:
                    # A resumed launch that did not finish leaves no claim that
                    # the session is healthy: the next turn starts a new one.
                    logger.warning("native session %s did not complete; next turn starts fresh",
                                   session_ref.key)
                    agent_sessions.clear(session_ref)
        from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence

        return ProviderResponse(
            text=outcome.messages[-1].strip(),
            provider=self.name,
            model=model or "provider-default",
            requested_model=model,
            configured_model=outcome.configured_model or model,
            family=self.family,
            latency_ms=elapsed_ms,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            cost_microunits=(outcome.input_tokens + outcome.output_tokens) * 100,
            # Every tool call crossed this process, but the evidence contract
            # is unchanged: a completed turn proves no absence of effects.
            native_evidence=NativeCompletionEvidence(self.name, False, True, "unknown"),
        )


#: The reduced model catalog's name inside the jail's private codex home.
_CATALOG_NAME = "model-catalog.json"


async def _cancel_task(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(task, timeout=2)


async def _served_engine_tools(stack: contextlib.AsyncExitStack, config: ModelConfig, *,
                               timeout: float):
    """The turn's granted engine tools, or ``None`` when this turn has none.

    The same owner-pinned route, session, live turn and signed grant the HTTP
    agent loop and Claude use. Requested but unreachable fails the turn: a
    served agent never silently runs without its tools.
    """
    actor_id = (getattr(config, "engine_mcp_actor_id", "") or "").strip()
    graph_id = (getattr(config, "engine_mcp_graph_id", "") or "").strip()
    enabled = model_tools(config)
    if not (getattr(config, "engine_mcp_enabled", False) and actor_id and graph_id and enabled):
        return None
    from tinyassets.engine_steering import session_of, turn_of
    from tinyassets.engine_tool_client import EngineToolError, open_engine_tools

    try:
        return await stack.enter_async_context(open_engine_tools(
            actor_id=actor_id, graph_id=graph_id, enabled_tools=enabled,
            capability_grant=granted_tools(config), timeout=timeout,
            session_key=session_of(config), turn=turn_of(),
        ))
    except EngineToolError as exc:
        raise ProviderUnavailableError(
            f"codex served turn could not reach its engine tools: {exc.code}") from None
