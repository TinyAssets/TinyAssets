"""Fake the provider CLI launch at its explicit owned-process transport seam.

The Claude and Codex adapters no longer reach ``asyncio.create_subprocess_exec``
or ``..._shell`` directly: every CLI is launched through
``tinyassets.providers.owned_process.aspawn_owned``. On POSIX that helper runs a
real wrapper/anchor handshake -- it execs a wrapper, waits for a status line on
a pipe, and parses the leader pid -- and **fails closed** with
``FamilyAnchorError`` when the handshake does not complete. A stand-in process
object cannot answer that handshake (no ``pid``, no wrapper, no status line), so
patching the ``asyncio`` spawn functions under it now produces anchor failures
rather than the adapter behaviour the test is about.

Tests that assert on *what the adapter builds and does with the stream* -- argv,
environment, cwd, auth classification, streaming, timeout/cancel teardown,
model selection, sandbox flags -- patch this seam instead. The argv is recorded
flattened (``recorder(*cmd, **kwargs)``), so ``call_args.args`` reads exactly as
it did against ``create_subprocess_exec(*cmd, **kwargs)``.

**This helper does not, and cannot, test ownership.** A fake transport proves
nothing about whether a real process family is ended: it never spawns one. Real
ownership stays proven by the real-process suites
(``tests/test_provider_real_adapter_deadline_reap.py`` and
``tests/test_provider_node_timeout_subprocess_reap.py``), which run the genuine
``aspawn_owned`` against genuine children and are deliberately untouched by
this module. Nothing here is autouse or global: each use is an explicit
context manager inside one test.
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

#: The adapter modules that import ``aspawn_owned`` into their own namespace.
PROVIDER_MODULES = (
    "tinyassets.providers.claude_provider",
)


def _raise(side_effect):
    if isinstance(side_effect, type) and issubclass(side_effect, BaseException):
        raise side_effect()
    if isinstance(side_effect, BaseException):
        raise side_effect
    return False


def _recording_spawn(return_value, side_effect):
    """A fake ``aspawn_owned`` plus the mock that records its flattened argv."""
    recorder = MagicMock(name="aspawn_owned")

    async def _spawn(cmd, *, shell=False, **kwargs):
        argv = list(cmd)
        recorder(*argv, shell=shell, **kwargs)
        if side_effect is not None:
            _raise(side_effect)
            return side_effect(argv, shell=shell, **kwargs)
        return return_value

    return _spawn, recorder


@contextlib.contextmanager
def fake_owned_spawn(*modules, return_value=None, side_effect=None):
    """Patch ``aspawn_owned`` in ``modules`` (both adapters by default).

    ``return_value`` is the stand-in process handed back to the adapter.
    ``side_effect`` may be an exception (class or instance) to raise instead --
    the "observe the argv and stop" pattern -- or a callable
    ``(cmd, shell=..., **kwargs) -> proc``.

    Yields the recording ``MagicMock``: ``spawn.call_args.args`` is the argv the
    adapter built, ``spawn.call_args.kwargs`` its ``env``/``cwd``/``limit``/
    ``shell`` and the stream pipes.
    """
    spawn, recorder = _recording_spawn(return_value, side_effect)
    with contextlib.ExitStack() as stack:
        for module in modules or PROVIDER_MODULES:
            stack.enter_context(patch(f"{module}.aspawn_owned", new=spawn))
        yield recorder


def install_fake_owned_spawn(monkeypatch, *modules, return_value=None, side_effect=None):
    """``monkeypatch`` flavour of :func:`fake_owned_spawn`; returns the recorder.

    For tests already built around ``monkeypatch.setattr`` rather than a ``with``
    block. Same seam, same recording contract, same undo-at-teardown scope.
    """
    spawn, recorder = _recording_spawn(return_value, side_effect)
    for module in modules or PROVIDER_MODULES:
        monkeypatch.setattr(f"{module}.aspawn_owned", spawn)
    return recorder
