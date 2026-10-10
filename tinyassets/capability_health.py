"""Bounded, process-local capability rates; no user data or exception text.

Record at daemon operation boundaries, after child/broker outcomes arrive. The
current five-minute window is compared with the preceding fifteen minutes. A
new deployment needs no baseline to alarm on a majority of failed operations.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import threading
import time
import uuid
from collections import Counter

from tinyassets.core_capabilities import CAPABILITIES, ERROR_CODES, failure_code

_LOG = logging.getLogger(__name__)
WINDOW = 300
HISTORY = 1200
MIN_SAMPLES = 3


class CapabilityRates:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.boot_id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.buckets: dict[int, Counter] = {}
        self.successes = Counter()

    def record(self, capability: str, code: str = "ok") -> None:
        if capability not in CAPABILITIES or (code != "ok" and code not in ERROR_CODES):
            raise ValueError("unregistered capability outcome")
        minute = int(self.clock() // 60)
        with self.lock:
            self._prune(minute)
            self.buckets.setdefault(minute, Counter())[(capability, code)] += 1
            if code == "ok":
                self.successes[capability] += 1
        if code != "ok":
            _LOG.error("capability_failure capability=%s code=%s", capability, code)

    def _prune(self, minute):
        for old in list(self.buckets):
            if old <= minute - HISTORY // 60:
                del self.buckets[old]

    def snapshot(self) -> dict:
        now = self.clock()
        minute = int(now // 60)
        recent, baseline = Counter(), Counter()
        with self.lock:
            self._prune(minute)
            successes = dict(self.successes)
            for stamp, bucket in self.buckets.items():
                (recent if stamp > minute - WINDOW // 60 else baseline).update(bucket)
        rows, alarms = [], []
        for capability in CAPABILITIES:
            samples = sum(n for (cap, code), n in recent.items() if cap == capability)
            previous = sum(n for (cap, code), n in baseline.items() if cap == capability)
            for code in sorted(ERROR_CODES):
                failures = recent[(capability, code)]
                if not failures:
                    continue
                rate = failures / samples
                base_rate = baseline[(capability, code)] / previous if previous else None
                alarm = failures >= MIN_SAMPLES and (
                    rate >= 0.5
                    or (
                        previous >= MIN_SAMPLES
                        and rate >= 0.1
                        and rate >= 3 * base_rate
                        and rate - base_rate >= 0.1
                    )
                )
                row = dict(
                    capability=capability,
                    code=code,
                    failures=failures,
                    samples=samples,
                    rate=round(rate, 4),
                    baseline_samples=previous,
                    baseline_rate=base_rate,
                    alarm=alarm,
                )
                rows.append(row)
                if alarm:
                    alarms.append(row)
        return dict(
            schema=1,
            boot_id=self.boot_id,
            pid=os.getpid(),
            observed_seconds=max(0, int(now - self.started)),
            window_seconds=WINDOW,
            history_seconds=HISTORY,
            outcomes=sum(recent.values()),
            capability_outcomes={
                cap: sum(n for (name, _), n in recent.items() if name == cap)
                for cap in CAPABILITIES
            },
            capability_successes={cap: successes.get(cap, 0) for cap in CAPABILITIES},
            failures=rows,
            alarms=alarms,
        )


RATES = CapabilityRates()
IGNORED = object()


def client_refusal(error):
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "not found",
            "no such file",
            "does not exist",
            "unknown target",
            "invalid target",
            "old_text matches",
            "is required",
            "permission denied",
            "access denied",
            "invalid_request",
            "not_authorized",
            "not authorized",
            "too large to show",
            "not utf-8",
            "byte write limit",
            "byte edit limit",
        )
    )


def result_error(result):
    """Separate expected client refusals from server failures and successes."""
    status = getattr(result, "status_code", 0)
    if 400 <= status < 500:
        return IGNORED
    if status >= 500:
        return "HTTP operation failed"
    if isinstance(result, str) and result.startswith("{"):
        try:
            result = json.loads(result)
        except ValueError:
            pass
    if isinstance(result, dict):
        status = result.get("status")
        if isinstance(status, int) and 400 <= status < 500:
            return IGNORED
        if isinstance(status, int) and status >= 500:
            return "HTTP operation failed"
        if result.get("error") or result.get("isError") or result.get("status") == "failed":
            error = result.get("error") or "operation failed"
            return IGNORED if client_refusal(error) else error
    if isinstance(result, str) and result.startswith("error:"):
        return IGNORED if client_refusal(result) else result
    return None


def record_exception(capability, exc, error_code=None):
    code = failure_code(exc)
    if type(exc).__name__ in {
        "UniverseToolError",
        "GrantResolutionError",
    } and code in {"operation_failed", "authority_refused"}:
        return
    RATES.record(capability, error_code or code)


def wake_outcome(reason: str) -> None:
    """A deferred wake is not a completed operation or a failure denominator."""
    if reason.startswith("ok:ran:"):
        RATES.record("scheduled_wake")
    elif reason.startswith(("run_failed:", "run_timeout", "error:")):
        RATES.record("scheduled_wake", failure_code(reason))


def observed(capability, *, error_code=None, inspect_result=True):
    """Observe a whole operation once; preserve its return and exception contract."""
    if capability not in CAPABILITIES:
        raise ValueError("unregistered capability")

    def decorate(function):
        def finish(result):
            error = result_error(result) if inspect_result else None
            if error is not IGNORED:
                RATES.record(capability, (error_code or failure_code(error)) if error else "ok")
            return result

        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def asynchronous(*args, **kwargs):
                try:
                    result = await function(*args, **kwargs)
                except Exception as exc:
                    record_exception(capability, exc, error_code)
                    raise
                return finish(result)

            return asynchronous

        @functools.wraps(function)
        def synchronous(*args, **kwargs):
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                record_exception(capability, exc, error_code)
                raise
            return finish(result)

        return synchronous

    return decorate
