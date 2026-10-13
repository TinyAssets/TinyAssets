"""Version-two native delegation records, never HTTP replies or launch authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from tinyassets.providers.agent_capacity_boundary import (
    NativeCompletionEvidence,
    capacity_boundary,
)
from tinyassets.providers.diagnostics import ProviderAttemptDiagnostic
from tinyassets.providers.model_policy import ModelRef
from tinyassets.storage import agent_turn_records as records


@dataclass(frozen=True, slots=True, repr=False)
class NativeInput:
    source_ref: str
    model: str  # Empty means the provider's default, not an invented model alias.
    binding_id: str | None
    reservation_id: str
    binding_generation: int | None
    binding_digest: str | None
    request_digest: str
    authority_kind: str = "served_request"
    work_receipt_id: str = ""

    def canonical_json(self) -> str:
        for name in ("source_ref", "reservation_id"):
            records.identity(getattr(self, name))
        if type(self.model) is not str:
            raise records.invalid()
        if self.model:
            records.identity(self.model)
        work = records.work_lineage(self.authority_kind, self.work_receipt_id)
        if work and self.binding_id is None:
            if self.binding_generation is not None or self.binding_digest is not None:
                raise records.invalid()
        else:
            records.identity(self.binding_id)
            records.integer(self.binding_generation, minimum=1)
            records.digest(self.binding_digest)
        records.digest(self.request_digest)
        value = asdict(self)
        if work:
            return records.dump({"version": 3, "kind": "native_agent", **value})
        value.pop("authority_kind")
        value.pop("work_receipt_id")
        return records.dump({"version": 2, "kind": "native_agent", **value})

    @classmethod
    def from_json(cls, raw: str) -> NativeInput:
        value = records.document(raw)
        version = value.get("version")
        names = set(cls.__dataclass_fields__)
        if version == 2:
            names -= {"authority_kind", "work_receipt_id"}
        elif version != 3:
            raise records.invalid()
        records.fields(value, {"version", "kind", *names}, version=version)
        if value["kind"] != "native_agent":
            raise records.invalid()
        result = cls(**{key: value[key] for key in names})
        if result.canonical_json() != raw:
            raise records.invalid()
        return result


@dataclass(frozen=True, slots=True, repr=False)
class NativeTerminal:
    status: str  # completed | capacity_no_effects | indeterminate
    evidence: NativeCompletionEvidence | None = None
    text: str | None = None
    configured_model: str | None = None
    reported_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    failure_class: str | None = None
    capacity_scope: str | None = None
    retry_after_s: float | None = None

    def canonical_json(self, candidate: NativeInput) -> str:
        if type(candidate) is not NativeInput:
            raise records.invalid()
        candidate.canonical_json()
        if self.status not in ("completed", "capacity_no_effects", "indeterminate"):
            raise records.invalid()
        proof = self.evidence
        if proof is not None:
            if (type(proof) is not NativeCompletionEvidence
                    or proof.provider != candidate.source_ref
                    or type(proof.protocol_complete) is not bool
                    or type(proof.process_reaped) is not bool
                    or proof.side_effect_state not in ("none", "possible", "committed", "unknown")):
                raise records.invalid()
        for value in (self.input_tokens, self.output_tokens):
            if value is not None:
                records.integer(value)
        for value in (self.configured_model, self.reported_model):
            if value is not None:
                records.identity(value)
        if self.status == "completed":
            if (type(self.text) is not str or proof is None
                    or not proof.process_reaped
                    or self.configured_model is None):
                raise records.invalid()
            # A validated successful terminal can finish the turn even when
            # intermediate tool telemetry was incomplete. It never permits a
            # following step. Capacity retry still needs a complete no-effects proof.
        elif any(value is not None for value in (
            self.text, self.configured_model, self.reported_model,
            self.input_tokens, self.output_tokens,
        )):
            raise records.invalid()
        if self.status == "capacity_no_effects":
            attempt = ProviderAttemptDiagnostic(
                provider=candidate.source_ref, status="failed", skip_class="quota_or_cooldown",
                side_effect_state="none",
                failure_class=self.failure_class, capacity_scope=self.capacity_scope,
                retry_after_s=self.retry_after_s,
            )
            boundary = capacity_boundary(
                ModelRef(candidate.source_ref, candidate.model), (attempt,),
                execution_kind="native_agent", native_evidence=(proof,),
            )
            if boundary is None or not boundary.attempted:
                raise records.invalid()
        elif any(value is not None for value in (
            self.failure_class, self.capacity_scope, self.retry_after_s,
        )):
            raise records.invalid()
        return records.dump({
            "version": 2, "kind": "native_agent", "accounting": "executor_accounting",
            **asdict(self),
        })

    @classmethod
    def from_json(cls, raw: str, candidate: NativeInput) -> NativeTerminal:
        value = records.fields(
            records.document(raw),
            {"version", "kind", "accounting", *cls.__dataclass_fields__}, version=2,
        )
        if value["kind"] != "native_agent" or value["accounting"] != "executor_accounting":
            raise records.invalid()
        kwargs = {key: value[key] for key in cls.__dataclass_fields__}
        evidence = kwargs["evidence"]
        if evidence is not None:
            if (type(evidence) is not dict
                    or evidence.keys() != NativeCompletionEvidence.__dataclass_fields__.keys()):
                raise records.invalid()
            kwargs["evidence"] = NativeCompletionEvidence(**evidence)
        result = cls(**kwargs)
        if result.canonical_json(candidate) != raw:
            raise records.invalid()
        return result
