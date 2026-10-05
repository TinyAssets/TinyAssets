"""Real sheet controllers shared by the sliced-app Node harnesses.

Keep the protected approval, request sheet and connect-shape objects together:
renderRail and account cleanup now delegate to them. DOM/network collaborators
remain owned by each harness; these controllers are never no-op replacements.
"""
from __future__ import annotations


def sheet_source(html: str) -> str:
    """Load the page's controllers verbatim, failing if a boundary moves."""
    boundaries = (
        ("  const InlineApprovals = {", "  function renderRail(items, options){"),
        ("  const ConnectShapes={", "  // A declared model list needs"),
    )
    return "\n".join(html[html.index(start):html.index(end, html.index(start))]
                     for start, end in boundaries)


def rail_source(html: str) -> str:
    """The renderer plus its shared sheet/connect/cleanup dependencies."""
    from tests.test_onboarding_app import _js_function

    functions = (
        "isSetupRequest", "isOptionalRequest", "forgetFinishedSetup",
        "foldedModelAccess", "renderRail", "connectBody", "clearTypedValues",
        "updateRailItems",
    )
    return sheet_source(html) + "\n" + "\n".join(
        _js_function(html, name) for name in functions
    )
