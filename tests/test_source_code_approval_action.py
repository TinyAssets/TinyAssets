"""Regression coverage for branch source_code approval.

The run/describe surfaces told hosts to call
``extensions action=approve_source_code`` before running a branch with
source_code nodes, but no such action existed. These tests pin the promised
handler and the approval metadata that lets source edits revoke approval.
"""

from __future__ import annotations


def test_approve_source_code_requires_extensions_admin_scope_when_auth_enabled():
    from tinyassets.auth.provider import action_scope_for

    metadata = action_scope_for("extensions", "approve_source_code")
    assert metadata is not None
    assert metadata.oauth_scope == "tinyassets.extensions.admin"
    assert metadata.effect == "admin"
