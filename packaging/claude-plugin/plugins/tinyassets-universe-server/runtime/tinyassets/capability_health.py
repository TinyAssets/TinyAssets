"""Bounded, process-local capability rates; no user data or exception text.

Record at daemon operation boundaries, after child/broker outcomes arrive. The
current five-minute window is compared with the preceding fifteen minutes. A
new deployment needs no baseline to alarm on a majority of failed operations.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import os
import secrets
import threading
import time
import uuid
from collections import Counter

from tinyassets.core_capabilities import CAPABILITIES, ERROR_CODES, failure_code

_LOG = logging.getLogger(__name__)
WINDOW = 300
HISTORY = 1200
MIN_SAMPLES = 3
MAX_OWNER_BUCKETS = 4096


class CapabilityRates:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.boot_id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.buckets: dict[int, Counter] = {}
        self.successes = Counter()
        self.owner_salt = secrets.token_bytes(32)

    def record(self, capability: str, code: str = "ok", *, owner: str | None = None) -> None:
        if capability not in CAPABILITIES or (code != "ok" and code not in ERROR_CODES):
            raise ValueError("unregistered capability outcome")
        if owner is None:
            from tinyassets.auth.middleware import current_identity_or_none

            identity = current_identity_or_none()
            owner = identity.user_id if identity else ""
        # Process-salted identifiers are internal only and expire with the bucket.
        owner_key = (
            hashlib.blake2b(owner.encode(), key=self.owner_salt, digest_size=16).hexdigest()
            if owner
            else ""
        )
        minute = int(self.clock() // 60)
        with self.lock:
            self._prune(minute)
            bucket = self.buckets.setdefault(minute, Counter())
            key = (capability, code, owner_key)
            if key not in bucket and len(bucket) >= MAX_OWNER_BUCKETS:
                key = (capability, code, "")  # overflow cannot invent distinct owners
            bucket[key] += 1
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
        owner_counts = recent
        recent = Counter()
        for (cap, code, _), count in owner_counts.items():
            recent[cap, code] += count
        collapsed = Counter()
        for (cap, code, _), count in baseline.items():
            collapsed[cap, code] += count
        baseline = collapsed
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
                failing_owners = sum(
                    1
                    for (cap, error, owner), count in owner_counts.items()
                    if cap == capability and error == code and owner and count >= MIN_SAMPLES
                )
                alarm = (
                    failing_owners >= 2
                    and failures >= MIN_SAMPLES
                    and (
                        rate >= 0.5
                        or (
                            previous >= MIN_SAMPLES
                            and rate >= 0.1
                            and rate >= 3 * base_rate
                            and rate - base_rate >= 0.1
                        )
                    )
                )
                row = dict(
                    capability=capability,
                    code=code,
                    failures=failures,
                    samples=samples,
                    failing_owners=failing_owners,
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


def record_exception(capability, exc, error_code=None, *, owner=None):
    code = failure_code(exc)
    if type(exc).__name__ in {
        "UniverseToolError",
        "GrantResolutionError",
    } and code in {"operation_failed", "authority_refused"}:
        return
    RATES.record(capability, error_code or code, owner=owner)


def wake_outcome(reason: str) -> None:
    """A deferred wake is not a completed operation or a failure denominator."""
    if reason.startswith("ok:ran:"):
        RATES.record("scheduled_wake")
    elif reason.startswith(("run_failed:", "run_timeout", "error:")):
        RATES.record("scheduled_wake", failure_code(reason))


def observed(capability, *, error_code=None, inspect_result=True, owner=None):
    """Observe a whole operation once; preserve its return and exception contract."""
    if capability not in CAPABILITIES:
        raise ValueError("unregistered capability")

    def decorate(function):
        def finish(result, principal):
            error = result_error(result) if inspect_result else None
            if error is not IGNORED:
                RATES.record(
                    capability,
                    (error_code or failure_code(error)) if error else "ok",
                    owner=principal,
                )
            return result

        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def asynchronous(*args, **kwargs):
                principal = owner(*args, **kwargs) if owner else None
                try:
                    result = await function(*args, **kwargs)
                except Exception as exc:
                    record_exception(capability, exc, error_code, owner=principal)
                    raise
                return finish(result, principal)

            return asynchronous

        @functools.wraps(function)
        def synchronous(*args, **kwargs):
            principal = owner(*args, **kwargs) if owner else None
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                record_exception(capability, exc, error_code, owner=principal)
                raise
            return finish(result, principal)

        return synchronous

    return decorate
