"""Name the test that leaked a resource, not the test that was running when it closed.

An unclosed file, socket or sqlite connection is closed by a GC finalizer at
some LATER moment -- inside whatever test happens to be running. That is how
an fd-count assertion in one test went flaky because of a leak in another
(#4204). Python only reports it as a ResourceWarning at finalization, so a
plain `-W error::ResourceWarning` blames the wrong test.

This plugin forces the finalizers at the end of EVERY test (`gc.collect()` in
teardown) and records any ResourceWarning they raise against the test that
just finished, plus any `workspace-*sweep*` or other named background thread a
test started and left alive. Opt-in, because a collection per test costs time:

    python -m pytest -p tests.leak_probe_plugin --leak-probe-out leaks.json ...

Inert unless `--leak-probe-out` is given. Writes JSON:
`{"resource": {nodeid: [messages]}, "threads": {nodeid: [names]}}`.
"""

from __future__ import annotations

import gc
import json
import threading
import warnings
from pathlib import Path
from typing import Any

_STATE: dict[str, Any] = {}


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--leak-probe-out",
        default=None,
        metavar="PATH",
        help="record per-test ResourceWarnings and leaked threads as JSON at PATH",
    )


def pytest_configure(config: Any) -> None:
    out = config.getoption("--leak-probe-out", default=None)
    if out:
        _STATE.update(out=Path(out), resource={}, threads={}, before=set())


def pytest_runtest_setup(item: Any) -> None:
    if _STATE:
        _STATE["before"] = {t.ident for t in threading.enumerate()}


def pytest_runtest_teardown(item: Any, nextitem: Any) -> None:
    if not _STATE:
        return
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        gc.collect()
    messages = sorted(
        {str(w.message)[:200] for w in caught if issubclass(w.category, ResourceWarning)}
    )
    if messages:
        _STATE["resource"][item.nodeid] = messages


def pytest_runtest_logfinish(nodeid: str, location: Any) -> None:
    if not _STATE:
        return
    leaked = sorted(
        t.name
        for t in threading.enumerate()
        if t.is_alive() and t.ident not in _STATE["before"] and t is not threading.current_thread()
    )
    if leaked:
        _STATE["threads"][nodeid] = leaked


def pytest_unconfigure(config: Any) -> None:
    if _STATE:
        _STATE["out"].write_text(
            json.dumps({"resource": _STATE["resource"], "threads": _STATE["threads"]}, indent=1),
            encoding="utf-8",
        )
