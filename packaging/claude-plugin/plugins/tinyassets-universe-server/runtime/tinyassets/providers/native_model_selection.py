"""Native model requests without HTTP-shaped pricing or fabricated telemetry.

These immutable facts are not grants. The serving/work boundary validates the
current accepted member and its owned custody before issuing them to a router.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from tinyassets.provider_assignment_manifest import ModelAccess


def validate_model_id(value):
    if (type(value) is not str or len(value) > 200 or value != value.strip()
            or value and not value.isprintable()):
        raise ValueError("invalid native model identifier")
    return value


@dataclass(frozen=True, slots=True)
class NativeSelection:
    provider: str
    requested_model_id: str
    default_model_id: str = ""
    basis: str = "owner_declared"
    observed_at: str = ""
    completed_at: str = ""
    source_digest: str = ""

    def __post_init__(self):
        if type(self.provider) is not str or not self.provider:
            raise ValueError("invalid native source")
        validate_model_id(self.requested_model_id)
        validate_model_id(self.default_model_id)
        if not self.requested_model_id:
            raise ValueError("native selection requires a requested model")
        if self.basis == "owner_declared":
            if any((self.default_model_id, self.observed_at,
                    self.completed_at, self.source_digest)):
                raise ValueError("owner declaration cannot attest discovery facts")
        elif self.basis == "executor_enumerated":
            observed, completed = self.discovery_times()
            if (observed > completed or type(self.source_digest) is not str
                    or not self.source_digest):
                raise ValueError("invalid native discovery facts")
        else:
            raise ValueError("unknown native selection basis")

    def discovery_times(self):
        values = []
        for value in (self.observed_at, self.completed_at):
            if type(value) is not str:
                raise ValueError("invalid native discovery time")
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("native discovery time must include timezone")
            values.append(parsed)
        return values

    def assert_access(self, access, source_digest):
        """Revalidate sealed facts against current scope/custody, not caller preference."""
        if type(access) is not ModelAccess:
            raise PermissionError("native model access is missing")
        if self.basis == "owner_declared":
            allowed = (access.model_scope == "explicit"
                       and self.requested_model_id in access.model_ids)
        else:
            observed, completed = self.discovery_times()
            now = datetime.now(timezone.utc)
            allowed = (access.model_scope == "discovered" and source_digest == self.source_digest
                       and completed <= now and now - observed <= timedelta(minutes=5))
        if not allowed:
            raise PermissionError("native selection is outside current model authority")

    def to_dict(self):
        value = {"kind": "native", "version": 1, "provider": self.provider,
                "requested_model_id": self.requested_model_id,
                "default_model_id": self.default_model_id, "basis": self.basis}
        if self.basis == "executor_enumerated":
            value.update(version=2, observed_at=self.observed_at,
                         completed_at=self.completed_at, source_digest=self.source_digest)
        return value

    @classmethod
    def from_dict(cls, value):
        fields = {
            "kind", "version", "provider", "requested_model_id", "default_model_id", "basis",
        }
        if type(value) is not dict:
            raise ValueError("native selection fields do not match schema")
        version = value.get("version")
        if type(version) is not int or version not in (1, 2):
            raise ValueError("unknown native selection version")
        if version == 2:
            fields |= {"observed_at", "completed_at", "source_digest"}
        if (set(value) != fields or value["kind"] != "native"
                or value["basis"] != ("owner_declared" if version == 1 else "executor_enumerated")):
            raise ValueError("native selection fields do not match schema")
        return cls(value["provider"], value["requested_model_id"],
                   value["default_model_id"], value["basis"], value.get("observed_at", ""),
                   value.get("completed_at", ""), value.get("source_digest", ""))


def accepted_native_selection(provider, model_id, access):
    """Return explicit declared native facts, or None for other/default paths."""
    from tinyassets.provider_serving_binding import _PROVIDER_SERVICE

    if provider not in _PROVIDER_SERVICE or model_id == "":
        return None
    validate_model_id(model_id)
    if type(access) is ModelAccess and access.model_scope == "discovered":
        return None  # Must continue through fresh account enumeration.
    if (type(access) is not ModelAccess or access.model_scope != "explicit"
            or model_id not in access.model_ids):
        raise PermissionError("native model requires accepted explicit scope or fresh discovery")
    return NativeSelection(provider, model_id)


def native_model_arguments(model_id, flag):
    """Argument vector only; no shell interpolation and no fallback added HERE.

    "No implicit fallback" is a statement about this function: it passes the
    accepted string through and never substitutes a second model. It is NOT a
    guarantee that the CLI answers on exactly that model.

    An ALIAS (``opus``, ``sonnet``) is a tier, not a model. From Claude Code
    2.1.286, when the API refuses the model an alias or default resolved to, the
    CLI retries the previous model in that tier -- so a refusal can come back as
    a successful answer from a neighbouring model, and ``--model sonnet`` names
    the tier it will be served from rather than one concrete model. The release
    note does not extend this to explicit model IDs; pass a full ID when a turn
    needs one exact model.

    This is safe here because nothing downstream infers the answering model from
    the request: ``claude_provider._AnswerModelEvidence`` reads the model off the
    assistant message in the stream, and that observed value is what
    ``ProviderResponse.reported_model`` carries. Requested and reported are
    separate fields on purpose and are allowed to differ.
    """
    if model_id is None:
        return []
    validate_model_id(model_id)
    return [flag, model_id] if model_id else []
