from __future__ import annotations

import pytest

from tinyassets.execution_subject import (
    ExecutionSubject,
    ExecutionSubjectKind,
)


def test_execution_subject_requires_typed_kind_reference_and_digest() -> None:
    subject = ExecutionSubject(
        kind=ExecutionSubjectKind.BRANCH_VERSION,
        ref="branch-example@abc12345",
        digest=f"sha256:{'a' * 64}",
    )

    assert subject.kind is ExecutionSubjectKind.BRANCH_VERSION
    assert subject.to_dict() == {
        "kind": "branch_version",
        "ref": "branch-example@abc12345",
        "digest": f"sha256:{'a' * 64}",
    }
    assert ExecutionSubject.from_dict(subject.to_dict()) == subject


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("kind", "branch_version"),
        ("ref", ""),
        ("digest", f"sha256:{'A' * 64}"),
    ),
)
def test_execution_subject_rejects_untyped_or_noncanonical_fields(
    field: str,
    value: object,
) -> None:
    values: dict[str, object] = {
        "kind": ExecutionSubjectKind.BRANCH_VERSION,
        "ref": "branch-example@abc12345",
        "digest": f"sha256:{'a' * 64}",
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        ExecutionSubject(**values)  # type: ignore[arg-type]


