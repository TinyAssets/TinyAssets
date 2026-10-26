"""Authoring sandbox policy primitives — budgets, network decisions, effect
simulation, and per-run confirmation for real effects.

Task 4.2 of ``openspec/changes/archive/2026-08-26-complete-independent-full-platform-targets``.

**Composable primitives, not a prebuilt sandbox.** Per the host's 2026-07-25
primitives principle, this module ships the smallest irreducible decisions an
authoring test run needs — *is this destination allowed*, *did this budget fire*,
*is this effect reversible*, *is this run confirmed* — as pure functions over a
declared :class:`SandboxPolicy`. The user declares the policy in their draft
(``definition["sandbox_policy"]``) and composes the behavior; the platform owns
the invariants (deny by default, clamp to ceilings, fail closed on the unknown)
rather than a fixed menu of sandbox "modes".

**Honesty about isolation** (tasks.md 4.2 note; STATUS P1 "No OS engine
sandbox"): :func:`isolation_report` reports the boundary authoring can actually
admit; the shipped ``bwrap`` probe remains diagnostic-only. Until a real
request-bound distributed backend is admitted, it never claims OS isolation and
a draft that *declares* it needs OS isolation is refused
(:func:`require_isolation`) rather than silently run in-process.

**Real effects never bypass the canonical boundary.** This module authorizes a
run; it does not perform effects. The canonical external-effect authority and
receipt owners (``tinyassets/effectors/``,
``tinyassets/storage/external_write_receipts.py``) remain the only path to a real
external write, and this lane does not modify them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

from tinyassets.authoring.models import (
    BudgetExceeded,
    ConfirmationRequired,
    SandboxDenied,
    ValidationIssue,
    canonical_json,
)

#: Keys whose *values* never appear in a test result, a would_execute record, or
#: an event payload. Substring match, case-insensitive.
_SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "secret",
    "token",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "auth",
    "credential",
    "private_key",
    "session_id_cookie",
    "cookie",
    "bearer",
)

REDACTED = "[redacted]"

#: Effect sinks known to be reversible. Anything not declared reversible is
#: treated as irreversible — the fail-closed direction.
_KNOWN_REVERSIBLE_SINKS: frozenset[str] = frozenset({"wiki_draft", "project_memory"})

#: Sinks whose destination is a *network host*, so the draft's declared
#: ``allowed_destinations`` governs them. Connector/effector sinks (a repo, a
#: wiki path) are governed by the canonical effect-authority boundary instead —
#: conflating the two would let a network allowlist silently authorize a
#: connector write, or a connector grant silently authorize egress.
NETWORK_SINKS: frozenset[str] = frozenset(
    {"http_get", "http_post", "http_put", "http_delete", "webhook", "fetch"}
)


def is_network_sink(effect: dict[str, Any]) -> bool:
    if not isinstance(effect, dict):
        return False
    if effect.get("transport") == "network":
        return True
    return str(effect.get("sink", "")).strip().lower() in NETWORK_SINKS


_BUDGET_FIELDS: tuple[str, ...] = (
    "cpu_seconds",
    "memory_mb",
    "wall_seconds",
    "max_output_bytes",
    "model_spend_micro",
    "max_external_calls",
)
_POLICY_FIELDS: frozenset[str] = frozenset(
    _BUDGET_FIELDS
    + (
        "allowed_destinations",
        "filesystem_write",
        "requires_os_isolation",
        "effect_mode",
    )
)

#: Declaration keys that would carry secret *material* rather than a credential
#: *class*. A draft is a shared, inspectable document: the canonical vault vends
#: secrets to a declared adapter at run time, so a policy or effect declaration
#: naming one is refused outright rather than stored and redacted later. (This
#: gate covers declaration slots, not free-text prompt content, which is user
#: content the platform does not classify.)
FORBIDDEN_DECLARATION_KEYS: frozenset[str] = frozenset({
    "credential",
    "credentials",
    "secret",
    "secrets",
    "token",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "private_key",
    "bearer",
})


def forbidden_declaration_keys(declaration: dict[str, Any]) -> list[str]:
    """Return declaration keys that would carry secret material, if any."""
    if not isinstance(declaration, dict):
        return []
    return sorted(
        key for key in declaration
        if str(key).strip().lower() in FORBIDDEN_DECLARATION_KEYS
    )


@dataclass(frozen=True)
class SandboxPolicy:
    """The declared execution envelope for one authoring test run."""

    cpu_seconds: float = 10.0
    memory_mb: int = 512
    wall_seconds: float = 30.0
    max_output_bytes: int = 256 * 1024
    model_spend_micro: int = 10_000
    max_external_calls: int = 0
    allowed_destinations: tuple[str, ...] = ()
    filesystem_write: bool = False
    requires_os_isolation: bool = False
    effect_mode: str = "simulated"

    def limits(self) -> dict[str, float]:
        return {name: float(getattr(self, name)) for name in _BUDGET_FIELDS}

    def to_dict(self) -> dict[str, Any]:
        return {
            **{name: getattr(self, name) for name in _BUDGET_FIELDS},
            "allowed_destinations": list(self.allowed_destinations),
            "filesystem_write": self.filesystem_write,
            "requires_os_isolation": self.requires_os_isolation,
            "effect_mode": self.effect_mode,
        }


#: The platform ceiling *and* the default. A declaration may tighten any budget;
#: it may never exceed the ceiling, and anything undeclared stays denied.
DEFAULT_POLICY = SandboxPolicy()


def policy_from_declaration(
    declaration: dict[str, Any] | None,
) -> tuple[SandboxPolicy, list[ValidationIssue]]:
    """Build a policy from a draft's ``sandbox_policy`` declaration.

    Budgets clamp to :data:`DEFAULT_POLICY` (the ceiling) with a
    ``sandbox.budget_clamped`` issue; unknown keys are reported loudly instead of
    being ignored, so a typo in a security-relevant declaration cannot pass as
    accepted policy.
    """
    issues: list[ValidationIssue] = []
    if not declaration:
        return DEFAULT_POLICY, issues
    if not isinstance(declaration, dict):
        return DEFAULT_POLICY, [
            ValidationIssue("sandbox.malformed_policy", "sandbox_policy", "must be an object"),
        ]

    updates: dict[str, Any] = {}
    for key in forbidden_declaration_keys(declaration):
        issues.append(ValidationIssue(
            "sandbox.inline_credentials_forbidden",
            f"sandbox_policy.{key}",
            "a draft never carries secret material: declare a credential_class "
            "on the effect instead and let the canonical vault vend the secret "
            "to the adapter at run time",
        ))
    for key, value in declaration.items():
        if str(key).strip().lower() in FORBIDDEN_DECLARATION_KEYS:
            continue  # already refused above; never copied into the policy
        if key not in _POLICY_FIELDS:
            issues.append(ValidationIssue(
                "sandbox.unknown_policy_key",
                f"sandbox_policy.{key}",
                f"'{key}' is not a sandbox policy field",
            ))
            continue
        if key in _BUDGET_FIELDS:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                issues.append(ValidationIssue(
                    "sandbox.malformed_budget", f"sandbox_policy.{key}", "must be a number",
                ))
                continue
            if numeric < 0:
                issues.append(ValidationIssue(
                    "sandbox.malformed_budget", f"sandbox_policy.{key}", "must be >= 0",
                ))
                continue
            ceiling = float(getattr(DEFAULT_POLICY, key))
            if numeric > ceiling:
                issues.append(ValidationIssue(
                    "sandbox.budget_clamped",
                    f"sandbox_policy.{key}",
                    f"declared {numeric} exceeds the platform ceiling {ceiling}; clamped",
                ))
                numeric = ceiling
            updates[key] = int(numeric) if isinstance(
                getattr(DEFAULT_POLICY, key), int
            ) else numeric
        elif key == "allowed_destinations":
            if not isinstance(value, (list, tuple)):
                issues.append(ValidationIssue(
                    "sandbox.malformed_destinations",
                    "sandbox_policy.allowed_destinations",
                    "must be a list of hosts",
                ))
                continue
            updates[key] = tuple(str(item).strip().lower() for item in value if str(item).strip())
        elif key == "effect_mode":
            mode = str(value).strip()
            if mode not in ("simulated", "real"):
                issues.append(ValidationIssue(
                    "sandbox.unknown_effect_mode",
                    "sandbox_policy.effect_mode",
                    "effect_mode must be 'simulated' or 'real'",
                ))
                continue
            updates[key] = mode
        else:  # booleans
            updates[key] = bool(value)

    return replace(DEFAULT_POLICY, **updates), issues


# ── budgets ────────────────────────────────────────────────────────────────


class BudgetLedger:
    """Charge-and-check ledger; the first breach names the budget that fired."""

    def __init__(self, policy: SandboxPolicy) -> None:
        self._limits = policy.limits()
        self._spent: dict[str, float] = {name: 0.0 for name in self._limits}
        self._fired: str = ""

    def charge(self, budget: str, amount: float) -> float:
        if budget not in self._limits:
            raise KeyError(f"undeclared budget kind: {budget!r}")
        prospective = self._spent[budget] + float(amount)
        if prospective > self._limits[budget]:
            self._fired = budget
            raise BudgetExceeded(budget, self._limits[budget], prospective)
        self._spent[budget] = prospective
        return prospective

    @property
    def fired(self) -> str:
        return self._fired

    def to_dict(self) -> dict[str, Any]:
        return {
            "limits": dict(self._limits),
            "spent": dict(self._spent),
            "fired": self._fired,
        }


# ── network ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NetworkDecision:
    allowed: bool
    destination: str
    code: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "destination": self.destination,
            "code": self.code,
            "reason": self.reason,
        }


def _host_of(destination: str) -> str:
    raw = str(destination).strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        return urlsplit(raw).hostname or ""
    return raw.split("/")[0].split(":")[0]


def destination_secret_parts(destination: str) -> list[str]:
    """Name the parts of *destination* that would carry secret material.

    A destination is echoed to the user in `would_execute` records and
    confirmation prompts, so ``https://api.example/hook?token=…`` and
    ``https://user:pw@host/`` are refused at declaration time rather than
    redacted on the way out.
    """
    raw = str(destination or "").strip()
    if not raw or "://" not in raw:
        return []
    split = urlsplit(raw)
    found: list[str] = []
    if split.username or split.password:
        found.append("embedded userinfo")
    for chunk in (split.query, split.fragment):
        if not chunk:
            continue
        for pair in re.split(r"[&;]", chunk):
            key = pair.split("=", 1)[0].strip().lower()
            if key and (
                key in FORBIDDEN_DECLARATION_KEYS
                or any(pattern in key for pattern in _SECRET_KEY_PATTERNS)
            ):
                found.append(f"secret-shaped parameter '{key}'")
    return found


def display_destination(destination: str) -> str:
    """The form safe to echo back: scheme, host, port, path — nothing else.

    Declaration-time validation already refuses a secret-bearing destination;
    this is the second layer, so a value that reached the store before that gate
    existed still cannot be echoed with its query string or userinfo attached.
    """
    raw = str(destination or "").strip()
    if not raw or "://" not in raw:
        return raw
    split = urlsplit(raw)
    host = split.hostname or ""
    if split.port:
        host = f"{host}:{split.port}"
    return f"{split.scheme}://{host}{split.path}" if host else raw


#: Modules a draft node could use to reach the network. The shipped
#: ``node_sandbox`` allowlist includes ``requests``/``httpx`` for the graph
#: substrate, and the host has no egress filter (see :func:`isolation_report`),
#: so authoring cannot make "network denied except declared destinations" true
#: for such code. It therefore refuses to execute it rather than pretending.
NETWORK_CAPABLE_MODULES: frozenset[str] = frozenset({
    "requests",
    "httpx",
    "socket",
    "ssl",
    "http",
    "urllib.request",
    "ftplib",
    "smtplib",
    "telnetlib",
    "asyncio",
    "aiohttp",
    "websockets",
})

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)|import\s+([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*))",
    re.MULTILINE,
)


def network_capable_imports(source_code: str) -> list[str]:
    """Return the network-capable modules a draft node's source imports."""
    found: set[str] = set()
    for match in _IMPORT_RE.finditer(str(source_code or "")):
        raw = match.group(1) or match.group(2) or ""
        for name in (part.strip() for part in raw.split(",")):
            if not name:
                continue
            root = name.split(".")[0]
            if name in NETWORK_CAPABLE_MODULES or root in NETWORK_CAPABLE_MODULES:
                found.add(name)
    return sorted(found)


def decide_network(policy: SandboxPolicy, destination: str) -> NetworkDecision:
    """Deny by default; allow only an exact declared host (or its subdomain)."""
    host = _host_of(destination)
    if not host:
        return NetworkDecision(
            False, str(destination), "sandbox.network_denied", "no resolvable destination host",
        )
    for allowed in policy.allowed_destinations:
        if host == allowed or host.endswith(f".{allowed}"):
            return NetworkDecision(
                True, host, "sandbox.network_allowed", f"'{allowed}' is declared",
            )
    return NetworkDecision(
        False,
        host,
        "sandbox.network_denied",
        "destination is not in the draft's declared allowed_destinations",
    )


# ── isolation honesty ──────────────────────────────────────────────────────


def _probe_sandbox() -> dict[str, Any]:
    """Seam over the shipped host sandbox probe (monkeypatched in tests)."""
    try:
        from tinyassets.providers.base import get_sandbox_status

        status = get_sandbox_status()
        return {
            "box_available": bool(status.get("box_available")),
            "reason": str(status.get("reason") or ""),
        }
    except Exception as exc:  # noqa: BLE001 — an unprobed host is not an isolated host
        return {"box_available": False, "reason": f"probe failed: {exc}"}


def isolation_report() -> dict[str, Any]:
    """Report authoring's admitted isolation boundary, not host availability."""
    probe = _probe_sandbox()
    probe_reason = str(probe.get("reason") or "")
    if probe.get("box_available") and not probe_reason:
        probe_reason = "owner box provider available (diagnostic only)"
    return {
        "level": "in_process_confined",
        "os_isolated": False,
        "reason": probe_reason,
        "note": (
            "in-process confinement only: declared-destination network policy, "
            "simulated effects, and budget stops — host availability is diagnostic "
            "and no request-bound distributed backend is admitted"
        ),
    }


def require_isolation(
    policy: SandboxPolicy, report: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return diagnostics, refusing OS isolation without backend admission."""
    resolved = report if report is not None else isolation_report()
    if policy.requires_os_isolation:
        raise SandboxDenied(
            "sandbox.os_isolation_unavailable: the draft declares "
            "requires_os_isolation but authoring has no admitted request-bound "
            "distributed backend; the diagnostic reports "
            f"{resolved.get('level')} ({resolved.get('reason') or 'no reason given'})"
        )
    return resolved


# ── effects ────────────────────────────────────────────────────────────────


def classify_effect(effect: dict[str, Any]) -> str:
    """``reversible`` only when declared (or a known-reversible sink)."""
    if not isinstance(effect, dict):
        return "irreversible"
    declared = effect.get("reversible")
    if declared is True:
        return "reversible"
    if declared is False:
        return "irreversible"
    sink = str(effect.get("sink", "")).strip().lower()
    return "reversible" if sink in _KNOWN_REVERSIBLE_SINKS else "irreversible"


def _is_secret_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(pattern in lowered for pattern in _SECRET_KEY_PATTERNS)


def redact(payload: Any) -> Any:
    """Structure-preserving redaction of secret-shaped values."""
    if isinstance(payload, dict):
        return {
            key: REDACTED if _is_secret_key(key) else redact(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact(item) for item in payload]
    return payload


def _payload_summary(payload: Any, *, limit: int = 200) -> str:
    text = canonical_json(redact(payload))
    return text if len(text) <= limit else f"{text[:limit]}…"


def idempotency_key(effect: dict[str, Any], payload: Any) -> str:
    """Stable key for one (effect, payload) pair — the receipt boundary's key."""
    material = canonical_json({
        "name": effect.get("name", ""),
        "sink": effect.get("sink", ""),
        "destination": effect.get("destination", ""),
        "payload": payload,
    })
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def effect_fingerprint(
    effect: dict[str, Any], payload: Any, *, session_id: str, draft_version: int
) -> str:
    """Bind a confirmation to exactly one run of exactly one effect."""
    material = canonical_json({
        "session_id": session_id,
        "draft_version": int(draft_version),
        "idempotency_key": idempotency_key(effect, payload),
        "credential_class": effect.get("credential_class", ""),
    })
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def simulate_effect(effect: dict[str, Any], *, payload: Any = None) -> dict[str, Any]:
    """The default-mode result for a declared effect: nothing happens."""
    return {
        "simulated": True,
        "would_execute": {
            "name": effect.get("name", ""),
            "sink": effect.get("sink", ""),
            "destination": display_destination(effect.get("destination", "")),
            "effect_class": classify_effect(effect),
            "credential_class": effect.get("credential_class", "unknown"),
            "idempotency_key": idempotency_key(effect, payload),
            "payload": redact(payload) if payload is not None else {},
            "payload_summary": _payload_summary(payload),
        },
    }


def confirmation_prompt(
    effect: dict[str, Any], payload: Any, *, session_id: str, draft_version: int
) -> dict[str, Any]:
    """Everything the owner must see before confirming a real effect."""
    return {
        "session_id": session_id,
        "draft_version": int(draft_version),
        "effect": effect.get("name", ""),
        "sink": effect.get("sink", ""),
        "destination": display_destination(effect.get("destination", "")),
        "effect_class": classify_effect(effect),
        "payload_summary": _payload_summary(payload),
        "credential_class": effect.get("credential_class", "unknown"),
        "idempotency_key": idempotency_key(effect, payload),
    }


CONFIRMATION_TTL_SECONDS = 300.0


def issue_confirmation(
    store: Any,
    *,
    session_id: str,
    owner_id: str,
    draft_version: int,
    effect: dict[str, Any],
    payload: Any = None,
    ttl_seconds: float = CONFIRMATION_TTL_SECONDS,
    now: float | None = None,
) -> dict[str, Any]:
    """Mint one single-use confirmation for this run of this effect."""
    fingerprint = effect_fingerprint(
        effect, payload, session_id=session_id, draft_version=draft_version
    )
    token = store.create_confirmation(
        session_id=session_id,
        owner_id=owner_id,
        draft_version=int(draft_version),
        fingerprint=fingerprint,
        ttl_seconds=float(ttl_seconds),
        now=now,
    )
    return {
        "token": token,
        "expires_in_seconds": float(ttl_seconds),
        "confirmation": confirmation_prompt(
            effect, payload, session_id=session_id, draft_version=draft_version
        ),
    }


def authorize_real_effect(
    store: Any,
    *,
    session_id: str,
    draft_version: int,
    effect: dict[str, Any],
    payload: Any = None,
    token: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Consume a fresh confirmation, or refuse before any adapter call.

    A reversible effect still needs an authorization record, but only an
    *irreversible* one requires per-run confirmation, per the spec. An
    unconfirmed irreversible effect raises :class:`ConfirmationRequired` before
    the caller can reach the effect boundary — no partial receipt exists.
    """
    effect_class = classify_effect(effect)
    if effect_class == "reversible":
        return {
            "authorized": True,
            "effect_class": effect_class,
            "confirmation_required": False,
            "idempotency_key": idempotency_key(effect, payload),
        }
    if not token:
        raise ConfirmationRequired(
            "effect.confirmation_required: an irreversible effect needs per-run "
            f"confirmation (effect={effect.get('name', '')!r})"
        )
    fingerprint = effect_fingerprint(
        effect, payload, session_id=session_id, draft_version=draft_version
    )
    if not store.consume_confirmation(
        token,
        session_id=session_id,
        draft_version=int(draft_version),
        fingerprint=fingerprint,
        now=now,
    ):
        raise ConfirmationRequired(
            "effect.confirmation_required: confirmation is missing, expired, "
            "already used, or was issued for a different run/effect/payload"
        )
    return {
        "authorized": True,
        "effect_class": effect_class,
        "confirmation_required": True,
        "idempotency_key": idempotency_key(effect, payload),
    }
